"""여러 Cog가 공유하는 권한 확인과 표준 안내 응답.

슬래시 명령은 대부분 "서버 안에서 실행됐는가 → 초기설정이 있는가 →
간부/관리자 권한이 있는가" 순서로 검사한다. 각 Cog가 같은 검사를 따로
구현하지 않도록 여기에서 한 번만 정의하고, 실패 시 사용자에게 보여 줄
문구도 한 곳에서 관리한다.
"""

from __future__ import annotations

from typing import Any

import discord

from bot.services.guild_service import GuildService
from bot.utils.permissions import has_officer_permission, is_server_admin

GUILD_ONLY_MESSAGE = "🚫 이 명령어는 Discord 서버에서만 사용할 수 있습니다."
NOT_CONFIGURED_MESSAGE = (
    "⚙️ 아직 초기설정이 완료되지 않았습니다. 먼저 /설정 초기화를 실행해주세요."
)
OFFICER_ONLY_MESSAGE = "🚫 간부 또는 서버 관리자만 사용할 수 있는 명령어입니다."
ADMIN_ONLY_MESSAGE = "🚫 서버 소유자 또는 관리자만 사용할 수 있는 명령어입니다."


async def send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    """아직 응답하지 않았으면 초기 응답으로, 이미 defer했다면 followup으로 보낸다."""

    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


async def require_guild(interaction: discord.Interaction) -> discord.Guild | None:
    """서버 안에서 실행된 명령인지 확인하고, 아니면 안내 후 ``None``을 반환한다."""

    if interaction.guild is None:
        await send_ephemeral(interaction, GUILD_ONLY_MESSAGE)
        return None
    return interaction.guild


async def require_guild_settings(
    interaction: discord.Interaction,
    guild_service: GuildService,
) -> dict[str, Any] | None:
    """서버 초기설정을 조회하고, 서버 밖이거나 미설정이면 안내 후 ``None``을 반환한다."""

    guild = await require_guild(interaction)
    if guild is None:
        return None
    settings = await guild_service.get_settings(guild.id)
    if settings is None:
        await send_ephemeral(interaction, NOT_CONFIGURED_MESSAGE)
        return None
    return settings


async def has_officer_access(
    interaction: discord.Interaction,
    guild_service: GuildService,
) -> bool:
    """응답 없이 간부/관리자 권한 여부만 반환한다.

    서비스가 ``has_permission`` 인자를 받아 스스로 PERMISSION_DENIED 결과를
    만드는 명령에서 사용한다.
    """

    guild = interaction.guild
    if guild is None:
        return False
    settings = await guild_service.get_settings(guild.id)
    if settings is None:
        return False
    return has_officer_permission(interaction, settings["officer_role_id"])


async def require_officer(
    interaction: discord.Interaction,
    guild_service: GuildService,
) -> dict[str, Any] | None:
    """간부/관리자 권한을 요구하고, 통과하면 서버 설정을 반환한다.

    서버 밖 실행, 미설정, 권한 부족 순서로 검사하며 실패 사유를 사용자에게
    안내한 뒤 ``None``을 반환한다.
    """

    settings = await require_guild_settings(interaction, guild_service)
    if settings is None:
        return None
    if not has_officer_permission(interaction, settings["officer_role_id"]):
        await send_ephemeral(interaction, OFFICER_ONLY_MESSAGE)
        return None
    return settings


async def require_server_admin(
    interaction: discord.Interaction,
) -> discord.Guild | None:
    """서버 소유자/관리자 권한을 요구하고, 통과하면 서버 객체를 반환한다."""

    guild = await require_guild(interaction)
    if guild is None:
        return None
    if not is_server_admin(interaction):
        await send_ephemeral(interaction, ADMIN_ONLY_MESSAGE)
        return None
    return guild
