"""봇 다운타임과 서버 변동(탈퇴 대원, 봇 제거)에 대한 복구 동작 테스트."""

from types import SimpleNamespace

import discord
from tests.helpers import (
    GUILD_ID,
    FakeGuildService,
    configure_guild,
    create_member,
    list_sessions,
    utc_dt,
)

from bot.repositories.session_repository import SessionRepository
from bot.scheduler.attendance_loop import AttendanceScheduler
from bot.services.member_service import MemberService
from bot.services.session_service import (
    MISSED_SESSION_CANCEL_REASON,
    SessionService,
)


def build_session_service(database, guild_repository, member_repository):
    return SessionService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=SessionRepository(database=database),
    )


async def test_recover_missed_sessions_marks_gap_days_cancelled(
    database, guild_repository, member_repository
):
    await configure_guild(database)
    member_id = await create_member(member_repository, "2001", "A")
    session_service = build_session_service(database, guild_repository, member_repository)

    await session_service.prepare_today_session(guild_id=GUILD_ID, now=utc_dt(12, 0, day=2))
    # 7/3 ~ 7/5 동안 봇이 꺼져 있다가 7/6에 다시 뜬 상황.
    result = await session_service.recover_missed_sessions(
        guild_id=GUILD_ID, now=utc_dt(3, 0, day=6)
    )
    again = await session_service.recover_missed_sessions(
        guild_id=GUILD_ID, now=utc_dt(3, 5, day=6)
    )
    sessions = await list_sessions(database)

    assert result.created_dates == ("2026-07-03", "2026-07-04", "2026-07-05")
    assert again.created_dates == ()
    assert [row["attendance_date"] for row in sessions] == [
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
        "2026-07-05",
    ]
    for row in sessions[1:]:
        assert row["status"] == "CANCELLED"
        assert row["cancel_reason"] == MISSED_SESSION_CANCEL_REASON
    # 결석 기록이나 점수는 남기지 않는다.
    assert (
        await SessionRepository(database=database).list_members_with_attendance(
            session_id=sessions[1]["id"]
        )
    )[0]["member_id"] == member_id
    connection = await database.connect()
    try:
        records = await connection.execute_fetchall("SELECT COUNT(*) AS c FROM attendance_records;")
        events = await connection.execute_fetchall("SELECT COUNT(*) AS c FROM score_events;")
    finally:
        await connection.close()
    assert records[0]["c"] == 0 and events[0]["c"] == 0


async def test_recover_missed_sessions_skips_non_attendance_days_and_new_guilds(
    database, guild_repository, member_repository
):
    await configure_guild(database, attendance_days="MON,TUE,WED,THU,FRI")
    await create_member(member_repository, "2001", "A")
    session_service = build_session_service(database, guild_repository, member_repository)

    # 세션이 하나도 없는 새 서버는 아무것도 만들지 않는다.
    empty = await session_service.recover_missed_sessions(guild_id=GUILD_ID, now=utc_dt(3, 0, day=7))
    assert empty.created_dates == ()

    await session_service.prepare_today_session(guild_id=GUILD_ID, now=utc_dt(12, 0, day=2))
    # 7/3(금) 출석일, 7/4(토)·7/5(일) 휴무, 7/6(월) 출석일, 7/7(화) 오늘.
    result = await session_service.recover_missed_sessions(
        guild_id=GUILD_ID, now=utc_dt(3, 0, day=7)
    )

    assert result.created_dates == ("2026-07-03", "2026-07-06")


async def test_recover_missed_sessions_is_bounded_by_lookback(
    database, guild_repository, member_repository
):
    await configure_guild(database)
    await create_member(member_repository, "2001", "A")
    session_service = build_session_service(database, guild_repository, member_repository)

    await session_service.prepare_today_session(guild_id=GUILD_ID, now=utc_dt(12, 0, day=2))
    result = await session_service.recover_missed_sessions(
        guild_id=GUILD_ID, now=utc_dt(3, 0, day=20), lookback_days=3
    )

    assert result.created_dates == ("2026-07-17", "2026-07-18", "2026-07-19")


class FakeGuild:
    def __init__(self, *, cached: set[int], existing: set[int]) -> None:
        self.cached = cached
        self.existing = existing
        self.fetched: list[int] = []

    def get_member(self, user_id):
        return object() if user_id in self.cached else None

    async def fetch_member(self, user_id):
        self.fetched.append(user_id)
        if user_id in self.existing:
            return object()
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"message": "Unknown Member", "code": 10007},
        )


class FakeBot:
    def __init__(self, guild) -> None:
        self.guild = guild
        self.user = SimpleNamespace(id=1)

    def is_ready(self):
        return True

    def get_guild(self, guild_id):
        return self.guild if self.guild is not None and guild_id == int(GUILD_ID) else None


async def test_daily_membership_sync_deactivates_departed_members(
    database, guild_repository, member_repository
):
    await configure_guild(database)
    await create_member(member_repository, "2001", "A")
    await create_member(member_repository, "2002", "B")
    await create_member(member_repository, "2003", "C")
    guild = FakeGuild(cached={2001}, existing={2002})
    scheduler = AttendanceScheduler(
        guild_service=FakeGuildService(guild_repository),
        session_service=build_session_service(database, guild_repository, member_repository),
        bot=FakeBot(guild),
        member_service=MemberService(repository=member_repository),
    )

    await scheduler.run_once(utc_dt(1, 0, day=2))
    await scheduler.run_once(utc_dt(1, 1, day=2))
    active = await member_repository.list_active(guild_id=GUILD_ID)

    assert {row["discord_id"] for row in active} == {"2001", "2002"}
    # 캐시된 대원은 조회하지 않고, 같은 날에는 동기화를 반복하지 않는다.
    assert guild.fetched == [2002, 2003]


async def test_scheduler_marks_guild_removed_when_bot_is_not_in_it(
    database, guild_repository, member_repository
):
    await configure_guild(database)
    await create_member(member_repository, "2001", "A")
    guild_service = FakeGuildService(guild_repository)

    async def mark_bot_removed(*, guild_id, now):
        return await guild_repository.set_bot_removed_at(
            guild_id=str(guild_id), removed_at=now.isoformat(), now=now.isoformat()
        )

    guild_service.mark_bot_removed = mark_bot_removed
    scheduler = AttendanceScheduler(
        guild_service=guild_service,
        session_service=build_session_service(database, guild_repository, member_repository),
        bot=FakeBot(None),
    )

    await scheduler.run_once(utc_dt(12, 0, day=2))
    remaining = await guild_repository.list_all_settings()
    sessions = await list_sessions(database)

    # 봇이 없는 서버는 세션을 만들지 않고 이후 순회 대상에서도 빠진다.
    assert sessions == []
    assert GUILD_ID not in {row["guild_id"] for row in remaining}

    # 다시 들어오면 표시가 지워진다.
    await guild_repository.set_bot_removed_at(
        guild_id=GUILD_ID, removed_at=None, now=utc_dt(12, 1, day=2).isoformat()
    )
    restored = await guild_repository.list_all_settings()
    assert GUILD_ID in {row["guild_id"] for row in restored}
