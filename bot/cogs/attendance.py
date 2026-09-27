"""출석 체크인, 현황 조회, 기록 수정, 오늘 세션 제어 슬래시 명령어(`/출석 ...`)를 제공한다.

일상적인 체크인은 출석 공지의 [출석하기] 버튼(`bot.ui.views.attendance`)으로
처리되고, 이 Cog의 `/출석 체크인`은 버튼을 놓친 경우의 대안이다.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.common import (
    NOT_CONFIGURED_MESSAGE,
    has_officer_access,
    require_guild,
    require_officer,
)
from bot.runtime.time_provider import TimeProvider
from bot.services.admin_service import (
    AdminService,
    SessionControlResult,
    SessionControlStatus,
)
from bot.services.attendance_service import AttendanceService
from bot.services.guild_service import GuildService
from bot.services.voice_verification_service import VoiceVerificationService
from bot.ui.attendance_messages import (
    build_check_in_message,
    build_correction_message,
    build_status_embed,
    build_verification_overview_embed,
    current_voice_location,
)

logger = logging.getLogger(__name__)


class AttendanceCog(commands.Cog):
    """사용자 출석과 오늘 출석 현황 명령어를 제공한다."""

    attendance = app_commands.Group(
        name="출석",
        description="출석 체크인, 현황 조회, 기록 수정, 오늘 세션 제어를 처리합니다.",
        guild_only=True,
    )

    def __init__(
        self,
        *,
        attendance_service: AttendanceService,
        guild_service: GuildService,
        admin_service: AdminService,
        voice_verification_service: VoiceVerificationService,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Cog 의존성을 초기화한다."""

        self.attendance_service = attendance_service
        self.guild_service = guild_service
        self.admin_service = admin_service
        self.voice_verification_service = voice_verification_service
        self.time_provider = time_provider or TimeProvider()

    @attendance.command(name="체크인", description="오늘 출석 세션에 출석합니다. (공지의 출석하기 버튼과 동일)")
    async def check_in(self, interaction: discord.Interaction) -> None:
        """/출석 체크인 명령을 처리한다."""

        guild = await require_guild(interaction)
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        voice_channel_id, voice_category_id = current_voice_location(interaction.user)

        try:
            result = await self.attendance_service.check_in(
                guild_id=guild.id,
                discord_id=interaction.user.id,
                now=self.time_provider.now_utc(),
                current_voice_channel_id=voice_channel_id,
                current_voice_category_id=voice_category_id,
            )
        except Exception:
            logger.exception(
                "출석 처리 중 오류가 발생했습니다. guild_id=%s discord_id=%s",
                guild.id,
                interaction.user.id,
            )
            await interaction.followup.send(
                "❌ 출석 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(build_check_in_message(result), ephemeral=True)

    @attendance.command(name="현황", description="오늘 출석 세션의 정상·지각·미체크 현황을 조회합니다.")
    async def show_status(self, interaction: discord.Interaction) -> None:
        """/출석 현황 명령을 처리한다."""

        guild = await require_guild(interaction)
        if guild is None:
            return

        try:
            result = await self.attendance_service.get_today_status(
                guild_id=guild.id,
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception("출석 현황 조회 중 오류가 발생했습니다. guild_id=%s", guild.id)
            await interaction.response.send_message(
                "❌ 출석 현황 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(embed=build_status_embed(result))

    @attendance.command(name="검증현황", description="오늘 음성 검증 진행 상황(대기/성공/실패, 누적 시간)을 조회합니다.")
    async def verification_status(self, interaction: discord.Interaction) -> None:
        """/출석 검증현황 명령을 처리한다."""

        if await require_officer(interaction, self.guild_service) is None:
            return
        guild = interaction.guild
        assert guild is not None
        overview = await self.voice_verification_service.list_today_verifications(
            guild_id=guild.id,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(
            embed=build_verification_overview_embed(overview),
            ephemeral=True,
        )

    @attendance.command(name="수정", description="간부가 특정 날짜의 출석 기록을 정정합니다.")
    @app_commands.rename(
        target_member="사용자",
        attendance_date="날짜",
        new_status="상태",
        reason="사유",
    )
    @app_commands.describe(
        target_member="출석을 정정할 사용자",
        attendance_date="YYYY-MM-DD 형식의 서버 기준 날짜",
        new_status="변경할 출석 상태",
        reason="정정 사유",
    )
    @app_commands.choices(
        new_status=[
            app_commands.Choice(name="정상 출석", value="PRESENT"),
            app_commands.Choice(name="지각", value="LATE"),
            app_commands.Choice(name="결석", value="ABSENT"),
        ]
    )
    async def correct_attendance(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        attendance_date: str,
        new_status: app_commands.Choice[str],
        reason: str,
    ) -> None:
        """/출석 수정 명령을 처리한다."""

        if await require_officer(interaction, self.guild_service) is None:
            return
        guild = interaction.guild
        assert guild is not None

        try:
            result = await self.attendance_service.correct_attendance(
                guild_id=guild.id,
                target_discord_id=target_member.id,
                attendance_date=attendance_date,
                new_status=new_status.value,
                reason=reason,
                actor_discord_id=interaction.user.id,
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception(
                "출석 수정 중 오류가 발생했습니다. guild_id=%s actor_id=%s target_id=%s",
                guild.id,
                interaction.user.id,
                target_member.id,
            )
            await interaction.response.send_message(
                "❌ 출석 수정 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            build_correction_message(result, target_member.mention),
            ephemeral=True,
        )

    @attendance.command(name="오늘취소", description="오늘 출석 세션을 취소합니다.")
    @app_commands.rename(reason="사유")
    async def cancel_today(self, interaction: discord.Interaction, reason: str) -> None:
        """/출석 오늘취소 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        result = await self.admin_service.cancel_today_session(
            guild_id=guild.id,
            reason=reason,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(
            self._session_control_message(result),
            ephemeral=True,
        )

    @attendance.command(name="오늘재개", description="취소된 오늘 출석 세션을 재개합니다.")
    async def resume_today(self, interaction: discord.Interaction) -> None:
        """/출석 오늘재개 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        result = await self.admin_service.resume_today_session(
            guild_id=guild.id,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(
            self._session_control_message(result),
            ephemeral=True,
        )

    def _session_control_message(self, result: SessionControlResult) -> str:
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
            SessionControlStatus.NOT_CONFIGURED: NOT_CONFIGURED_MESSAGE,
            SessionControlStatus.NO_SESSION: "오늘 출석 세션이 없습니다.",
            SessionControlStatus.INVALID_REASON: "사유는 2자 이상 500자 이하로 입력해 주세요.",
            SessionControlStatus.CLOSED: "이미 마감된 세션은 취소할 수 없습니다.",
            SessionControlStatus.ALREADY_CANCELLED: "이미 취소된 세션입니다.",
            SessionControlStatus.NOT_CANCELLED: "취소된 세션만 재개할 수 있습니다.",
            SessionControlStatus.CLOSE_ALREADY_PASSED: "마감 시간이 지난 세션은 재개할 수 없습니다.",
        }
        return messages.get(result.status, "요청을 처리할 수 없습니다.")
