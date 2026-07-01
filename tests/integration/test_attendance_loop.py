"""Integration tests for the actual attendance check-in loop."""

from datetime import datetime, timezone

import pytest

from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.services.attendance_service import (
    AttendanceCheckInStatus,
    AttendanceService,
)
from bot.services.session_service import SessionPrepareStatus, SessionService


GUILD_ID = "111"
ADMIN_ID = "9001"


def utc_dt(hour: int, minute: int, second: int = 0, day: int = 2) -> datetime:
    return datetime(2026, 7, day, hour, minute, second, tzinfo=timezone.utc)


async def configure_daily_2130(database):
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


async def create_member(
    member_repository,
    discord_id: str,
    display_name: str,
) -> int:
    return await member_repository.create(
        guild_id=GUILD_ID,
        discord_id=discord_id,
        display_name=display_name,
        created_by_discord_id=ADMIN_ID,
        now="2026-07-02T00:00:00+00:00",
    )


def build_services(database, guild_repository, member_repository):
    session_repository = SessionRepository(database=database)
    attendance_repository = AttendanceRepository(database=database)
    score_repository = ScoreRepository(database=database)
    session_service = SessionService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
    )
    attendance_service = AttendanceService(
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        session_service=session_service,
    )
    return (
        session_repository,
        attendance_repository,
        score_repository,
        session_service,
        attendance_service,
    )


async def count_rows(database, table: str) -> int:
    connection = await database.connect()
    try:
        cursor = await connection.execute(f"SELECT COUNT(*) AS count FROM {table};")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["count"])
    finally:
        await connection.close()


async def first_session_id(database) -> int:
    connection = await database.connect()
    try:
        cursor = await connection.execute(
            "SELECT id FROM attendance_sessions ORDER BY id LIMIT 1;"
        )
        row = await cursor.fetchone()
        await cursor.close()
        assert row is not None
        return int(row["id"])
    finally:
        await connection.close()


async def test_prepare_today_session_creates_snapshot_once(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    active_a = await create_member(member_repository, "2001", "A")
    active_b = await create_member(member_repository, "2002", "B")
    inactive = await create_member(member_repository, "2003", "C")
    await member_repository.deactivate(
        guild_id=GUILD_ID,
        discord_id="2003",
        display_name="C",
        now="2026-07-02T01:00:00+00:00",
    )

    session_repository, _, _, session_service, _ = build_services(
        database,
        guild_repository,
        member_repository,
    )

    result = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 0),
    )
    second_result = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 0),
    )

    assert result.status is SessionPrepareStatus.READY
    assert result.session["status"] == "SCHEDULED"
    assert second_result.session["id"] == result.session["id"]
    assert await count_rows(database, "attendance_sessions") == 1

    members = await session_repository.list_members_with_attendance(
        session_id=result.session["id"],
    )
    assert {row["member_id"] for row in members} == {active_a, active_b}
    assert inactive not in {row["member_id"] for row in members}


async def test_snapshot_excludes_late_registration_until_next_session(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")

    session_repository, _, _, session_service, _ = build_services(
        database,
        guild_repository,
        member_repository,
    )

    first = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 31),
    )
    late_member_id = await create_member(member_repository, "2002", "B")

    same_day = await session_repository.list_members_with_attendance(
        session_id=first.session["id"],
    )

    next_day = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 31, day=3),
    )
    next_day_members = await session_repository.list_members_with_attendance(
        session_id=next_day.session["id"],
    )

    assert late_member_id not in {row["member_id"] for row in same_day}
    assert late_member_id in {row["member_id"] for row in next_day_members}


async def test_prepare_today_session_operational_statuses(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    _, _, _, session_service, _ = build_services(
        database,
        guild_repository,
        member_repository,
    )

    no_members = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 0),
    )
    assert no_members.status is SessionPrepareStatus.NO_ACTIVE_MEMBERS

    connection = await database.connect()
    try:
        await connection.execute(
            "UPDATE guild_settings SET attendance_days = 'MON' WHERE guild_id = ?;",
            (GUILD_ID,),
        )
        await connection.commit()
    finally:
        await connection.close()

    not_day = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 0),
    )
    assert not_day.status is SessionPrepareStatus.NOT_ATTENDANCE_DAY

    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")
    already_closed = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 46),
    )
    assert already_closed.status is SessionPrepareStatus.ALREADY_CLOSED


async def test_scheduled_session_opens_after_start(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")
    _, _, _, session_service, _ = build_services(
        database,
        guild_repository,
        member_repository,
    )

    before_start = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 0),
    )
    after_start = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 31),
    )

    assert before_start.session["status"] == "SCHEDULED"
    assert after_start.session["status"] == "OPEN"
    assert after_start.session["opened_at"] == "2026-07-02T12:31:00+00:00"


