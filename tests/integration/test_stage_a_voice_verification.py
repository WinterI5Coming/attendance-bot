"""Integration tests for Stage A voice participation verification."""


from tests.helpers import (
    GUILD_ID,
    configure_guild,
    create_member,
    fetch_all,
    utc_dt,
)

from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.repositories.stage_a_repository import StageARepository
from bot.services.attendance_service import AttendanceService
from bot.services.session_service import SessionService
from bot.services.voice_verification_service import VoiceVerificationService


def build_services(database, guild_repository, member_repository):
    session_repository = SessionRepository(database=database)
    attendance_repository = AttendanceRepository(database=database)
    score_repository = ScoreRepository(database=database)
    stage_a_repository = StageARepository(database=database)
    session_service = SessionService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
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
        voice_verification_service=voice_service,
    )
    return (
        session_repository,
        attendance_repository,
        score_repository,
        stage_a_repository,
        session_service,
        voice_service,
        attendance_service,
    )


async def test_voice_disabled_preserves_existing_check_in_behavior(
    database,
    guild_repository,
    member_repository,
):
    await configure_guild(database, voice_verification_enabled=0, voice_channel_ids="777", voice_category_ids=None)
    member_id = await create_member(member_repository, "2001", "A")
    _, _, score_repository, _, _, _, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    result = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 30),
        current_voice_channel_id="777",
    )
    verifications = await fetch_all(database, "SELECT * FROM attendance_verifications;")

    assert result.attendance_status == "PRESENT"
    assert result.score_delta == 3
    assert await score_repository.get_total_score(member_id=member_id) == 3
    assert verifications == []


async def test_voice_duration_can_verify_before_end_time(
    database,
    guild_repository,
    member_repository,
):
    await configure_guild(database, voice_verification_enabled=1, voice_channel_ids="777", voice_category_ids=None)
    await create_member(member_repository, "2001", "A")
    _, _, _, _, _, voice_service, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 30),
    )
    await voice_service.handle_voice_update(
        guild_id=GUILD_ID,
        discord_id="2001",
        before_channel_id=None,
        before_category_id=None,
        after_channel_id="777",
        after_category_id=None,
        now=utc_dt(12, 31),
    )
    await voice_service.handle_voice_update(
        guild_id=GUILD_ID,
        discord_id="2001",
        before_channel_id="777",
        before_category_id=None,
        after_channel_id=None,
        after_category_id=None,
        now=utc_dt(13, 31),
    )
    rows = await fetch_all(database, "SELECT * FROM attendance_verifications;")
    logs = await fetch_all(database, "SELECT * FROM voice_presence_logs;")

    assert len(rows) == 1
    assert rows[0]["status"] == "VERIFIED"
    assert rows[0]["accumulated_seconds"] == 3600
    assert logs[0]["duration_seconds"] == 3600
    assert logs[0]["close_reason"] == "LEFT"


async def test_finalize_no_voice_join_creates_single_penalty(
    database,
    guild_repository,
    member_repository,
):
    await configure_guild(database, voice_verification_enabled=1, voice_channel_ids="777", voice_category_ids=None)
    member_id = await create_member(member_repository, "2001", "A")
    _, _, score_repository, _, _, voice_service, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 30),
    )
    first = await voice_service.finalize_due_verifications(now=utc_dt(14, 1))
    second = await voice_service.finalize_due_verifications(now=utc_dt(14, 2))
    verifications = await fetch_all(database, "SELECT * FROM attendance_verifications;")
    events = await fetch_all(
        database,
        """
        SELECT event_type, delta, reference_type, dedup_key
        FROM score_events
        ORDER BY id;
        """,
    )

    assert first.processed == 1
    assert first.failed == 1
    assert first.penalties == 1
    assert second.processed == 0
    assert verifications[0]["status"] == "FAILED"
    assert verifications[0]["failure_reason"] == "NO_VOICE_JOIN"
    assert [event["event_type"] for event in events] == [
        "ATTENDANCE_PRESENT",
        "NO_PARTICIPATION_PENALTY",
    ]
    assert events[1]["reference_type"] == "VOICE_VERIFICATION"
    assert events[1]["dedup_key"] == "voice-verification:1:failure"
    assert await score_repository.get_total_score(member_id=member_id) == 1


async def test_check_in_inside_configured_category_opens_voice_log(
    database,
    guild_repository,
    member_repository,
):
    """카테고리로만 대상을 지정해도 체크인 시점의 음성 참여가 인정되어야 한다."""

    await configure_guild(database, voice_verification_enabled=1, voice_channel_ids=None, voice_category_ids="555")
    await create_member(member_repository, "2001", "A")
    _, _, _, _, _, voice_service, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 30),
        current_voice_channel_id="999",
        current_voice_category_id="555",
    )
    logs = await fetch_all(database, "SELECT * FROM voice_presence_logs;")
    assert len(logs) == 1
    assert logs[0]["left_at"] is None

    result = await voice_service.finalize_due_verifications(now=utc_dt(14, 1))
    verifications = await fetch_all(database, "SELECT * FROM attendance_verifications;")

    assert result.verified == 1
    assert verifications[0]["status"] == "VERIFIED"
    # 체크인(21:30 KST)부터 검증 마감(23:00 KST)까지 90분이 인정된다.
    assert verifications[0]["accumulated_seconds"] == 90 * 60


async def test_today_verification_overview_reflects_live_accumulation(
    database,
    guild_repository,
    member_repository,
):
    await configure_guild(database, voice_verification_enabled=1, voice_channel_ids="777", voice_category_ids=None)
    await create_member(member_repository, "2001", "A")
    await create_member(member_repository, "2002", "B")
    _, _, _, _, _, voice_service, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    await attendance_service.check_in(guild_id=GUILD_ID, discord_id="2001", now=utc_dt(12, 30))
    await attendance_service.check_in(guild_id=GUILD_ID, discord_id="2002", now=utc_dt(12, 32))
    await voice_service.handle_voice_update(
        guild_id=GUILD_ID,
        discord_id="2001",
        before_channel_id=None,
        before_category_id=None,
        after_channel_id="777",
        after_category_id=None,
        now=utc_dt(12, 40),
    )

    overview = await voice_service.list_today_verifications(guild_id=GUILD_ID, now=utc_dt(13, 0))

    assert overview.configured and overview.enabled and overview.has_targets
    assert overview.voice_channel_ids == ["777"]
    assert overview.session is not None
    by_id = {row["discord_id"]: row for row in overview.rows}
    assert by_id["2001"]["status"] == "PENDING"
    assert by_id["2001"]["accumulated_seconds"] == 20 * 60
    assert by_id["2002"]["accumulated_seconds"] == 0
    assert by_id["2001"]["attendance_status"] == "PRESENT"
