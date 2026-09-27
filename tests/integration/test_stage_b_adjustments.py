"""Integration tests for Stage B attendance adjustments."""


from tests.helpers import (
    ADMIN_ID,
    GUILD_ID,
    configure_guild,
    count_rows,
    create_member,
    fetch_all,
    utc_dt,
)

from bot.repositories.adjustment_repository import AdjustmentRepository
from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.excuse_repository import ExcuseRepository
from bot.repositories.report_repository import ReportRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.services.adjustment_service import AdjustmentService, AdjustmentStatus
from bot.services.attendance_service import AttendanceService
from bot.services.report_service import ReportService
from bot.services.session_service import SessionService


async def create_approved_excuse(excuse_repository, member_id: int) -> dict:
    return await excuse_repository.create(
        guild_id=GUILD_ID,
        member_id=member_id,
        target_date="2026-07-02",
        reason="approved reason",
        expected_time=None,
        status="APPROVED",
        requested_at="2026-07-02T00:00:00+00:00",
    )


def build_services(database, guild_repository, member_repository):
    session_repository = SessionRepository(database=database)
    attendance_repository = AttendanceRepository(database=database)
    score_repository = ScoreRepository(database=database)
    audit_repository = AuditRepository(database=database)
    excuse_repository = ExcuseRepository(database=database)
    adjustment_repository = AdjustmentRepository(database=database)
    session_service = SessionService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        excuse_repository=excuse_repository,
    )
    attendance_service = AttendanceService(
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        session_service=session_service,
        guild_repository=guild_repository,
        audit_repository=audit_repository,
        excuse_repository=None,
    )
    adjustment_service = AdjustmentService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        excuse_repository=excuse_repository,
        score_repository=score_repository,
        audit_repository=audit_repository,
        adjustment_repository=adjustment_repository,
    )
    report_service = ReportService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        report_repository=ReportRepository(database=database),
        score_repository=score_repository,
    )
    return (
        session_service,
        attendance_service,
        adjustment_service,
        report_service,
        excuse_repository,
        score_repository,
    )


async def test_full_late_reduction_applies_delta_and_cancel_reversal(
    database,
    guild_repository,
    member_repository,
):
    await configure_guild(database, close_deadline="22:30", exempt_absence_counts_in_attendance_denominator=0)
    member_id = await create_member(member_repository, "2001", "A")
    (
        _,
        attendance_service,
        adjustment_service,
        report_service,
        excuse_repository,
        score_repository,
    ) = build_services(database, guild_repository, member_repository)

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 55),
    )
    excuse = await create_approved_excuse(excuse_repository, member_id)

    applied = await adjustment_service.apply_late_reduction(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        reduction_minutes=15,
        full_reduction=False,
        reason="approved late reduction",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(13, 0),
    )
    duplicate = await adjustment_service.apply_late_reduction(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        reduction_minutes=15,
        full_reduction=True,
        reason="duplicate",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(13, 1),
    )
    report = await report_service.get_my_report(guild_id=GUILD_ID, discord_id="2001")
    total_after_apply = await score_repository.get_total_score(member_id=member_id)
    cancelled = await adjustment_service.cancel_late_reduction(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        reason="cancel reduction",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(13, 2),
    )

    assert applied.status is AdjustmentStatus.APPLIED
    assert applied.excuse_request_id == excuse["id"]
    assert applied.original_late_seconds == 900
    assert applied.resulting_late_seconds == 0
    assert applied.resulting_status == "PRESENT"
    assert applied.score_delta == 2
    assert duplicate.status is AdjustmentStatus.DUPLICATE_ACTIVE_ADJUSTMENT
    assert report.present_count == 1
    assert report.late_count == 0
    assert total_after_apply == 3
    assert cancelled.status is AdjustmentStatus.CANCELLED
    assert cancelled.reversal_delta == -2
    assert await score_repository.get_total_score(member_id=member_id) == 1
    assert await count_rows(database, "audit_logs") == 2
    assert [event["event_type"] for event in await fetch_all(database, "SELECT event_type, delta, reference_type, reference_id, dedup_key, reversed_event_id FROM score_events ORDER BY id;")] == [
        "ATTENDANCE_LATE",
        "LATE_REDUCTION_ADJUSTMENT",
        "LATE_REDUCTION_REVERSAL",
    ]


