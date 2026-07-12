"""Integration tests for Stage A voice participation verification."""

from datetime import datetime, timezone

from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.repositories.stage_a_repository import StageARepository
from bot.services.attendance_service import AttendanceService
from bot.services.session_service import SessionService
from bot.services.voice_verification_service import VoiceVerificationService


GUILD_ID = "111"
ADMIN_ID = "9001"


def utc_dt(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, 2, hour, minute, second, tzinfo=timezone.utc)


async def configure_daily_voice(database, *, enabled: bool = True) -> None:
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
                timezone = 'Asia/Seoul',
                voice_verification_enabled = ?,
                voice_channel_ids = '777',
                voice_category_ids = NULL
            WHERE guild_id = ?;
            """,
            (1 if enabled else 0, GUILD_ID),
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
        now="2026-07-02T00:00:00+00:00",
    )


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


async def fetch_all(database, sql: str):
    connection = await database.connect()
    try:
        return [dict(row) for row in await connection.execute_fetchall(sql)]
    finally:
        await connection.close()


async def test_voice_disabled_preserves_existing_check_in_behavior(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_voice(database, enabled=False)
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
    await configure_daily_voice(database, enabled=True)
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
    await configure_daily_voice(database, enabled=True)
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
