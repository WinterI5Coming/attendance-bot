"""재시작 복구, 세션 취소, 출석 정정, 조퇴 사유와 음성 검증의 연동 테스트."""

from tests.helpers import (
    ADMIN_ID,
    GUILD_ID,
    configure_guild,
    create_member,
    fetch_all,
    utc_dt,
)

from bot.policies.score_policy import get_attendance_score
from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.excuse_repository import ExcuseRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.repositories.stage_a_repository import StageARepository
from bot.services.admin_service import AdminService, SessionControlStatus
from bot.services.attendance_service import AttendanceService
from bot.services.excuse_service import ExcuseService, ExcuseStatus
from bot.services.session_service import SessionService
from bot.services.voice_verification_service import VoiceVerificationService


def build_services(database, guild_repository, member_repository):
    session_repository = SessionRepository(database=database)
    attendance_repository = AttendanceRepository(database=database)
    score_repository = ScoreRepository(database=database)
    stage_a_repository = StageARepository(database=database)
    audit_repository = AuditRepository(database=database)
    excuse_repository = ExcuseRepository(database=database)
    session_service = SessionService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        excuse_repository=excuse_repository,
        stage_a_repository=stage_a_repository,
    )
    voice_service = VoiceVerificationService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        stage_a_repository=stage_a_repository,
    )
    attendance_service = AttendanceService(
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        session_service=session_service,
        guild_repository=guild_repository,
        audit_repository=audit_repository,
        excuse_repository=excuse_repository,
        voice_verification_service=voice_service,
    )
    admin_service = AdminService(
        guild_repository=guild_repository,
        session_repository=session_repository,
        score_repository=score_repository,
        audit_repository=audit_repository,
        voice_verification_service=voice_service,
    )
    excuse_service = ExcuseService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        excuse_repository=excuse_repository,
        audit_repository=audit_repository,
        voice_verification_service=voice_service,
    )
    return {
        "score": score_repository,
        "stage_a": stage_a_repository,
        "session": session_service,
        "voice": voice_service,
        "attendance": attendance_service,
        "admin": admin_service,
        "excuse": excuse_service,
    }


async def enable_voice(database):
    await configure_guild(
        database,
        voice_verification_enabled=1,
        voice_channel_ids="777",
        voice_category_ids=None,
    )


async def join(voice_service, discord_id, now, channel="777"):
    await voice_service.handle_voice_update(
        guild_id=GUILD_ID,
        discord_id=discord_id,
        before_channel_id=None,
        before_category_id=None,
        after_channel_id=channel,
        after_category_id=None,
        now=now,
    )


async def leave(voice_service, discord_id, now, channel="777"):
    await voice_service.handle_voice_update(
        guild_id=GUILD_ID,
        discord_id=discord_id,
        before_channel_id=channel,
        before_category_id=None,
        after_channel_id=None,
        after_category_id=None,
        now=now,
    )


async def test_reconcile_closes_missed_leave_at_last_heartbeat(
    database, guild_repository, member_repository
):
    """봇이 꺼진 사이 나간 대원의 열린 로그는 마지막 하트비트까지만 인정한다."""

    await enable_voice(database)
    await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    await join(services["voice"], "2001", utc_dt(12, 31))
    await services["voice"].record_heartbeat(now=utc_dt(12, 50))

    # 봇 다운 → 대원 퇴장(이벤트 유실) → 재시작 시 아무도 음성 채널에 없음.
    result = await services["voice"].reconcile_guild_voice_presence(
        guild_id=GUILD_ID, present={}, now=utc_dt(13, 30)
    )
    logs = await fetch_all(database, "SELECT * FROM voice_presence_logs ORDER BY id;")
    verifications = await fetch_all(database, "SELECT * FROM attendance_verifications;")

    assert result.pending == 1 and result.closed_logs == 1 and result.opened_logs == 0
    assert logs[0]["close_reason"] == "BOT_RECOVERY"
    assert logs[0]["left_at"] == utc_dt(12, 50).isoformat()
    assert logs[0]["duration_seconds"] == 19 * 60
    assert verifications[0]["status"] == "PENDING"
    assert verifications[0]["accumulated_seconds"] == 19 * 60


async def test_reconcile_opens_log_for_member_already_in_channel(
    database, guild_repository, member_repository
):
    """봇이 꺼진 사이 들어온 대원은 재시작 시각부터 체류를 인정한다."""

    await enable_voice(database)
    await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    result = await services["voice"].reconcile_guild_voice_presence(
        guild_id=GUILD_ID,
        present={"2001": ("777", None)},
        now=utc_dt(12, 40),
    )
    await leave(services["voice"], "2001", utc_dt(13, 40))
    verifications = await fetch_all(database, "SELECT * FROM attendance_verifications;")

    assert result.opened_logs == 1
    assert verifications[0]["status"] == "VERIFIED"
    assert verifications[0]["accumulated_seconds"] == 3600