async def test_partial_late_reduction_can_record_zero_delta_without_score_event(
    database,
    guild_repository,
    member_repository,
):
    await configure_guild(database, close_deadline="22:30", exempt_absence_counts_in_attendance_denominator=0)
    member_id = await create_member(member_repository, "2001", "A")
    _, attendance_service, adjustment_service, _, excuse_repository, _ = build_services(
        database,
        guild_repository,
        member_repository,
    )

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 55),
    )
    await create_approved_excuse(excuse_repository, member_id)

    result = await adjustment_service.apply_late_reduction(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        reduction_minutes=10,
        full_reduction=False,
        reason="partial reduction",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(13, 0),
    )

    assert result.status is AdjustmentStatus.APPLIED
    assert result.resulting_late_seconds == 300
    assert result.resulting_status == "LATE"
    assert result.score_delta == 0
    assert await count_rows(database, "attendance_adjustments") == 1
    assert await count_rows(database, "score_events") == 1


async def test_absence_exemption_excludes_denominator_and_cancel_restores(
    database,
    guild_repository,
    member_repository,
):
    await configure_guild(database, close_deadline="22:30", exempt_absence_counts_in_attendance_denominator=0)
    member_id = await create_member(member_repository, "2001", "A")
    (
        session_service,
        _,
        adjustment_service,
        report_service,
        excuse_repository,
        score_repository,
    ) = build_services(database, guild_repository, member_repository)
    prepared = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 30),
    )
    await session_service.close_session(session_id=prepared.session["id"], now=utc_dt(13, 31))
    await create_approved_excuse(excuse_repository, member_id)

    applied = await adjustment_service.apply_absence_exemption(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        reason="approved absence exemption",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(13, 40),
    )
    report = await report_service.get_my_report(guild_id=GUILD_ID, discord_id="2001")
    total_after_apply = await score_repository.get_total_score(member_id=member_id)
    cancelled = await adjustment_service.cancel_absence_exemption(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        reason="cancel exemption",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(13, 41),
    )
    restored = await report_service.get_my_report(guild_id=GUILD_ID, discord_id="2001")

    assert applied.status is AdjustmentStatus.APPLIED
    assert applied.score_delta == 3
    assert total_after_apply == 0
    assert report.total_sessions == 0
    assert report.absent_count == 0
    assert cancelled.status is AdjustmentStatus.CANCELLED
    assert cancelled.reversal_delta == -3
    assert await score_repository.get_total_score(member_id=member_id) == -3
    assert restored.total_sessions == 1
    assert restored.absent_count == 1


async def test_adjustments_require_permission_and_approved_excuse(
    database,
    guild_repository,
    member_repository,
):
    await configure_guild(database, close_deadline="22:30", exempt_absence_counts_in_attendance_denominator=0)
    await create_member(member_repository, "2001", "A")
    _, attendance_service, adjustment_service, _, _, _ = build_services(
        database,
        guild_repository,
        member_repository,
    )
    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 55),
    )

    denied = await adjustment_service.apply_late_reduction(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        reduction_minutes=5,
        full_reduction=False,
        reason="no permission",
        actor_discord_id=ADMIN_ID,
        has_permission=False,
        now=utc_dt(13, 0),
    )
    missing_excuse = await adjustment_service.apply_late_reduction(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        reduction_minutes=5,
        full_reduction=False,
        reason="missing excuse",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(13, 1),
    )

    assert denied.status is AdjustmentStatus.PERMISSION_DENIED
    assert missing_excuse.status is AdjustmentStatus.EXCUSE_NOT_APPROVED
    assert await count_rows(database, "attendance_adjustments") == 0
