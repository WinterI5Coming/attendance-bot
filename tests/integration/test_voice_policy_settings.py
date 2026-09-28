"""`/설정 음성검증`으로 바꾼 정책이 새 세션에 반영되는지 확인한다."""

from unittest.mock import AsyncMock, MagicMock

import discord
from tests.helpers import (
    ADMIN_ID,
    GUILD_ID,
    configure_guild,
    count_rows,
    create_member,
    utc_dt,
)

from bot.cogs.settings import SettingsCog
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.runtime.time_provider import TimeProvider
from bot.services.admin_service import AdminService, SettingsUpdateStatus
from bot.services.guild_service import GuildService
from bot.services.session_service import SessionService


def build_admin_service(database, guild_repository):
    return AdminService(
        guild_repository=guild_repository,
        session_repository=SessionRepository(database=database),
        score_repository=ScoreRepository(database=database),
        audit_repository=AuditRepository(database=database),
    )


async def test_voice_policy_update_is_atomic_and_applies_to_new_sessions(
    database, guild_repository, member_repository
):
    await configure_guild(database)
    await create_member(member_repository, "2001", "A")
    admin_service = build_admin_service(database, guild_repository)

    result = await admin_service.update_voice_verification(
        guild_id=GUILD_ID,
        enabled=True,
        voice_channel_id="777",
        voice_category_id=None,
        required_minutes=30,
        verification_end_time="22:30",
        early_leave_penalty=0,
        no_participation_penalty=-5,
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(1, 0),
    )
    assert result.status is SettingsUpdateStatus.UPDATED
    assert result.settings["voice_required_minutes"] == 30
    assert await count_rows(database, "audit_logs") == 1

    session_service = SessionService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=SessionRepository(database=database),
    )
    prepared = await session_service.prepare_today_session(guild_id=GUILD_ID, now=utc_dt(12, 0))
    session = prepared.session

    assert session["required_voice_seconds"] == 30 * 60
    assert session["early_leave_penalty"] == 0
    assert session["no_participation_penalty"] == -5
    # 22:30 KST 마감 설정이지만 세션 마감(21:45)+30분=22:15보다 늦으므로 그대로 22:30 KST = 13:30 UTC
    assert session["verification_end_at"] == utc_dt(13, 30).isoformat()


async def test_voice_policy_update_rejects_invalid_values_without_partial_write(
    database, guild_repository
):
    admin_service = build_admin_service(database, guild_repository)

    result = await admin_service.update_voice_verification(
        guild_id=GUILD_ID,
        enabled=True,
        voice_channel_id="777",
        voice_category_id=None,
        required_minutes=None,
        verification_end_time="25:99",
        early_leave_penalty=None,
        no_participation_penalty=None,
        actor_discord_id=ADMIN_ID,
        has_permission=True,
        now=utc_dt(1, 0),
    )
    settings = await guild_repository.get_by_guild_id(GUILD_ID)

    assert result.status is SettingsUpdateStatus.INVALID_VALUE
    assert result.field == "voice_verification_end_time"
    # 앞 항목(사용 여부, 채널)도 저장되지 않아야 한다.
    assert settings["voice_verification_enabled"] == 0
    assert not settings["voice_channel_ids"]
    assert await count_rows(database, "audit_logs") == 0


async def test_settings_cog_voice_command_replies_with_saved_policy(
    database, guild_repository
):
    """Cog 핸들러가 서비스 결과를 사용자 응답으로 바꾸는지 확인한다."""

    admin_service = build_admin_service(database, guild_repository)
    cog = SettingsCog(
        admin_service=admin_service,
        guild_service=GuildService(repository=guild_repository, settings=MagicMock()),
        time_provider=TimeProvider(),
    )
    interaction = MagicMock()
    interaction.guild.id = int(GUILD_ID)
    interaction.guild.owner_id = 0
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = int(ADMIN_ID)
    interaction.user.guild_permissions.administrator = True
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    voice_channel = MagicMock()
    voice_channel.id = 777

    await cog.configure_voice_verification.callback(
        cog,
        interaction,
        True,
        voice_channel,
        None,
        45,
        "23:30",
        None,
        None,
    )

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    field_values = {field.name: field.value for field in embed.fields}
    assert field_values["요구 시간"] == "45분"
    assert field_values["검증 마감"] == "23:30"
    assert "<#777>" in field_values["대상"]