async def test_reconcile_ignores_members_outside_target_channels(
    database, guild_repository, member_repository
):
    await enable_voice(database)
    await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    result = await services["voice"].reconcile_guild_voice_presence(
        guild_id=GUILD_ID,
        present={"2001": ("888", None)},
        now=utc_dt(12, 40),
    )
    logs = await fetch_all(database, "SELECT * FROM voice_presence_logs;")

    assert result.opened_logs == 0
    assert logs == []


async def test_rejoin_after_missed_leave_replaces_stale_open_log(
    database, guild_repository, member_repository
):
    """퇴장 이벤트를 놓친 뒤 재입장하면 이전 로그를 하트비트 시각에 닫고 새로 연다."""

    await enable_voice(database)
    await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    await join(services["voice"], "2001", utc_dt(12, 31))
    await services["voice"].record_heartbeat(now=utc_dt(12, 45))
    await join(services["voice"], "2001", utc_dt(13, 0))
    logs = await fetch_all(database, "SELECT * FROM voice_presence_logs ORDER BY id;")

    assert len(logs) == 2
    assert logs[0]["close_reason"] == "BOT_RECOVERY"
    assert logs[0]["left_at"] == utc_dt(12, 45).isoformat()
    assert logs[1]["left_at"] is None
    assert logs[1]["joined_at"] == utc_dt(13, 0).isoformat()


async def test_leaving_channel_removed_from_targets_still_closes_log(
    database, guild_repository, member_repository
):
    await enable_voice(database)
    await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    await join(services["voice"], "2001", utc_dt(12, 31))
    await configure_guild(database, voice_channel_ids="999")
    await leave(services["voice"], "2001", utc_dt(12, 41))
    logs = await fetch_all(database, "SELECT * FROM voice_presence_logs;")

    assert logs[0]["left_at"] is not None
    assert logs[0]["duration_seconds"] == 10 * 60


async def test_cancel_waives_pending_verifications_and_resume_restores(
    database, guild_repository, member_repository
):
    await enable_voice(database)
    member_id = await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    cancelled = await services["admin"].cancel_today_session(
        guild_id=GUILD_ID,
        reason="휴무",
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(12, 35),
    )
    waived = await fetch_all(database, "SELECT * FROM attendance_verifications;")
    finalize = await services["voice"].finalize_due_verifications(now=utc_dt(14, 1))
    resumed = await services["admin"].resume_today_session(
        guild_id=GUILD_ID,
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(12, 36),
    )
    restored = await fetch_all(database, "SELECT * FROM attendance_verifications;")

    assert cancelled.status is SessionControlStatus.CANCELLED
    assert waived[0]["status"] == "WAIVED"
    assert waived[0]["waived_reason"] == "SESSION_CANCELLED"
    assert finalize.processed == 0
    assert resumed.status is SessionControlStatus.RESUMED
    assert restored[0]["status"] == "PENDING"
    assert restored[0]["waived_reason"] is None
    # 취소된 세션에는 음성 검증 감점이 붙지 않는다.
    events = await fetch_all(database, "SELECT event_type FROM score_events;")
    assert "NO_PARTICIPATION_PENALTY" not in {row["event_type"] for row in events}
    assert await services["score"].get_total_score(member_id=member_id) == 3


async def test_finalize_waives_when_session_cancelled_after_check_in(
    database, guild_repository, member_repository
):
    """취소 경로를 거치지 않고 세션만 CANCELLED여도 마감 처리는 감점 없이 면제한다."""

    await enable_voice(database)
    await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    connection = await database.connect()
    try:
        await connection.execute("UPDATE attendance_sessions SET status = 'CANCELLED';")
        await connection.commit()
    finally:
        await connection.close()

    result = await services["voice"].finalize_due_verifications(now=utc_dt(14, 1))
    rows = await fetch_all(database, "SELECT * FROM attendance_verifications;")

    assert result.processed == 1 and result.failed == 0 and result.penalties == 0
    assert rows[0]["status"] == "WAIVED"
    assert rows[0]["waived_reason"] == "SESSION_CANCELLED"


async def test_finalize_waives_when_verification_disabled_mid_day(
    database, guild_repository, member_repository
):
    await enable_voice(database)
    await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    await configure_guild(database, voice_verification_enabled=0)
    result = await services["voice"].finalize_due_verifications(now=utc_dt(14, 1))
    rows = await fetch_all(database, "SELECT * FROM attendance_verifications;")

    assert result.penalties == 0
    assert rows[0]["status"] == "WAIVED"
    assert rows[0]["waived_reason"] == "VERIFICATION_DISABLED"


