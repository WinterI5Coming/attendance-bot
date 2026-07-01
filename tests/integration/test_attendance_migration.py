"""Integration tests for the attendance core migration."""

from datetime import datetime, timezone

import aiosqlite
import pytest

from bot.db.database import Database


GUILD_ID = "migration-guild"
OTHER_GUILD_ID = "other-guild"
NOW = "2026-07-02T12:00:00+00:00"
START_AT = "2026-07-02T12:30:00+00:00"
LATE_AT = "2026-07-02T12:40:00+00:00"
CLOSE_AT = "2026-07-02T12:45:00+00:00"


@pytest.fixture
async def migrated_database(tmp_path):
    db = Database(tmp_path / "attendance_migration.db")
    await db.initialize()
    await db.initialize()
    return db


async def _seed_guild(connection: aiosqlite.Connection, guild_id: str) -> None:
    await connection.execute(
        """
        INSERT INTO guild_settings (
            guild_id,
            timezone,
            attendance_days,
            attendance_start,
            late_deadline,
            close_deadline,
            excuse_mode,
            created_at,
            updated_at
        )
        VALUES (?, 'Asia/Seoul', 'MON,TUE,WED,THU,FRI', '21:30', '21:40',
            '21:45', 'officer_approval', ?, ?);
        """,
        (guild_id, NOW, NOW),
    )


