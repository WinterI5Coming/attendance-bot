"""관리자 설정과 당일 세션 제어 슬래시 명령어를 제공한다."""

from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.admin_service import (
    AdminService,
    SessionControlResult,
    SessionControlStatus,
    SettingsUpdateResult,
    SettingsUpdateStatus,
)
from bot.services.guild_service import GuildService
from bot.utils.permissions import has_officer_permission


logger = logging.getLogger(__name__)


class SettingsCog(commands.Cog):
    """서버 설정과 오늘 출석 세션 제어 명령어를 제공한다."""

    def __init__(
        self,
        *,
        admin_service: AdminService,
        guild_service: GuildService,
    ) -> None:
        """Cog가 사용할 관리자 서비스와 서버 설정 서비스를 저장한다."""

        self.admin_service = admin_service
        self.guild_service = guild_service

    @app_commands.command(name="설정조회", description="현재 근태관리 설정을 조회합니다.")
    @app_commands.guild_only()
    async def show_settings(self, interaction: discord.Interaction) -> None:
        """/설정조회 명령을 처리한다."""

        if not await self._has_permission(interaction):
            await interaction.response.send_message("관리 권한이 필요합니다.", ephemeral=True)
            return
        guild = interaction.guild
        assert guild is not None
        settings = await self.guild_service.get_settings(guild.id)
        if settings is None:
            await interaction.response.send_message("초기설정이 필요합니다.", ephemeral=True)
            return
        await interaction.response.send_message(
            (
                "현재 설정\n"
                f"timezone: {settings['timezone']}\n"
                f"attendance_days: {settings['attendance_days']}\n"
                f"attendance_start: {settings['attendance_start']}\n"
                f"late_deadline: {settings['late_deadline']}\n"
                f"close_deadline: {settings['close_deadline']}\n"
                f"excuse_mode: {settings['excuse_mode']}\n"
                f"officer_role_id: {settings['officer_role_id']}\n"
                f"attendance_channel_id: {settings['attendance_channel_id']}\n"
                f"announcement_channel_id: {settings['announcement_channel_id']}"
            ),
            ephemeral=True,
        )

    @app_commands.command(name="설정변경", description="근태관리 설정 한 항목을 변경합니다.")
    @app_commands.guild_only()
    @app_commands.rename(field="항목", value="값")
    @app_commands.choices(
        field=[
            app_commands.Choice(name="timezone", value="timezone"),
            app_commands.Choice(name="attendance_days", value="attendance_days"),
            app_commands.Choice(name="attendance_start", value="attendance_start"),
            app_commands.Choice(name="late_deadline", value="late_deadline"),
            app_commands.Choice(name="close_deadline", value="close_deadline"),
            app_commands.Choice(name="excuse_mode", value="excuse_mode"),
            app_commands.Choice(name="officer_role_id", value="officer_role_id"),
            app_commands.Choice(name="attendance_channel_id", value="attendance_channel_id"),
            app_commands.Choice(name="announcement_channel_id", value="announcement_channel_id"),
            app_commands.Choice(name="voice_verification_enabled", value="voice_verification_enabled"),
            app_commands.Choice(name="voice_channel_ids", value="voice_channel_ids"),
            app_commands.Choice(name="voice_category_ids", value="voice_category_ids"),
            app_commands.Choice(
                name="exempt_absence_counts_in_attendance_denominator",
                value="exempt_absence_counts_in_attendance_denominator",
            ),
        ]
    )
    async def update_setting(
        self,
        interaction: discord.Interaction,
        field: app_commands.Choice[str],
        value: str,
    ) -> None:
        """/설정변경 명령을 처리한다."""

        permission = await self._has_permission(interaction)
        guild = interaction.guild
        assert guild is not None
        try:
            result = await self.admin_service.update_setting(
                guild_id=guild.id,
                field=field.value,
                value=value,
                actor_discord_id=interaction.user.id,
                has_permission=permission,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("Setting update failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "설정 변경 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            self._settings_update_message(result),
            ephemeral=True,
        )

    @app_commands.command(name="오늘출석취소", description="오늘 출석 세션을 취소합니다.")
    @app_commands.guild_only()
    @app_commands.rename(reason="사유")
    async def cancel_today(
        self,
        interaction: discord.Interaction,
        reason: str,
    ) -> None:
        """/오늘출석취소 명령을 처리한다."""

        permission = await self._has_permission(interaction)
        guild = interaction.guild
        assert guild is not None
        result = await self.admin_service.cancel_today_session(
            guild_id=guild.id,
            reason=reason,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._session_message(result),
            ephemeral=True,
        )

    @app_commands.command(name="오늘출석재개", description="취소된 오늘 출석 세션을 재개합니다.")
    @app_commands.guild_only()
    async def resume_today(self, interaction: discord.Interaction) -> None:
        """/오늘출석재개 명령을 처리한다."""

        permission = await self._has_permission(interaction)
        guild = interaction.guild
        assert guild is not None
        result = await self.admin_service.resume_today_session(
            guild_id=guild.id,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._session_message(result),
            ephemeral=True,
        )

    async def _has_permission(self, interaction: discord.Interaction) -> bool:
        """명령 실행자가 설정 변경 권한을 갖고 있는지 확인한다."""

        guild = interaction.guild
        if guild is None:
            return False
        settings = await self.guild_service.get_settings(guild.id)
        if settings is None:
            return False
        return has_officer_permission(interaction, settings["officer_role_id"])

    def _settings_update_message(self, result: SettingsUpdateResult) -> str:
        """설정 변경 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is SettingsUpdateStatus.UPDATED:
            return (
                "설정을 변경했습니다.\n"
                f"항목: {result.field}\n"
                f"변경 전: {result.before_value}\n"
                f"변경 후: {result.after_value}"
            )
        messages = {
            SettingsUpdateStatus.PERMISSION_DENIED: "관리 권한이 필요합니다.",
            SettingsUpdateStatus.NOT_CONFIGURED: "초기설정이 필요합니다.",
            SettingsUpdateStatus.INVALID_FIELD: "변경할 수 없는 설정 항목입니다.",
            SettingsUpdateStatus.INVALID_VALUE: "설정 값이 올바르지 않습니다.",
            SettingsUpdateStatus.INVALID_TIME_ORDER: "출석 시간은 시작 < 지각 < 마감 순서여야 합니다.",
        }
        return messages[result.status]

    def _session_message(self, result: SessionControlResult) -> str:
        """오늘 출석 세션 제어 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is SessionControlStatus.CANCELLED:
            return (
                "오늘 출석 세션을 취소했습니다.\n"
                f"세션 ID: {result.session_id}\n"
                f"점수 보정 이벤트: {result.score_event_count}개"
            )
        if result.status is SessionControlStatus.RESUMED:
            return (
                "오늘 출석 세션을 재개했습니다.\n"
                f"세션 ID: {result.session_id}\n"
                f"점수 복원 이벤트: {result.score_event_count}개"
            )
        messages = {
            SessionControlStatus.PERMISSION_DENIED: "관리 권한이 필요합니다.",
            SessionControlStatus.NOT_CONFIGURED: "초기설정이 필요합니다.",
            SessionControlStatus.NO_SESSION: "오늘 출석 세션이 없습니다.",
            SessionControlStatus.INVALID_REASON: "사유는 2자 이상 500자 이하로 입력해 주세요.",
            SessionControlStatus.CLOSED: "이미 마감된 세션은 취소할 수 없습니다.",
            SessionControlStatus.ALREADY_CANCELLED: "이미 취소된 세션입니다.",
            SessionControlStatus.NOT_CANCELLED: "취소된 세션만 재개할 수 있습니다.",
            SessionControlStatus.CLOSE_ALREADY_PASSED: "마감 시간이 지난 세션은 재개할 수 없습니다.",
        }
        return messages.get(result.status, "요청을 처리할 수 없습니다.")
