"""Discord 서버의 최초 설정 명령어를 제공한다."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.guild_service import GuildService
from bot.utils.permissions import is_server_admin


logger = logging.getLogger(__name__)


class SetupCog(commands.Cog):
    """서버 최초 설정 관련 슬래시 명령어."""

    def __init__(
        self,
        guild_service: GuildService,
    ) -> None:
        """Cog에 서버 설정 Service를 주입한다."""

        self.guild_service = guild_service

    @app_commands.command(
        name="초기설정",
        description="근태관리봇을 현재 서버에 처음 설정합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(
        officer_role="간부역할",
        attendance_channel="출석채널",
        announcement_channel="공지채널",
    )
    @app_commands.describe(
        officer_role="간부 명령어를 사용할 Discord 역할",
        attendance_channel="출석 명령어를 사용할 텍스트 채널",
        announcement_channel="출석 시작과 마감 공지를 보낼 채널",
    )
    async def initial_setup(
        self,
        interaction: discord.Interaction,
        officer_role: discord.Role,
        attendance_channel: discord.TextChannel,
        announcement_channel: discord.TextChannel,
    ) -> None:
        """현재 Discord 서버의 기본 근태 설정을 생성한다."""

        if not is_server_admin(interaction):
            await interaction.response.send_message(
                "서버 소유자 또는 관리자만 초기설정을 할 수 있습니다.",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        if officer_role.is_default():
            await interaction.response.send_message(
                "@everyone 역할은 간부 역할로 사용할 수 없습니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.guild_service.initialize_guild(
                guild_id=guild.id,
                officer_role_id=officer_role.id,
                attendance_channel_id=attendance_channel.id,
                announcement_channel_id=announcement_channel.id,
            )
        except Exception:
            logger.exception(
                "서버 초기설정 중 오류가 발생했습니다. guild_id=%s",
                guild.id,
            )

            await interaction.response.send_message(
                "초기설정 중 DB 오류가 발생했습니다. "
                "서버 로그를 확인해주세요.",
                ephemeral=True,
            )
            return

        if not result.created:
            await interaction.response.send_message(
                "이미 초기설정이 완료된 서버입니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="근태관리봇 초기설정 완료",
            description=(
                "현재 서버의 기본 근태 설정을 저장했습니다."
            ),
        )

        embed.add_field(
            name="간부 역할",
            value=officer_role.mention,
            inline=False,
        )

        embed.add_field(
            name="출석 채널",
            value=attendance_channel.mention,
            inline=True,
        )

        embed.add_field(
            name="공지 채널",
            value=announcement_channel.mention,
            inline=True,
        )

        embed.add_field(
            name="출석 요일",
            value=result.attendance_days,
            inline=False,
        )

        embed.add_field(
            name="출석 시간",
            value=(
                f"정상: {result.attendance_start}"
                f" ~ {result.late_deadline}\n"
                f"지각: {result.late_deadline}"
                f" ~ {result.close_deadline}\n"
                f"마감: {result.close_deadline}"
            ),
            inline=False,
        )

        embed.add_field(
            name="사유 승인 방식",
            value=result.excuse_mode,
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )