"""Integration tests for Phase 1 closing, recovery, reports, and corrections."""

from datetime import datetime, timezone

import pytest

from bot.repositories.audit_repository import AuditRepository
from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.report_repository import ReportRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.scheduler.attendance_loop import AttendanceScheduler
from bot.services.attendance_service import (
    AttendanceCorrectionStatus,
    AttendanceService,
)
from bot.services.report_service import ReportService
from bot.services.session_service import SessionCloseStatus, SessionService


GUILD_ID = "111"
ADMIN_ID = "9001"


def utc_dt(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, second, tzinfo=timezone.utc)


async def configure_daily(database):
    connection = await database.connect()
    try:
        await connection.execute(
            """
            UPDATE guild_settings
            SET
                attendance_days = 'MON,TUE,WED,THU,FRI,SAT,SUN',
                attendance_start = '21:30',
                late_deadline = '21:40',
                close_deadline = '21:45',
                timezone = 'Asia/Seoul'
            WHERE guild_id = ?;
            """,
            (GUILD_ID,),
        )
        await connection.commit()
    finally:
        await connection.close()


async def create_member(member_repository, discord_id: str, name: str) -> int:
    return await member_repository.create(
        guild_id=GUILD_ID,
        discord_id=discord_id,
        display_name=name,
        created_by_discord_id=ADMIN_ID,
        now="2026-07-01T00:00:00+00:00",
    )


def build_all(database, guild_repository, member_repository, guild_service=None):
    session_repository = SessionRepository(database=database)
    attendance_repository = AttendanceRepository(database=database)
    score_repository = ScoreRepository(database=database)
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
        guild_repository=guild_repository,
        audit_repository=audit_repository,
    )
    report_service = ReportService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        report_repository=ReportRepository(database=database),
        score_repository=score_repository,
    )
    scheduler = None
    if guild_service is not None:
        scheduler = AttendanceScheduler(
            guild_service=guild_service,
            session_service=session_service,
        )
    return session_service, attendance_service, report_service, scheduler


class FakeGuildService:
    """Tiny scheduler test double that exposes configured guild settings."""

    def __init__(self, guild_repository):
        self.guild_repository = guild_repository

    async def list_all_settings(self):
        return await self.guild_repository.list_all_settings()


async def count_rows(database, table: str) -> int:
    connection = await database.connect()
    try:
        cursor = await connection.execute(f"SELECT COUNT(*) AS count FROM {table};")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["count"])
    finally:
        await connection.close()


async def get_session(database):
    connection = await database.connect()
    try:
        rows = await connection.execute_fetchall(
            "SELECT * FROM attendance_sessions ORDER BY id;"
        )
        return [dict(row) for row in rows]
    finally:
        await connection.close()


async def test_scheduler_auto_creates_opens_and_skips_duplicate(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    await create_member(member_repository, "2001", "A")
    await create_member(member_repository, "2002", "B")
    inactive_id = await create_member(member_repository, "2003", "C")
    await member_repository.deactivate(
        guild_id=GUILD_ID,
        discord_id="2003",
        display_name="C",
        now="2026-07-02T00:00:00+00:00",
    )
    _, _, _, scheduler = build_all(
        database,
        guild_repository,
        member_repository,
        FakeGuildService(guild_repository),
    )

    await scheduler.run_once(utc_dt(2, 12, 0))
    await scheduler.run_once(utc_dt(2, 12, 0))
    sessions = await get_session(database)
    assert len(sessions) == 1
    assert sessions[0]["status"] == "SCHEDULED"

    await scheduler.run_once(utc_dt(2, 12, 30))
    sessions = await get_session(database)
    opened_at = sessions[0]["opened_at"]
    assert sessions[0]["status"] == "OPEN"

    await scheduler.run_once(utc_dt(2, 12, 31))
    sessions = await get_session(database)
    assert sessions[0]["opened_at"] == opened_at

    connection = await database.connect()
    try:
        rows = await connection.execute_fetchall(
            "SELECT member_id FROM attendance_session_members;"
        )
    finally:
        await connection.close()
    assert inactive_id not in {row["member_id"] for row in rows}


async def test_auto_close_creates_absent_scores_and_is_idempotent(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    await create_member(member_repository, "2001", "A")
    await create_member(member_repository, "2002", "B")
    await create_member(member_repository, "2003", "C")
    await create_member(member_repository, "2004", "D")
    session_service, attendance_service, _, _ = build_all(
        database,
        guild_repository,
        member_repository,
    )

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(2, 12, 30),
    )
    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2002",
        now=utc_dt(2, 12, 40),
    )

    sessions = await get_session(database)
    result = await session_service.close_session(
        session_id=sessions[0]["id"],
        now=utc_dt(2, 12, 46),
    )
    assert result.status is SessionCloseStatus.CLOSED
    assert result.newly_absent_count == 2
    assert await count_rows(database, "attendance_records") == 4
    assert await count_rows(database, "score_events") == 4

    closed_at = (await get_session(database))[0]["closed_at"]
    second = await session_service.close_session(
        session_id=sessions[0]["id"],
        now=utc_dt(2, 12, 50),
    )
    assert second.status is SessionCloseStatus.ALREADY_CLOSED
    assert await count_rows(database, "attendance_records") == 4
    assert await count_rows(database, "score_events") == 4
    assert (await get_session(database))[0]["closed_at"] == closed_at