async def _seed_member(
    connection: aiosqlite.Connection,
    *,
    guild_id: str = GUILD_ID,
    discord_id: str = "member-1",
) -> int:
    cursor = await connection.execute(
        """
        INSERT INTO members (
            guild_id,
            discord_id,
            display_name,
            is_active,
            activated_at,
            created_by_discord_id,
            updated_at
        )
        VALUES (?, ?, 'Member One', 1, ?, 'admin-1', ?);
        """,
        (guild_id, discord_id, NOW, NOW),
    )

    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def _seed_session(connection: aiosqlite.Connection) -> int:
    cursor = await connection.execute(
        """
        INSERT INTO attendance_sessions (
            guild_id,
            attendance_date,
            start_at,
            late_at,
            close_at,
            status,
            created_at,
            updated_at
        )
        VALUES (?, '2026-07-02', ?, ?, ?, 'SCHEDULED', ?, ?);
        """,
        (GUILD_ID, START_AT, LATE_AT, CLOSE_AT, NOW, NOW),
    )

    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def test_attendance_tables_exist_after_migration(migrated_database):
    connection = await migrated_database.connect()

    try:
        cursor = await connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name;
            """
        )
        rows = await cursor.fetchall()
        table_names = {row["name"] for row in rows}

        assert {
            "attendance_sessions",
            "attendance_session_members",
            "attendance_records",
            "score_events",
            "audit_logs",
            "excuse_requests",
            "evaluations",
        }.issubset(table_names)

        migration_rows = await connection.execute_fetchall(
            """
            SELECT version
            FROM schema_migrations
            ORDER BY version;
            """
        )
        assert [row["version"] for row in migration_rows] == [1, 2, 3, 4]
    finally:
        await connection.close()


async def test_attendance_migration_constraints(migrated_database):
    connection = await migrated_database.connect()

    try:
        await _seed_guild(connection, GUILD_ID)
        await _seed_guild(connection, OTHER_GUILD_ID)
        member_id = await _seed_member(connection)
        await connection.commit()

        session_id = await _seed_session(connection)
        await connection.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await _seed_session(connection)

        await connection.rollback()

        await connection.execute(
            """
            INSERT INTO attendance_session_members (
                session_id,
                member_id,
                included_at
            )
            VALUES (?, ?, ?);
            """,
            (session_id, member_id, NOW),
        )
        await connection.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO attendance_session_members (
                    session_id,
                    member_id,
                    included_at
                )
                VALUES (?, ?, ?);
                """,
                (session_id, member_id, NOW),
            )

        await connection.rollback()

        await connection.execute(
            """
            INSERT INTO attendance_records (
                session_id,
                member_id,
                status,
                source,
                created_at,
                updated_at
            )
            VALUES (?, ?, 'PRESENT', 'USER', ?, ?);
            """,
            (session_id, member_id, NOW, NOW),
        )
        await connection.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO attendance_records (
                    session_id,
                    member_id,
                    status,
                    source,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, 'LATE', 'USER', ?, ?);
                """,
                (session_id, member_id, NOW, NOW),
            )

        await connection.rollback()

        await connection.execute(
            """
            INSERT INTO score_events (
                guild_id,
                member_id,
                event_type,
                delta,
                reference_type,
                reference_id,
                dedup_key,
                description,
                created_at
            )
            VALUES (?, ?, 'attendance', 3, 'attendance_record', 1,
                'attendance:1', 'Attendance score', ?);
            """,
            (GUILD_ID, member_id, NOW),
        )
        await connection.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO score_events (
                    guild_id,
                    member_id,
                    event_type,
                    delta,
                    dedup_key,
                    description,
                    created_at
                )
                VALUES (?, ?, 'attendance', 3, 'attendance:1',
                    'Duplicate score', ?);
                """,
                (GUILD_ID, member_id, NOW),
            )

        await connection.rollback()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO attendance_sessions (
                    guild_id,
                    attendance_date,
                    start_at,
                    late_at,
                    close_at,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, '2026-07-03', ?, ?, ?, 'BROKEN', ?, ?);
                """,
                (GUILD_ID, START_AT, LATE_AT, CLOSE_AT, NOW, NOW),
            )

        await connection.rollback()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO attendance_records (
                    session_id,
                    member_id,
                    status,
                    source,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, 'BROKEN', 'USER', ?, ?);
                """,
                (session_id, member_id, NOW, NOW),
            )

        await connection.rollback()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO attendance_records (
                    session_id,
                    member_id,
                    status,
                    source,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, 'LATE', 'BROKEN', ?, ?);
                """,
                (session_id, member_id, NOW, NOW),
            )

        await connection.rollback()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO attendance_sessions (
                    guild_id,
                    attendance_date,
                    start_at,
                    late_at,
                    close_at,
                    status,
                    created_at,
                    updated_at
                )
                VALUES ('missing-guild', '2026-07-04', ?, ?, ?,
                    'SCHEDULED', ?, ?);
                """,
                (START_AT, LATE_AT, CLOSE_AT, NOW, NOW),
            )

        await connection.rollback()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO attendance_session_members (
                    session_id,
                    member_id,
                    included_at
                )
                VALUES (?, 999999, ?);
                """,
                (session_id, NOW),
            )

        await connection.rollback()

        await connection.execute(
            """
            INSERT INTO excuse_requests (
                guild_id,
                member_id,
                target_date,
                reason,
                expected_time,
                status,
                requested_at
            )
            VALUES (?, ?, '2026-07-03', 'Family schedule', '21:35',
                'PENDING', ?);
            """,
            (GUILD_ID, member_id, NOW),
        )
        await connection.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO excuse_requests (
                    guild_id,
                    member_id,
                    target_date,
                    reason,
                    status,
                    requested_at
                )
                VALUES (?, ?, '2026-07-03', 'Duplicate active request',
                    'APPROVED', ?);
                """,
                (GUILD_ID, member_id, NOW),
            )

        await connection.rollback()

        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """
                INSERT INTO excuse_requests (
                    guild_id,
                    member_id,
                    target_date,
                    reason,
                    status,
                    requested_at
                )
                VALUES (?, ?, '2026-07-04', 'Broken status', 'BROKEN', ?);
                """,
                (GUILD_ID, member_id, NOW),
            )
    finally:
        await connection.close()


def test_test_constants_use_timezone_aware_utc_strings():
    parsed = datetime.fromisoformat(NOW)

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)
