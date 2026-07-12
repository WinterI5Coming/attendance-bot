"""Integration tests for Stage C seasons, achievements, and officer reviews."""

from datetime import datetime, timezone

import pytest

from bot.db.database import Database
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.stage_c_repository import StageCRepository
from bot.services.stage_c_service import (
    AchievementService,
    OfficerReviewService,
    SeasonService,
)


GUILD_ID = "stage-c-guild"
NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
NOW_TEXT = NOW.isoformat()


@pytest.fixture
async def services(tmp_path):
    database = Database(tmp_path / "stage_c.db")
    await database.initialize()
    guild_repository = GuildRepository(database=database)
    member_repository = MemberRepository(database=database)
    stage_c_repository = StageCRepository(database=database)
    season_service = SeasonService(
        guild_repository=guild_repository,
        repository=stage_c_repository,
    )
    achievement_service = AchievementService(
        member_repository=member_repository,
        repository=stage_c_repository,
        season_service=season_service,
    )
    officer_review_service = OfficerReviewService(
        guild_repository=guild_repository,
        repository=stage_c_repository,
        season_service=season_service,
    )
    await _seed_base(database)
    return {
        "database": database,
        "season_service": season_service,
        "achievement_service": achievement_service,
        "officer_review_service": officer_review_service,
        "stage_c_repository": stage_c_repository,
    }


async def test_season_reconcile_and_ranking_include_stage_b_effective_counts(services):
    season_service = services["season_service"]

    season_id = await season_service.create_season(
        guild_id=GUILD_ID,
        name="2026 Summer",
        start_date="2026-07-01",
        end_date="2026-07-31",
        created_by_discord_id="admin",
        now=NOW,
    )
    assert await season_service.start_season(
        guild_id=GUILD_ID,
        season_id=season_id,
        now=NOW,
    )

    count = await season_service.reconcile_season(
        guild_id=GUILD_ID,
        season_id=season_id,
        now=NOW,
    )
    assert count == 2

    ranking = await season_service.get_ranking(
        guild_id=GUILD_ID,
        season_id=season_id,
    )
    assert ranking.configured
    assert ranking.season is not None
    assert ranking.entries is not None
    assert ranking.entries[0].discord_id == "member-1"
    assert ranking.entries[0].attendance_rate == 100.0
    assert ranking.entries[1].discord_id == "member-2"
    assert ranking.entries[1].attendance_rate == 0.0


async def test_achievement_evaluation_awards_score_and_title_once(services):
    season_service = services["season_service"]
    achievement_service = services["achievement_service"]

    season_id = await season_service.create_season(
        guild_id=GUILD_ID,
        name="2026 Awards",
        start_date="2026-07-01",
        end_date="2026-07-31",
        created_by_discord_id="admin",
        now=NOW,
    )
    await season_service.start_season(guild_id=GUILD_ID, season_id=season_id, now=NOW)

    first_result = await achievement_service.evaluate_season(
        guild_id=GUILD_ID,
        season_id=season_id,
        created_by_discord_id="admin",
        now=NOW,
    )
    second_result = await achievement_service.evaluate_season(
        guild_id=GUILD_ID,
        season_id=season_id,
        created_by_discord_id="admin",
        now=NOW,
    )

    assert first_result.awarded_count >= 1
    assert second_result.awarded_count == 0

    achievements = await achievement_service.list_member_achievements(
        guild_id=GUILD_ID,
        discord_id="member-1",
    )
    assert {row["code"] for row in achievements} >= {"FIRST_PRESENT"}

    titles = await achievement_service.list_member_titles(
        guild_id=GUILD_ID,
        discord_id="member-1",
    )
    assert any(row["title_name"] == "First Check-in" for row in titles)


async def test_officer_review_preview_is_stored_without_role_change_logs(services):
    season_service = services["season_service"]
    officer_review_service = services["officer_review_service"]
    stage_c_repository = services["stage_c_repository"]

    season_id = await season_service.create_season(
        guild_id=GUILD_ID,
        name="2026 Officers",
        start_date="2026-07-01",
        end_date="2026-07-31",
        created_by_discord_id="admin",
        now=NOW,
    )
    await season_service.start_season(guild_id=GUILD_ID, season_id=season_id, now=NOW)
    await officer_review_service.update_settings(
        guild_id=GUILD_ID,
        values={
            "enabled": 1,
            "minimum_sessions": 1,
            "promotion_threshold": 70,
            "retention_threshold": 50,
            "officer_capacity": 1,
        },
        now=NOW,
    )

    result = await officer_review_service.create_preview(
        guild_id=GUILD_ID,
        season_id=season_id,
        current_officer_discord_ids=set(),
        protected_discord_ids=set(),
        created_by_discord_id="admin",
        now=NOW,
    )

    assert result.configured
    assert result.enabled
    assert result.review_id is not None
    assert result.candidates is not None
    assert result.candidates[0].action == "PROMOTE"
    assert result.candidates[0].discord_id == "member-1"

    logs = await stage_c_repository.list_role_change_logs(
        guild_id=GUILD_ID,
        limit=10,
    )
    assert logs == []


async def _seed_base(database: Database) -> None:
    connection = await database.connect()
    try:
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
                officer_role_id,
                attendance_channel_id,
                announcement_channel_id,
                exempt_absence_counts_in_attendance_denominator,
                created_at,
                updated_at
            )
            VALUES (?, 'Asia/Seoul', 'MON,TUE,WED,THU,FRI', '21:30', '21:40',
                '21:45', 'officer_approval', '9001', '1001', '1002', 0, ?, ?);
            """,
            (GUILD_ID, NOW_TEXT, NOW_TEXT),
        )
        member_1 = await _insert_member(connection, "member-1", "Member One")
        member_2 = await _insert_member(connection, "member-2", "Member Two")
        session_id = await _insert_session(connection)
        for member_id in (member_1, member_2):
            await connection.execute(
                """
                INSERT INTO attendance_session_members (
                    session_id,
                    member_id,
                    included_at
                )
                VALUES (?, ?, ?);
                """,
                (session_id, member_id, NOW_TEXT),
            )
        await _insert_record(connection, session_id, member_1, "PRESENT")
        await _insert_record(connection, session_id, member_2, "ABSENT")
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
            VALUES (?, ?, 'ATTENDANCE_PRESENT', 3, 'ATTENDANCE', 1,
                'attendance:member-1', 'Attendance', ?);
            """,
            (GUILD_ID, member_1, NOW_TEXT),
        )
        await connection.commit()
    except Exception:
        await connection.rollback()
        raise
    finally:
        await connection.close()


async def _insert_member(connection, discord_id: str, display_name: str) -> int:
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
        VALUES (?, ?, ?, 1, ?, 'admin', ?);
        """,
        (GUILD_ID, discord_id, display_name, NOW_TEXT, NOW_TEXT),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def _insert_session(connection) -> int:
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
        VALUES (?, '2026-07-02', ?, ?, ?, 'CLOSED', ?, ?);
        """,
        (
            GUILD_ID,
            "2026-07-02T12:30:00+00:00",
            "2026-07-02T12:40:00+00:00",
            "2026-07-02T12:45:00+00:00",
            NOW_TEXT,
            NOW_TEXT,
        ),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def _insert_record(
    connection,
    session_id: int,
    member_id: int,
    status: str,
) -> None:
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
        VALUES (?, ?, ?, 'AUTO', ?, ?);
        """,
        (session_id, member_id, status, NOW_TEXT, NOW_TEXT),
    )