async def test_recovery_closes_overdue_open_session_once(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    await create_member(member_repository, "2001", "A")
    await create_member(member_repository, "2002", "B")
    session_service, _, _, _ = build_all(database, guild_repository, member_repository)
    prepared = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(2, 12, 30),
    )

    recovery = await session_service.process_overdue_sessions(now=utc_dt(2, 12, 46))
    again = await session_service.process_overdue_sessions(now=utc_dt(2, 12, 47))
    session = (await get_session(database))[0]

    assert prepared.session["status"] == "OPEN"
    assert recovery.processed_sessions == 1
    assert recovery.newly_absent_count == 2
    assert again.processed_sessions == 0
    assert session["status"] == "CLOSED"


async def test_personal_report_summary_and_zero_rate(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    await create_member(member_repository, "2001", "A")
    await create_member(member_repository, "2002", "B")
    session_service, attendance_service, report_service, _ = build_all(
        database,
        guild_repository,
        member_repository,
    )

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(2, 12, 30),
    )
    await session_service.close_session(session_id=(await get_session(database))[0]["id"], now=utc_dt(2, 12, 46))
    report = await report_service.get_my_report(guild_id=GUILD_ID, discord_id="2001")
    zero = await report_service.get_my_report(guild_id=GUILD_ID, discord_id="2002")

    assert report.found
    assert report.total_sessions == 1
    assert report.present_count == 1
    assert report.attendance_rate == 100.0
    assert report.total_score == 3
    assert report.rank == "먼지 이병"
    assert len(report.recent_events) == 1
    assert zero.total_sessions == 1
    assert zero.absent_count == 1
    assert zero.attendance_rate == 0.0


async def test_attendance_correction_updates_score_and_audit(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    await create_member(member_repository, "2001", "A")
    session_service, _, _, _ = build_all(database, guild_repository, member_repository)
    attendance_repository = AttendanceRepository(database=database)
    score_repository = ScoreRepository(database=database)
    audit_repository = AuditRepository(database=database)
    attendance_service = AttendanceService(
        member_repository=member_repository,
        session_repository=SessionRepository(database=database),
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        session_service=session_service,
        guild_repository=guild_repository,
        audit_repository=audit_repository,
    )
    prepared = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(2, 12, 30),
    )
    await session_service.close_session(session_id=prepared.session["id"], now=utc_dt(2, 12, 46))

    result = await attendance_service.correct_attendance(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        new_status="PRESENT",
        reason="입력 실수",
        actor_discord_id=ADMIN_ID,
        now=utc_dt(2, 13, 0),
    )
    same = await attendance_service.correct_attendance(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        new_status="PRESENT",
        reason="입력 실수",
        actor_discord_id=ADMIN_ID,
        now=utc_dt(2, 13, 1),
    )

    assert result.status is AttendanceCorrectionStatus.UPDATED
    assert result.previous_status == "ABSENT"
    assert result.score_delta == 6
    assert same.status is AttendanceCorrectionStatus.SAME_STATUS
    assert await count_rows(database, "audit_logs") == 1
    assert await score_repository.get_total_score(member_id=1) == 3


async def test_attendance_correction_creates_new_admin_record(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    await create_member(member_repository, "2001", "A")
    session_service, attendance_service, _, _ = build_all(
        database,
        guild_repository,
        member_repository,
    )
    await session_service.prepare_today_session(guild_id=GUILD_ID, now=utc_dt(2, 12, 30))

    result = await attendance_service.correct_attendance(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        new_status="ABSENT",
        reason="봇 장애",
        actor_discord_id=ADMIN_ID,
        now=utc_dt(2, 13, 0),
    )

    assert result.status is AttendanceCorrectionStatus.CREATED
    assert result.score_delta == -3
    assert await count_rows(database, "attendance_records") == 1
    assert await count_rows(database, "audit_logs") == 1


async def test_correction_rejects_future_and_non_session_member(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    await create_member(member_repository, "2001", "A")
    await create_member(member_repository, "2002", "B")
    session_service, attendance_service, _, _ = build_all(
        database,
        guild_repository,
        member_repository,
    )
    await session_service.prepare_today_session(guild_id=GUILD_ID, now=utc_dt(2, 12, 30))
    await create_member(member_repository, "2003", "C")

    future = await attendance_service.correct_attendance(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-03",
        new_status="PRESENT",
        reason="미래",
        actor_discord_id=ADMIN_ID,
        now=utc_dt(2, 13, 0),
    )
    not_member = await attendance_service.correct_attendance(
        guild_id=GUILD_ID,
        target_discord_id="2003",
        attendance_date="2026-07-02",
        new_status="PRESENT",
        reason="테스트",
        actor_discord_id=ADMIN_ID,
        now=utc_dt(2, 13, 0),
    )

    assert future.status is AttendanceCorrectionStatus.FUTURE_DATE
    assert not_member.status is AttendanceCorrectionStatus.NOT_SESSION_MEMBER
