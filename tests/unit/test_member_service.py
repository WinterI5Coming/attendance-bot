"""MemberService의 등록/제외/조회 비즈니스 규칙을 검증한다."""

import pytest

from bot.services.member_service import (
    BotRegistrationError,
    InvalidDeactivationReasonError,
    MemberDeactivationOutcome,
    MemberRegistrationOutcome,
)

GUILD_ID = 111
OTHER_GUILD_ID = 222
DISCORD_ID = 1001
ACTOR_ID = 9001


async def test_register_new_member_creates_row(member_service, member_repository):
    """등록된 적 없는 사용자를 등록하면 CREATED와 함께 활성 행이 생성된다."""

    result = await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    assert result.outcome is MemberRegistrationOutcome.CREATED

    row = await member_repository.get_by_discord_id(
        guild_id=str(GUILD_ID),
        discord_id=str(DISCORD_ID),
    )

    assert row is not None
    assert row["is_active"] == 1
    assert row["deactivated_at"] is None


async def test_register_already_active_member_is_noop(member_service, member_repository):
    """이미 활성 상태인 사용자를 다시 등록하면 행이 늘어나지 않아야 한다."""

    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    result = await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    assert result.outcome is MemberRegistrationOutcome.ALREADY_ACTIVE

    rows = await member_repository.list_active(guild_id=str(GUILD_ID))
    assert len(rows) == 1


async def test_reregister_deactivated_member_reactivates_existing_row(
    member_service, member_repository
):
    """제외된 대원을 다시 등록하면 기존 행이 재활성화되어야 한다."""

    register_result = await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    await member_service.deactivate_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        reason="테스트 제외",
        actor_discord_id=ACTOR_ID,
    )

    result = await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    assert result.outcome is MemberRegistrationOutcome.REACTIVATED
    assert result.member_id == register_result.member_id

    row = await member_repository.get_by_discord_id(
        guild_id=str(GUILD_ID),
        discord_id=str(DISCORD_ID),
    )

    assert row["is_active"] == 1
    assert row["deactivated_at"] is None


async def test_register_bot_account_is_rejected(member_service, member_repository):
    """봇 계정은 등록할 수 없고 DB 행도 생성되지 않아야 한다."""

    with pytest.raises(BotRegistrationError):
        await member_service.register_member(
            guild_id=GUILD_ID,
            discord_id=DISCORD_ID,
            display_name="봇",
            created_by_discord_id=ACTOR_ID,
            is_bot=True,
        )

    row = await member_repository.get_by_discord_id(
        guild_id=str(GUILD_ID),
        discord_id=str(DISCORD_ID),
    )

    assert row is None


async def test_deactivate_unregistered_member_returns_not_found(member_service):
    """등록된 적 없는 사용자를 제외하면 NOT_FOUND를 반환해야 한다."""

    result = await member_service.deactivate_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        reason="테스트 제외",
        actor_discord_id=ACTOR_ID,
    )

    assert result.outcome is MemberDeactivationOutcome.NOT_FOUND


async def test_deactivate_already_inactive_member_is_noop(member_service, member_repository):
    """이미 제외된 대원을 다시 제외해도 행 개수가 늘어나지 않아야 한다."""

    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )
    await member_service.deactivate_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        reason="첫 제외",
        actor_discord_id=ACTOR_ID,
    )

    result = await member_service.deactivate_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        reason="두번째 제외",
        actor_discord_id=ACTOR_ID,
    )

    assert result.outcome is MemberDeactivationOutcome.ALREADY_INACTIVE

    active_rows = await member_repository.list_active(guild_id=str(GUILD_ID))
    assert len(active_rows) == 0


async def test_list_active_members_excludes_inactive(member_service):
    """활성 대원 목록 조회 시 비활성 대원은 제외되어야 한다."""

    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=1001,
        display_name="가나다",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )
    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=1002,
        display_name="라마바",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )
    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=1003,
        display_name="비활성",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )
    await member_service.deactivate_member(
        guild_id=GUILD_ID,
        discord_id=1003,
        display_name="비활성",
        reason="테스트 제외",
        actor_discord_id=ACTOR_ID,
    )

    members = await member_service.list_active_members(guild_id=GUILD_ID)

    assert len(members) == 2
    assert {member["discord_id"] for member in members} == {"1001", "1002"}


async def test_register_deactivate_register_keeps_single_row(
    member_service, member_repository
):
    """등록, 제외, 재등록을 반복해도 행은 정확히 하나여야 한다."""

    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )
    await member_service.deactivate_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        reason="테스트 제외",
        actor_discord_id=ACTOR_ID,
    )
    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    row = await member_repository.get_by_discord_id(
        guild_id=str(GUILD_ID),
        discord_id=str(DISCORD_ID),
    )

    assert row is not None
    assert row["is_active"] == 1


async def test_same_discord_id_across_guilds_creates_separate_rows(
    member_service, member_repository
):
    """서로 다른 서버에서는 같은 discord_id도 별도 행으로 저장되어야 한다."""

    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )
    await member_service.register_member(
        guild_id=OTHER_GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    row_in_first_guild = await member_repository.get_by_discord_id(
        guild_id=str(GUILD_ID),
        discord_id=str(DISCORD_ID),
    )
    row_in_second_guild = await member_repository.get_by_discord_id(
        guild_id=str(OTHER_GUILD_ID),
        discord_id=str(DISCORD_ID),
    )

    assert row_in_first_guild is not None
    assert row_in_second_guild is not None
    assert row_in_first_guild["id"] != row_in_second_guild["id"]


async def test_reregister_updates_display_name(member_service, member_repository):
    """재등록 시 display_name이 최신 값으로 갱신되어야 한다."""

    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="옛날이름",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )
    await member_service.deactivate_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="옛날이름",
        reason="테스트 제외",
        actor_discord_id=ACTOR_ID,
    )
    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="새이름",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    row = await member_repository.get_by_discord_id(
        guild_id=str(GUILD_ID),
        discord_id=str(DISCORD_ID),
    )

    assert row["display_name"] == "새이름"


async def test_deactivation_reason_too_short_raises(member_service):
    """공백 제거 후 2자 미만인 제외 사유는 거절되어야 한다."""

    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    with pytest.raises(InvalidDeactivationReasonError):
        await member_service.deactivate_member(
            guild_id=GUILD_ID,
            discord_id=DISCORD_ID,
            display_name="테스터",
            reason=" a ",
            actor_discord_id=ACTOR_ID,
        )


async def test_deactivation_reason_too_long_raises(member_service):
    """200자를 초과하는 제외 사유는 거절되어야 한다."""

    await member_service.register_member(
        guild_id=GUILD_ID,
        discord_id=DISCORD_ID,
        display_name="테스터",
        created_by_discord_id=ACTOR_ID,
        is_bot=False,
    )

    with pytest.raises(InvalidDeactivationReasonError):
        await member_service.deactivate_member(
            guild_id=GUILD_ID,
            discord_id=DISCORD_ID,
            display_name="테스터",
            reason="가" * 201,
            actor_discord_id=ACTOR_ID,
        )
