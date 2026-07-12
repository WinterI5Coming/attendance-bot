"""Stage B 출석 조정 슬래시 명령어를 제공한다."""

from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.adjustment_service import (
    AdjustmentResult,
    AdjustmentService,
    AdjustmentStatus,
)
from bot.services.guild_service import GuildService
from bot.utils.permissions import has_officer_permission


logger = logging.getLogger(__name__)


class AdjustmentsCog(commands.Cog):
    """관리자의 지각 감면과 결석 면제 명령어를 제공한다."""

    def __init__(
        self,
        *,
        adjustment_service: AdjustmentService,
        guild_service: GuildService,
    ) -> None:
        """Cog 의존성을 초기화한다."""

        self.adjustment_service = adjustment_service
        self.guild_service = guild_service

    @app_commands.command(name="지각감면", description="승인된 사유를 근거로 지각 시간을 감면합니다.")
    @app_commands.guild_only()
    @app_commands.rename(
        target_member="사용자",
        attendance_date="날짜",
        reduction_minutes="감면분",
        full_reduction="전체감면",
        reason="사유",
    )
    async def reduce_late(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        attendance_date: str,
        reduction_minutes: int,
        full_reduction: bool,
        reason: str,
    ) -> None:
        """/지각감면 명령을 처리한다."""

        permission = await self._has_permission(interaction)
        guild = interaction.guild
        assert guild is not None
        try:
            result = await self.adjustment_service.apply_late_reduction(
                guild_id=guild.id,
                target_discord_id=target_member.id,
                attendance_date=attendance_date,
                reduction_minutes=reduction_minutes,
                full_reduction=full_reduction,
                reason=reason,
                actor_discord_id=interaction.user.id,
                has_permission=permission,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("Late reduction failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "지각 감면 처리 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            self._build_late_message(result, target_member.mention),
            ephemeral=True,
        )

    @app_commands.command(name="지각감면취소", description="활성 지각 감면을 취소합니다.")
    @app_commands.guild_only()
    @app_commands.rename(target_member="사용자", attendance_date="날짜", reason="취소사유")
    async def cancel_late_reduction(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        attendance_date: str,
        reason: str,
    ) -> None:
        """/지각감면취소 명령을 처리한다."""

        permission = await self._has_permission(interaction)
        guild = interaction.guild
        assert guild is not None
        result = await self.adjustment_service.cancel_late_reduction(
            guild_id=guild.id,
            target_discord_id=target_member.id,
            attendance_date=attendance_date,
            reason=reason,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._build_cancel_message(result, "지각 감면"),
            ephemeral=True,
        )

    @app_commands.command(name="결석면제", description="승인된 사유를 근거로 결석 감점을 면제합니다.")
    @app_commands.guild_only()
    @app_commands.rename(target_member="사용자", attendance_date="날짜", reason="사유")
    async def exempt_absence(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        attendance_date: str,
        reason: str,
    ) -> None:
        """/결석면제 명령을 처리한다."""

        permission = await self._has_permission(interaction)
        guild = interaction.guild
        assert guild is not None
        result = await self.adjustment_service.apply_absence_exemption(
            guild_id=guild.id,
            target_discord_id=target_member.id,
            attendance_date=attendance_date,
            reason=reason,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._build_absence_message(result, target_member.mention),
            ephemeral=True,
        )

    @app_commands.command(name="결석면제취소", description="활성 결석 면제를 취소합니다.")
    @app_commands.guild_only()
    @app_commands.rename(target_member="사용자", attendance_date="날짜", reason="취소사유")
    async def cancel_absence_exemption(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        attendance_date: str,
        reason: str,
    ) -> None:
        """/결석면제취소 명령을 처리한다."""

        permission = await self._has_permission(interaction)
        guild = interaction.guild
        assert guild is not None
        result = await self.adjustment_service.cancel_absence_exemption(
            guild_id=guild.id,
            target_discord_id=target_member.id,
            attendance_date=attendance_date,
            reason=reason,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._build_cancel_message(result, "결석 면제"),
            ephemeral=True,
        )

    async def _has_permission(self, interaction: discord.Interaction) -> bool:
        """명령 실행자가 간부 권한을 갖고 있는지 확인한다."""

        guild = interaction.guild
        if guild is None:
            return False
        settings = await self.guild_service.get_settings(guild.id)
        if settings is None:
            return False
        return has_officer_permission(interaction, settings["officer_role_id"])

    def _build_late_message(self, result: AdjustmentResult, mention: str) -> str:
        """지각 감면 처리 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is AdjustmentStatus.APPLIED:
            original = (result.original_late_seconds or 0) // 60
            requested = (result.requested_reduction_seconds or 0) // 60
            remaining = (result.resulting_late_seconds or 0) // 60
            return (
                "지각 감면을 적용했습니다.\n"
                f"대상: {mention}\n"
                f"날짜: {result.attendance_date}\n"
                f"기존 지각: {original}분\n"
                f"감면: {requested}분\n"
                f"최종 지각: {remaining}분\n"
                f"유효 상태: {result.resulting_status}\n"
                f"점수 보정: {result.score_delta:+d}\n"
                f"조정 번호: {result.adjustment_id}"
            )
        return self._error_message(result.status)

    def _build_absence_message(self, result: AdjustmentResult, mention: str) -> str:
        """결석 면제 처리 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is AdjustmentStatus.APPLIED:
            return (
                "결석 면제를 적용했습니다.\n"
                f"대상: {mention}\n"
                f"날짜: {result.attendance_date}\n"
                f"기존 상태: {result.original_status}\n"
                f"유효 상태: {result.resulting_status}\n"
                f"점수 보정: {result.score_delta:+d}\n"
                f"조정 번호: {result.adjustment_id}"
            )
        return self._error_message(result.status)

    def _build_cancel_message(self, result: AdjustmentResult, label: str) -> str:
        """감면 또는 면제 취소 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is AdjustmentStatus.CANCELLED:
            return (
                f"{label}을 취소했습니다.\n"
                f"날짜: {result.attendance_date}\n"
                f"조정 번호: {result.adjustment_id}\n"
                f"점수 복원: {result.reversal_delta:+d}"
            )
        return self._error_message(result.status)

    def _error_message(self, status: AdjustmentStatus) -> str:
        """조정 실패 상태 코드를 사용자 친화적인 한국어 문구로 변환한다."""

        messages = {
            AdjustmentStatus.PERMISSION_DENIED: "관리 권한이 필요합니다.",
            AdjustmentStatus.NOT_CONFIGURED: "초기 설정이 필요합니다.",
            AdjustmentStatus.TARGET_NOT_FOUND: "대상자가 등록되어 있지 않습니다.",
            AdjustmentStatus.SESSION_NOT_FOUND: "해당 날짜의 출석 세션이 없습니다.",
            AdjustmentStatus.RECORD_NOT_FOUND: "해당 날짜의 출석 기록이 없습니다.",
            AdjustmentStatus.EXCUSE_NOT_APPROVED: "승인된 사유 신청을 찾을 수 없습니다.",
            AdjustmentStatus.INVALID_STATUS: "해당 출석 상태에는 이 조정을 적용할 수 없습니다.",
            AdjustmentStatus.INVALID_REASON: "사유는 2자 이상 500자 이하로 입력해주세요.",
            AdjustmentStatus.INVALID_REDUCTION: "감면 시간은 1분 이상이어야 합니다.",
            AdjustmentStatus.DUPLICATE_ACTIVE_ADJUSTMENT: "이미 활성 조정이 있습니다.",
            AdjustmentStatus.ACTIVE_ADJUSTMENT_NOT_FOUND: "취소할 활성 조정을 찾을 수 없습니다.",
        }
        return messages.get(status, "요청을 처리할 수 없습니다.")