async def test_present_check_in_creates_record_score_and_total(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    member_id = await create_member(member_repository, "2001", "A")
    _, attendance_repository, score_repository, _, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    result = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 30),
    )

    assert result.status is AttendanceCheckInStatus.PRESENT
    assert result.score_delta == 3
    assert result.total_score == 3

    record = await attendance_repository.get_by_session_and_member(
        session_id=await first_session_id(database),
        member_id=member_id,
    )
    assert record is not None
    assert record["status"] == "PRESENT"
    assert record["source"] == "USER"

    assert await score_repository.get_total_score(member_id=member_id) == 3

    connection = await database.connect()
    try:
        event = (
            await connection.execute_fetchall(
                """
                SELECT event_type, delta, reference_type, reference_id, dedup_key
                FROM score_events
                WHERE member_id = ?;
                """,
                (member_id,),
            )
        )[0]
    finally:
        await connection.close()

    assert event["event_type"] == "ATTENDANCE_PRESENT"
    assert event["delta"] == 3
    assert event["reference_type"] == "ATTENDANCE"
    assert event["reference_id"] == record["id"]
    assert event["dedup_key"] == f"attendance:{record['id']}"


async def test_late_check_in_creates_late_score(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")
    _, _, _, _, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    result = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 40),
    )

    assert result.status is AttendanceCheckInStatus.LATE
    assert result.score_delta == 1
    assert result.total_score == 1


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (utc_dt(12, 29, 59), AttendanceCheckInStatus.NOT_OPEN),
        (utc_dt(12, 30), AttendanceCheckInStatus.PRESENT),
        (utc_dt(12, 39, 59), AttendanceCheckInStatus.PRESENT),
        (utc_dt(12, 40), AttendanceCheckInStatus.LATE),
        (utc_dt(12, 44, 59), AttendanceCheckInStatus.LATE),
        (utc_dt(12, 45), AttendanceCheckInStatus.CLOSED),
    ],
)
async def test_check_in_boundaries(
    database,
    guild_repository,
    member_repository,
    now,
    expected,
):
    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")
    _, _, _, _, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    result = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=now,
    )

    assert result.status is expected


async def test_duplicate_check_in_does_not_duplicate_record_or_score(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")
    _, _, _, _, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    first = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 30),
    )
    second = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 31),
    )

    assert first.status is AttendanceCheckInStatus.PRESENT
    assert second.status is AttendanceCheckInStatus.ALREADY_CHECKED
    assert second.total_score == 3
    assert await count_rows(database, "attendance_records") == 1
    assert await count_rows(database, "score_events") == 1


async def test_check_in_rejects_not_registered_and_not_session_member(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")
    _, _, _, session_service, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    not_registered = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="9999",
        now=utc_dt(12, 30),
    )
    assert not_registered.status is AttendanceCheckInStatus.NOT_REGISTERED

    await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 30),
    )
    await create_member(member_repository, "2002", "B")

    not_session_member = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2002",
        now=utc_dt(12, 31),
    )
    assert not_session_member.status is AttendanceCheckInStatus.NOT_SESSION_MEMBER


async def test_cancelled_and_closed_sessions_are_rejected(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")
    _, _, _, session_service, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    prepared = await session_service.prepare_today_session(
        guild_id=GUILD_ID,
        now=utc_dt(12, 30),
    )
    connection = await database.connect()
    try:
        await connection.execute(
            """
            UPDATE attendance_sessions
            SET status = 'CANCELLED', cancel_reason = '휴무'
            WHERE id = ?;
            """,
            (prepared.session["id"],),
        )
        await connection.commit()
    finally:
        await connection.close()

    cancelled = await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 31),
    )
    assert cancelled.status is AttendanceCheckInStatus.CANCELLED
    assert cancelled.cancel_reason == "휴무"


async def test_transaction_rolls_back_when_score_creation_fails(
    database,
    guild_repository,
    member_repository,
    monkeypatch,
):
    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")
    _, _, score_repository, _, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    async def fail_score_event(**kwargs):
        raise RuntimeError("forced score failure")

    monkeypatch.setattr(
        score_repository,
        "create_attendance_event",
        fail_score_event,
    )

    with pytest.raises(RuntimeError):
        await attendance_service.check_in(
            guild_id=GUILD_ID,
            discord_id="2001",
            now=utc_dt(12, 30),
        )

    assert await count_rows(database, "attendance_records") == 0
    assert await count_rows(database, "score_events") == 0


async def test_today_status_groups_present_late_and_unchecked(
    database,
    guild_repository,
    member_repository,
):
    await configure_daily_2130(database)
    await create_member(member_repository, "2001", "A")
    await create_member(member_repository, "2002", "B")
    await create_member(member_repository, "2003", "C")
    await create_member(member_repository, "2004", "D")
    _, _, _, _, attendance_service = build_services(
        database,
        guild_repository,
        member_repository,
    )

    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2001",
        now=utc_dt(12, 30),
    )
    await attendance_service.check_in(
        guild_id=GUILD_ID,
        discord_id="2002",
        now=utc_dt(12, 40),
    )
    result = await attendance_service.get_today_status(
        guild_id=GUILD_ID,
        now=utc_dt(12, 41),
    )

    assert len(result.present) == 1
    assert len(result.late) == 1
    assert len(result.unchecked) == 2
    assert result.total_count == 4
    assert result.checked_count == 2
    assert {member.discord_id for member in result.unchecked} == {"2003", "2004"}
