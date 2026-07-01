"""출석 대상 대원의 등록, 제외, 조회 슬래시 명령어를 제공한다."""

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.guild_service import GuildService
from bot.services.member_service import (
    BotRegistrationError,
    InvalidDeactivationReasonError,
    MemberDeactivationOutcome,
    MemberRegistrationOutcome,
    MemberService,
)
from bot.utils.permissions import has_officer_permission


logger = logging.getLogger(__name__)


class MembersCog(commands.Cog):
    """대원 등록, 제외, 목록 조회 관련 슬래시 명령어."""

    def __init__(
        self,
        guild_service: GuildService,
        member_service: MemberService,
    ) -> None:
        """Cog에 서버 설정 Service와 대원 Service를 주입한다."""

        self.guild_service = guild_service
        self.member_service = member_service

    async def _get_guild_settings_or_notify(
        self,
        interaction: discord.Interaction,
    ) -> dict[str, Any] | None:
        """현재 서버의 초기설정을 조회하고, 없으면 안내 메시지를 보낸다.

        Args:
            interaction:
                Discord 명령 실행 정보.

        Returns:
            초기설정이 있으면 설정 딕셔너리, 없으면 None.
        """

        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "🚫 이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return None

        settings = await self.guild_service.get_settings(guild.id)

        if settings is None:
            await interaction.response.send_message(
                "⚙️ 아직 초기설정이 완료되지 않았습니다. "
                "먼저 /초기설정을 실행해주세요.",
                ephemeral=True,
            )
            return None

        return settings

    @app_commands.command(
        name="대원등록",
        description="Discord 사용자를 출석 대원으로 등록합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(
        target_member="사용자",
    )
    @app_commands.describe(
        target_member="출석 대원으로 등록할 Discord 사용자",
    )
    async def register_member(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
    ) -> None:
        """대상 사용자를 활성 대원으로 등록하거나 재활성화한다."""

        settings = await self._get_guild_settings_or_notify(interaction)

        if settings is None:
            return

        if not has_officer_permission(
            interaction,
            settings["officer_role_id"],
        ):
            await interaction.response.send_message(
                "🚫 간부 또는 서버 관리자만 사용할 수 있는 명령어입니다.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        actor = interaction.user

        assert guild is not None

        try:
            result = await self.member_service.register_member(
                guild_id=guild.id,
                discord_id=target_member.id,
                display_name=target_member.display_name,
                created_by_discord_id=actor.id,
                is_bot=target_member.bot,
            )
        except BotRegistrationError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception(
                "대원 등록 중 오류가 발생했습니다. "
                "guild_id=%s actor_id=%s target_id=%s",
                guild.id,
                actor.id,
                target_member.id,
            )
            await interaction.response.send_message(
                "❌ 대원 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        if result.outcome is MemberRegistrationOutcome.ALREADY_ACTIVE:
            message = (
                f"ℹ️ {target_member.mention}님은 "
                "이미 활성 대원으로 등록되어 있습니다."
            )
        elif result.outcome is MemberRegistrationOutcome.REACTIVATED:
            message = (
                f"🔄 {target_member.mention}님을 다시 활성 대원으로 등록했습니다."
            )
        else:
            message = f"✅ {target_member.mention}님을 출석 대원으로 등록했습니다."

        await interaction.response.send_message(
            message,
            ephemeral=True,
        )

    @app_commands.command(
        name="대원제외",
        description="대원을 이후 출석 대상에서 제외합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(
        target_member="사용자",
        reason="사유",
    )
    @app_commands.describe(
        target_member="출석 대상에서 제외할 Discord 사용자",
        reason="제외 사유 (2자 이상 200자 이하)",
    )
    async def deactivate_member(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        reason: str,
    ) -> None:
        """대상 사용자를 이후 출석 대상에서 제외한다."""

        settings = await self._get_guild_settings_or_notify(interaction)

        if settings is None:
            return

        if not has_officer_permission(
            interaction,
            settings["officer_role_id"],
        ):
            await interaction.response.send_message(
                "🚫 간부 또는 서버 관리자만 사용할 수 있는 명령어입니다.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        actor = interaction.user

        assert guild is not None

        try:
            result = await self.member_service.deactivate_member(
                guild_id=guild.id,
                discord_id=target_member.id,
                display_name=target_member.display_name,
                reason=reason,
                actor_discord_id=actor.id,
            )
        except InvalidDeactivationReasonError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception(
                "대원 제외 중 오류가 발생했습니다. "
                "guild_id=%s actor_id=%s target_id=%s",
                guild.id,
                actor.id,
                target_member.id,
            )
            await interaction.response.send_message(
                "❌ 대원 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        if result.outcome is MemberDeactivationOutcome.NOT_FOUND:
            message = f"⚠️ {target_member.mention}님은 등록된 대원이 아닙니다."
        elif result.outcome is MemberDeactivationOutcome.ALREADY_INACTIVE:
            message = (
                f"ℹ️ {target_member.mention}님은 "
                "이미 출석 대상에서 제외되어 있습니다."
            )
        else:
            message = (
                f"👋 {target_member.mention}님을 이후 출석 대상에서 제외했습니다.\n"
                f"📝 사유: {reason.strip()}"
            )

        await interaction.response.send_message(
            message,
            ephemeral=True,
        )

    @app_commands.command(
        name="대원목록",
        description="현재 서버의 활성 대원 목록을 조회합니다.",
    )
    @app_commands.guild_only()
    async def list_members(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """현재 서버의 활성 대원 목록을 Embed로 보여준다."""

        settings = await self._get_guild_settings_or_notify(interaction)

        if settings is None:
            return

        guild = interaction.guild

        assert guild is not None

        try:
            members = await self.member_service.list_active_members(
                guild_id=guild.id,
            )
        except Exception:
            logger.exception(
                "대원 목록 조회 중 오류가 발생했습니다. guild_id=%s",
                guild.id,
            )
            await interaction.response.send_message(
                "❌ 대원 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        if not members:
            await interaction.response.send_message(
                "ℹ️ 현재 등록된 활성 대원이 없습니다.",
                ephemeral=True,
            )
            return

        description_lines = [
            f"{index}. <@{member['discord_id']}>"
            for index, member in enumerate(members, start=1)
        ]

        embed = discord.Embed(
            title="👥 출석 대원 목록",
            description="\n".join(description_lines),
            color=discord.Color.blurple(),
        )

        embed.set_footer(
            text=f"✅ 활성 대원 {len(members)}명",
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )
