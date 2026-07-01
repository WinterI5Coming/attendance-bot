"""Integration tests for Phase 2 excuse, streak, and ranking flows."""

from datetime import datetime, timezone

from bot.repositories.audit_repository import AuditRepository
from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.excuse_repository import ExcuseRepository
from bot.repositories.report_repository import ReportRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.scheduler.attendance_loop import AttendanceScheduler
from bot.services.attendance_service import AttendanceCheckInStatus, AttendanceService
from bot.services.excuse_service import ExcuseStatus, ExcuseService
from bot.services.report_service import ReportService
from bot.services.session_service import SessionCloseStatus, SessionService
from bot.services.streak_service import StreakService


GUILD_ID = "111"
ADMIN_ID = "9001"


def utc_dt(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, second, tzinfo=timezone.utc)


async def configure_daily(database) -> None:
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


def build_phase2_services(database, guild_repository, member_repository):
    session_repository = SessionRepository(database=database)
    attendance_repository = AttendanceRepository(database=database)
    score_repository = ScoreRepository(database=database)
    audit_repository = AuditRepository(database=database)
    excuse_repository = ExcuseRepository(database=database)
    streak_service = StreakService(score_repository=score_repository)
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
        excuse_repository=excuse_repository,
        streak_service=streak_service,
    )
    excuse_service = ExcuseService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        excuse_repository=excuse_repository,
        audit_repository=audit_repository,
    )
    report_service = ReportService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        report_repository=ReportRepository(database=database),
        score_repository=score_repository,
        streak_service=streak_service,
    )
    return (
        session_repository,
        attendance_repository,
        score_repository,
        session_service,
        attendance_service,
        excuse_service,
        report_service,
    )


class FakeGuildService:
    """Scheduler test double that exposes configured guild settings."""

    def __init__(self, guild_repository):
        self.guild_repository = guild_repository

    async def list_all_settings(self):
        return await self.guild_repository.list_all_settings()


class FakeChannel:
    """Tiny Discord channel test double."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
        self.messages.append(content)


class FakeBot:
    """Tiny Discord bot test double."""

    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    def get_channel(self, channel_id: int):
        return self.channel

    async def fetch_channel(self, channel_id: int):
        return self.channel


async def test_approved_excuse_turns_late_check_in_into_excused_late(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    member_id = await create_member(member_repository, "2001", "A")
    (
        session_repository,
        attendance_repository,
        score_repository,
        _,
        attendance_service,
        excuse_service,
        _,
    ) = build_phase2_services(database, guild_repository, member_repository)

    created = await excuse_service.create_request(
        guild_id=GUILD_ID,
        discord_id="2001",
        target_date="2026-07-02",
        expected_time="21:42",
        reason="업무 일정",
        now=utc_dt(2, 12, 0),
    )
    approved = await excuse_service.approve_request(
        guild_id=GUILD_ID,
        excuse_request_id=created.request["id"],
        actor_discord_id=ADMIN_ID,
        now=utc_dt(2, 12, 5),
    )
    result = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(2, 12, 40),
    )

    session = await session_repository.get_by_guild_and_date(
        guild_id=GUILD_ID,
        attendance_date="2026-07-02",
    )
    record = await attendance_repository.get_by_session_and_member(
        session_id=session["id"],
        member_id=member_id,
    )

    assert created.status is ExcuseStatus.CREATED_PENDING
    assert approved.status is ExcuseStatus.APPROVED
    assert result.status is AttendanceCheckInStatus.EXCUSED_LATE
    assert result.score_delta == 0
    assert result.total_score == 0
    assert record["status"] == "EXCUSED_LATE"
    assert record["excuse_request_id"] == created.request["id"]
    assert await score_repository.get_total_score(member_id=member_id) == 0


async def test_approved_excuse_turns_auto_absent_into_excused_absent(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    member_id = await create_member(member_repository, "2001", "A")
    (
        session_repository,
        attendance_repository,
        score_repository,
        session_service,
        _,
        excuse_service,
        _,
    ) = build_phase2_services(database, guild_repository, member_repository)

    created = await excuse_service.create_request(
        guild_id=GUILD_ID,
        discord_id="2001",
        target_date="2026-07-02",
        expected_time=None,
        reason="외부 일정",
        now=utc_dt(2, 12, 0),
    )
    await excuse_service.approve_request(
        guild_id=GUILD_ID,
        excuse_request_id=created.request["id"],
        actor_discord_id=ADMIN_ID,
        now=utc_dt(2, 12, 5),
    )
    prepared = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(2, 12, 30),
    )
    closed = await session_service.close_session(
        session_id=prepared.session["id"],
        now=utc_dt(2, 12, 46),
    )
    record = await attendance_repository.get_by_session_and_member(
        session_id=prepared.session["id"],
        member_id=member_id,
    )

    assert closed.status is SessionCloseStatus.CLOSED
    assert record["status"] == "EXCUSED_ABSENT"
    assert record["excuse_request_id"] == created.request["id"]
    assert await score_repository.get_total_score(member_id=member_id) == -1


async def test_streak_bonus_and_ranking_use_current_scores(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    member_a = await create_member(member_repository, "2001", "A")
    await create_member(member_repository, "2002", "B")
    (
        _,
        _,
        score_repository,
        session_service,
        attendance_service,
        _,
        report_service,
    ) = build_phase2_services(database, guild_repository, member_repository)

    for day in (2, 3, 4):
        result = await attendance_service.check_in(
            guild_id=GUILD_ID,
            discord_id="2001",
            now=utc_dt(day, 12, 30),
        )
        sessions = await session_service.session_repository.get_by_guild_and_date(
            guild_id=GUILD_ID,
            attendance_date=f"2026-07-{day:02d}",
        )
        await session_service.close_session(
            session_id=sessions["id"],
            now=utc_dt(day, 12, 46),
        )

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2002",
        now=utc_dt(4, 12, 30),
    )

    report = await report_service.get_my_report(guild_id=GUILD_ID, discord_id="2001")
    ranking = await report_service.get_ranking(guild_id=GUILD_ID)

    assert result.status is AttendanceCheckInStatus.PRESENT
    assert result.current_streak == 3
    assert result.streak_bonus_delta == 2
    assert await score_repository.get_total_score(member_id=member_a) == 11
    assert report.current_streak == 3
    assert ranking.configured
    assert ranking.entries[0].discord_id == "2001"
    assert ranking.entries[0].total_score == 11
    assert ranking.entries[1].discord_id == "2002"


async def test_scheduler_sends_start_and_close_announcements_once(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily(database)
    await create_member(member_repository, "2001", "A")
    (
        session_repository,
        _,
        _,
        session_service,
        _,
        _,
        _,
    ) = build_phase2_services(database, guild_repository, member_repository)
    channel = FakeChannel()
    scheduler = AttendanceScheduler(
        guild_service=FakeGuildService(guild_repository),
        session_service=session_service,
        bot=FakeBot(channel),
    )

    await scheduler.run_once(utc_dt(2, 12, 30))
    await scheduler.run_once(utc_dt(2, 12, 31))
    await scheduler.run_once(utc_dt(2, 12, 46))
    session = await session_repository.get_by_guild_and_date(
        guild_id=GUILD_ID,
        attendance_date="2026-07-02",
    )

    assert len(channel.messages) == 2
    assert "출석이 시작되었습니다." in channel.messages[0]
    assert "출석이 마감되었습니다." in channel.messages[1]
    assert session["start_announced_at"] is not None
    assert session["close_announced_at"] is not None
