"""Integration tests for Phase 3 administrator operations."""

from datetime import datetime, timezone

from bot.repositories.audit_repository import AuditRepository
from bot.repositories.evaluation_repository import EvaluationRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.repositories.attendance_repository import AttendanceRepository
from bot.services.attendance_service import AttendanceService
from bot.services.admin_service import AdminService, SettingsUpdateStatus
from bot.services.backup_service import BackupService
from bot.services.evaluation_service import (
    EvaluationService,
    EvaluationStatus,
    ManualScoreStatus,
)
from bot.services.session_service import SessionService
from bot.services.streak_service import StreakService


GUILD_ID = "111"
ADMIN_ID = "9001"


def utc_dt(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


async def create_member(member_repository, discord_id: str, name: str) -> int:
    return await member_repository.create(
        guild_id=GUILD_ID,
        discord_id=discord_id,
        display_name=name,
        created_by_discord_id=ADMIN_ID,
        now="2026-07-01T00:00:00+00:00",
    )


def build_services(database, guild_repository, member_repository):
    score_repository = ScoreRepository(database=database)
    audit_repository = AuditRepository(database=database)
    evaluation_repository = EvaluationRepository(database=database)
    evaluation_service = EvaluationService(
        member_repository=member_repository,
        score_repository=score_repository,
        evaluation_repository=evaluation_repository,
        audit_repository=audit_repository,
    )
    admin_service = AdminService(
        guild_repository=guild_repository,
        session_repository=SessionRepository(database=database),
        score_repository=score_repository,
        audit_repository=audit_repository,
    )
    return score_repository, evaluation_service, admin_service


async def count_rows(database, table: str) -> int:
    connection = await database.connect()
    try:
        row = (
            await connection.execute_fetchall(
                f"SELECT COUNT(*) AS count FROM {table};"
            )
        )[0]
        return int(row["count"])
    finally:
        await connection.close()


async def test_evaluation_cancel_and_manual_adjustment_are_ledger_events(
    database,
    guild_repository,
    member_repository,
):
    member_id = await create_member(member_repository, "2001", "A")
    score_repository, evaluation_service, _ = build_services(
        database,
        guild_repository,
        member_repository,
    )

    created = await evaluation_service.create_evaluation(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        evaluator_discord_id=ADMIN_ID,
        score=3,
        reason="good attendance",
        has_permission=True,
        now=utc_dt(2, 1, 0),
    )
    cancelled = await evaluation_service.cancel_evaluation(
        guild_id=GUILD_ID,
        evaluation_id=created.evaluation_id,
        actor_discord_id=ADMIN_ID,
        cancellation_reason="duplicate review",
        has_permission=True,
        now=utc_dt(2, 1, 5),
    )
    adjusted = await evaluation_service.adjust_score(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        actor_discord_id=ADMIN_ID,
        delta=-10,
        reason="manual operation correction",
        has_permission=True,
        now=utc_dt(2, 1, 10),
    )

    assert created.status is EvaluationStatus.CREATED
    assert created.total_score == 3
    assert cancelled.status is EvaluationStatus.CANCELLED
    assert cancelled.total_score == 0
    assert adjusted.status is ManualScoreStatus.ADJUSTED
    assert await score_repository.get_total_score(member_id=member_id) == -10
    assert await count_rows(database, "evaluations") == 1
    assert await count_rows(database, "score_events") == 3
    assert await count_rows(database, "audit_logs") == 3


async def test_evaluation_validation_rejects_self_zero_and_inactive_target(
    database,
    guild_repository,
    member_repository,
):
    await create_member(member_repository, "2001", "A")
    await member_repository.deactivate(
        guild_id=GUILD_ID,
        discord_id="2001",
        display_name="A",
        now="2026-07-02T00:00:00+00:00",
    )
    _, evaluation_service, _ = build_services(
        database,
        guild_repository,
        member_repository,
    )

    inactive = await evaluation_service.create_evaluation(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        evaluator_discord_id=ADMIN_ID,
        score=1,
        reason="test",
        has_permission=True,
        now=utc_dt(2, 1, 0),
    )
    zero = await evaluation_service.create_evaluation(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        evaluator_discord_id=ADMIN_ID,
        score=0,
        reason="test",
        has_permission=True,
        now=utc_dt(2, 1, 0),
    )
    denied = await evaluation_service.adjust_score(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        actor_discord_id=ADMIN_ID,
        delta=1,
        reason="test",
        has_permission=False,
        now=utc_dt(2, 1, 0),
    )

    assert inactive.status is EvaluationStatus.TARGET_NOT_ACTIVE
    assert zero.status is EvaluationStatus.INVALID_SCORE
    assert denied.status is ManualScoreStatus.PERMISSION_DENIED


async def test_setting_update_writes_audit_log(database, guild_repository, member_repository):
    _, _, admin_service = build_services(database, guild_repository, member_repository)

    result = await admin_service.update_setting(
        guild_id=GUILD_ID,
        field="attendance_days",
        value="mon,wed,mon",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(2, 1, 0),
    )
    settings = await guild_repository.get_by_guild_id(GUILD_ID)

    assert result.status is SettingsUpdateStatus.UPDATED
    assert settings["attendance_days"] == "MON,WED"
    assert await count_rows(database, "audit_logs") == 1


async def test_today_session_cancel_and_resume_compensates_scores(
    database,
    guild_repository,
    member_repository,
):
    member_id = await create_member(member_repository, "2001", "A")
    score_repository = ScoreRepository(database=database)
    session_repository = SessionRepository(database=database)
    attendance_repository = AttendanceRepository(database=database)
    audit_repository = AuditRepository(database=database)
    session_service = SessionService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
    )
    attendance_service = AttendanceService(
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        session_service=session_service,
        streak_service=StreakService(score_repository=score_repository),
    )
    admin_service = AdminService(
        guild_repository=guild_repository,
        session_repository=session_repository,
        score_repository=score_repository,
        audit_repository=audit_repository,
    )

    connection = await database.connect()
    try:
        await connection.execute(
            """
            UPDATE guild_settings
            SET
                attendance_days = 'MON,TUE,WED,THU,FRI,SAT,SUN',
                attendance_start = '10:00',
                late_deadline = '10:10',
                close_deadline = '10:20',
                timezone = 'UTC'
            WHERE guild_id = ?;
            """,
            (GUILD_ID,),
        )
        await connection.commit()
    finally:
        await connection.close()

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(2, 10, 0),
    )
    cancelled = await admin_service.cancel_today_session(
        guild_id=GUILD_ID,
        reason="day off",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(2, 10, 5),
    )
    resumed = await admin_service.resume_today_session(
        guild_id=GUILD_ID,
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(2, 10, 6),
    )

    assert await score_repository.get_total_score(member_id=member_id) == 3
    assert cancelled.score_event_count == 1
    assert resumed.score_event_count == 1
    assert await count_rows(database, "audit_logs") == 2


async def test_backup_service_creates_integrity_checked_copy(database, tmp_path):
    backup_service = BackupService(
        database=database,
        backup_directory=tmp_path / "backups",
        retention_count=2,
    )

    first = await backup_service.create_backup(now=utc_dt(2, 1, 0))
    second = await backup_service.create_backup(now=utc_dt(3, 1, 0))
    third = await backup_service.create_backup(now=utc_dt(4, 1, 0))

    backups = sorted((tmp_path / "backups").glob("attendance-*.db"))
    assert first.created
    assert second.created
    assert third.created
    assert third.integrity_ok
    assert len(backups) == 2
    assert third.backup_path.exists()
