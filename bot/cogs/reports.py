"""개인 정보, 랭킹, 주간 보고 슬래시 명령어를 제공한다."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.common import require_guild
from bot.runtime.time_provider import TimeProvider
from bot.services.report_service import ReportService
from bot.ui.report_messages import (
    build_personal_embed,
    build_public_report_embed,
    build_ranking_embed,
    build_weekly_embed,
)

logger = logging.getLogger(__name__)


class ReportsCog(commands.Cog):
    """개인, 공개, 랭킹, 주간 리포트 명령어를 제공한다."""

    def __init__(
        self,
        *,
        report_service: ReportService,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """리포트 조회에 사용할 서비스와 시각 공급자를 저장한다."""

        self.report_service = report_service
        self.time_provider = time_provider or TimeProvider()

    @app_commands.command(name="내정보", description="내 출석 통계와 점수를 조회합니다. 사용자를 지정하면 공개 리포트를 보여줍니다.")
    @app_commands.guild_only()
    @app_commands.rename(target_member="사용자")
    @app_commands.describe(target_member="지정하면 해당 사용자의 공개 리포트를 채널에 표시합니다.")
    async def my_info(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member | None = None,
    ) -> None:
        """/내정보 명령을 처리한다."""

        guild = await require_guild(interaction)
        if guild is None:
            return

        if target_member is not None:
            await self._send_public_report(interaction, guild, target_member)
            return

        try:
            result = await self.report_service.get_my_report(
                guild_id=guild.id,
                discord_id=interaction.user.id,
            )
        except Exception:
            logger.exception("Personal report failed: guild_id=%s", guild.id)
            await interaction.response.send_message("내정보 조회 중 오류가 발생했습니다.", ephemeral=True)
            return

        await interaction.response.send_message(embed=build_personal_embed(result), ephemeral=True)

    @app_commands.command(name="랭킹", description="현재 출석 점수 랭킹을 조회합니다.")
    @app_commands.guild_only()
    async def ranking(self, interaction: discord.Interaction) -> None:
        """/랭킹 명령을 처리한다."""

        guild = await require_guild(interaction)
        if guild is None:
            return

        try:
            result = await self.report_service.get_ranking(guild_id=guild.id)
        except Exception:
            logger.exception("Ranking report failed: guild_id=%s", guild.id)
            await interaction.response.send_message("랭킹 조회 중 오류가 발생했습니다.", ephemeral=True)
            return

        await interaction.response.send_message(embed=build_ranking_embed(result))

    @app_commands.command(name="주간보고", description="이번 주 또는 지난 주 근태 통계를 조회합니다.")
    @app_commands.guild_only()
    @app_commands.rename(previous_week="지난주")
    async def weekly_report(
        self,
        interaction: discord.Interaction,
        previous_week: bool = False,
    ) -> None:
        """/주간보고 명령을 처리한다."""

        guild = await require_guild(interaction)
        if guild is None:
            return

        try:
            result = await self.report_service.get_weekly_report(
                guild_id=guild.id,
                now=self.time_provider.now_utc(),
                previous_week=previous_week,
            )
        except Exception:
            logger.exception("Weekly report failed: guild_id=%s", guild.id)
            await interaction.response.send_message("주간보고 생성 중 오류가 발생했습니다.", ephemeral=True)
            return

        await interaction.response.send_message(embed=build_weekly_embed(result))

    async def _send_public_report(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        target_member: discord.Member,
    ) -> None:
        """대상 사용자의 공개 가능한 근태 리포트를 채널에 표시한다."""

        try:
            result = await self.report_service.get_public_report(
                guild_id=guild.id,
                target_discord_id=target_member.id,
            )
        except Exception:
            logger.exception("Public report failed: guild_id=%s", guild.id)
            await interaction.response.send_message("리포트 조회 중 오류가 발생했습니다.", ephemeral=True)
            return

        await interaction.response.send_message(
            embed=build_public_report_embed(result, target_member.mention),
        )