async def test_correction_to_absent_waives_verification_and_reverses_penalty(
    database, guild_repository, member_repository
):
    await enable_voice(database)
    member_id = await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    finalize = await services["voice"].finalize_due_verifications(now=utc_dt(14, 1))
    assert finalize.penalties == 1
    assert await services["score"].get_total_score(member_id=member_id) == 3 - 2

    corrected = await services["attendance"].correct_attendance(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        new_status="ABSENT",
        reason="실제로는 결석",
        actor_discord_id=ADMIN_ID,
        now=utc_dt(14, 10),
    )
    rows = await fetch_all(database, "SELECT * FROM attendance_verifications;")
    events = await fetch_all(
        database,
        "SELECT event_type, delta, reversed_event_id FROM score_events ORDER BY id;",
    )

    assert corrected.new_status == "ABSENT"
    assert rows[0]["status"] == "WAIVED"
    assert rows[0]["waived_reason"] == "ATTENDANCE_CORRECTED"
    reversal = [event for event in events if event["event_type"] == "VOICE_PENALTY_WAIVED"]
    assert len(reversal) == 1 and reversal[0]["delta"] == 2
    assert await services["score"].get_total_score(member_id=member_id) == get_attendance_score(
        "ABSENT"
    )

    # 같은 기록을 다시 정정해도 감점 취소가 중복되지 않는다.
    await services["attendance"].correct_attendance(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        new_status="LATE",
        reason="다시 정정",
        actor_discord_id=ADMIN_ID,
        now=utc_dt(14, 11),
    )
    await services["attendance"].correct_attendance(
        guild_id=GUILD_ID,
        target_discord_id="2001",
        attendance_date="2026-07-02",
        new_status="ABSENT",
        reason="또 정정",
        actor_discord_id=ADMIN_ID,
        now=utc_dt(14, 12),
    )
    events = await fetch_all(database, "SELECT event_type FROM score_events;")
    assert sum(1 for e in events if e["event_type"] == "VOICE_PENALTY_WAIVED") == 1


async def test_early_leave_excuse_approval_waives_verification(
    database, guild_repository, member_repository
):
    await enable_voice(database)
    member_id = await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    created = await services["excuse"].create_request(
        guild_id=GUILD_ID,
        discord_id="2001",
        target_date="2026-07-02",
        expected_time="22:00",
        reason="병원 진료",
        now=utc_dt(13, 0, day=1),
        excuse_type="EARLY_LEAVE",
    )
    assert created.status is ExcuseStatus.CREATED_PENDING

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    approved = await services["excuse"].approve_request(
        guild_id=GUILD_ID,
        excuse_request_id=created.request["id"],
        actor_discord_id=ADMIN_ID,
        now=utc_dt(12, 35),
    )
    finalize = await services["voice"].finalize_due_verifications(now=utc_dt(14, 1))
    rows = await fetch_all(database, "SELECT * FROM attendance_verifications;")

    assert approved.status is ExcuseStatus.APPROVED
    assert rows[0]["status"] == "WAIVED"
    assert rows[0]["waived_reason"] == "EXCUSE_APPROVED"
    assert finalize.penalties == 0
    assert await services["score"].get_total_score(member_id=member_id) == 3


async def test_verification_end_is_at_least_close_plus_required_minutes(
    database, guild_repository, member_repository
):
    """마감이 늦은 서버에서는 검증 종료가 '마감 + 요구 시간' 이후로 밀린다."""

    await configure_guild(
        database,
        attendance_start="22:00",
        late_deadline="22:20",
        close_deadline="22:30",
        voice_verification_enabled=1,
        voice_channel_ids="777",
    )
    await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    prepared = await services["session"].prepare_today_session(
        guild_id=GUILD_ID, now=utc_dt(13, 0)
    )

    # 22:30 KST 마감 + 60분 = 23:30 KST = 14:30 UTC (기본 23:00 KST보다 늦다)
    assert prepared.session["verification_end_at"] == utc_dt(14, 30).isoformat()


async def test_zero_penalty_setting_creates_no_score_event(
    database, guild_repository, member_repository
):
    await enable_voice(database)
    member_id = await create_member(member_repository, "2001", "A")
    services = build_services(database, guild_repository, member_repository)

    await services["attendance"].check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    connection = await database.connect()
    try:
        await connection.execute(
            "UPDATE attendance_sessions SET no_participation_penalty = 0, early_leave_penalty = 0;"
        )
        await connection.commit()
    finally:
        await connection.close()

    result = await services["voice"].finalize_due_verifications(now=utc_dt(14, 1))

    assert result.failed == 1 and result.penalties == 0
    assert await services["score"].get_total_score(member_id=member_id) == 3
