"""MemberRepository가 실제 SQLite에 대해 올바르게 동작하는지 검증한다."""

import aiosqlite
import pytest

GUILD_ID = "111"
DISCORD_ID = "2001"
CREATED_BY = "9001"


async def test_create_returns_new_row_id(member_repository):
    """신규 대원을 INSERT하면 생성된 행의 id가 반환되어야 한다."""

    member_id = await member_repository.create(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=CREATED_BY,
        now="2026-01-01T00:00:00+00:00",
    )

    assert isinstance(member_id, int)

    row = await member_repository.get_by_discord_id(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
    )

    assert row is not None
    assert row["id"] == member_id
    assert row["is_active"] == 1
    assert row["deactivated_at"] is None


async def test_duplicate_insert_violates_unique_constraint(member_repository):
    """같은 guild_id, discord_id로 두 번 INSERT하면 제약 위반이 발생해야 한다."""

    await member_repository.create(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=CREATED_BY,
        now="2026-01-01T00:00:00+00:00",
    )

    with pytest.raises(aiosqlite.IntegrityError):
        await member_repository.create(
            guild_id=GUILD_ID,
            discord_id=DISCORD_ID,
            display_name="테스터2",
            created_by_discord_id=CREATED_BY,
            now="2026-01-01T00:00:01+00:00",
        )


async def test_deactivate_then_reactivate_preserves_row(member_repository):
    """비활성화 후 재활성화해도 같은 행이 재사용되어야 한다."""

    member_id = await member_repository.create(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=CREATED_BY,
        now="2026-01-01T00:00:00+00:00",
    )

    await member_repository.deactivate(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        now="2026-01-02T00:00:00+00:00",
    )

    deactivated_row = await member_repository.get_by_discord_id(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
    )

    assert deactivated_row["is_active"] == 0
    assert deactivated_row["deactivated_at"] is not None

    reactivated_id = await member_repository.reactivate(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        now="2026-01-03T00:00:00+00:00",
    )

    assert reactivated_id == member_id

    reactivated_row = await member_repository.get_by_discord_id(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
    )

    assert reactivated_row["is_active"] == 1
    assert reactivated_row["deactivated_at"] is None


async def test_list_active_excludes_deactivated(member_repository):
    """활성 대원 목록에는 비활성화된 대원이 포함되지 않아야 한다."""

    await member_repository.create(
        guild_id=GUILD_ID,
        discord_id="3001",
        display_name="가",
        created_by_discord_id=CREATED_BY,
        now="2026-01-01T00:00:00+00:00",
    )
    await member_repository.create(
        guild_id=GUILD_ID,
        discord_id="3002",
        display_name="나",
        created_by_discord_id=CREATED_BY,
        now="2026-01-01T00:00:00+00:00",
    )
    await member_repository.deactivate(
        guild_id=GUILD_ID,
        discord_id="3002",
        display_name="나",
        now="2026-01-02T00:00:00+00:00",
    )

    rows = await member_repository.list_active(guild_id=GUILD_ID)

    assert [row["discord_id"] for row in rows] == ["3001"]
