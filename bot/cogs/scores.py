"""평가, 수동 점수 조정, 지각 감면, 결석 면제 슬래시 명령어(`/점수 ...`)를 제공한다."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.common import NOT_CONFIGURED_MESSAGE, has_officer_access
from bot.runtime.time_provider import TimeProvider
from bot.services.adjustment_service import (
    AdjustmentResult,
    AdjustmentService,
    AdjustmentStatus,
)
from bot.services.evaluation_service import (
    EvaluationResult,
    EvaluationService,
    EvaluationStatus,
    ManualScoreResult,
    ManualScoreStatus,
)
from bot.services.guild_service import GuildService

logger = logging.getLogger(__name__)


class ScoresCog(commands.Cog):
    """간부가 점수 장부에 개입하는 모든 명령어를 한 그룹으로 제공한다."""

    scores = app_commands.Group(
        name="점수",
        description="평가, 점수 조정, 지각 감면, 결석 면제를 처리합니다.",
        guild_only=True,
    )

    def __init__(
        self,
        *,
        evaluation_service: EvaluationService,
        adjustment_service: AdjustmentService,
        guild_service: GuildService,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Cog가 사용할 평가/조정 서비스와 서버 설정 서비스를 저장한다."""

        self.evaluation_service = evaluation_service
        self.adjustment_service = adjustment_service
        self.guild_service = guild_service
        self.time_provider = time_provider or TimeProvider()

    # ----- 평가와 수동 조정 -----

    @scores.command(name="평가", description="대상자에게 평가 점수를 부여합니다.")
    @app_commands.rename(target_member="사용자", score="점수", reason="사유")
    async def create_evaluation(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        score: int,
        reason: str,
    ) -> None:
        """/점수 평가 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        try:
            result = await self.evaluation_service.create_evaluation(
                guild_id=guild.id,
                target_discord_id=target_member.id,
                evaluator_discord_id=interaction.user.id,
                score=score,
                reason=reason,
                has_permission=permission,
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception("Evaluation creation failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "평가 처리 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._evaluation_message(result, target_member.mention),
            ephemeral=True,
        )

    @scores.command(name="평가취소", description="평가를 취소하고 반대 점수를 생성합니다.")
    @app_commands.rename(evaluation_id="평가번호", reason="취소사유")
    async def cancel_evaluation(
        self,
        interaction: discord.Interaction,
        evaluation_id: int,
        reason: str,
    ) -> None:
        """/점수 평가취소 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        try:
            result = await self.evaluation_service.cancel_evaluation(
                guild_id=guild.id,
                evaluation_id=evaluation_id,
                actor_discord_id=interaction.user.id,
                cancellation_reason=reason,
                has_permission=permission,
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception("Evaluation cancellation failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "평가 취소 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        target = (
            f"<@{result.target_discord_id}>"
            if result.target_discord_id is not None
            else "-"
        )
        await interaction.response.send_message(
            self._evaluation_message(result, target),
            ephemeral=True,
        )

    @scores.command(name="조정", description="대상자의 점수를 수동 조정합니다.")
    @app_commands.rename(target_member="사용자", delta="점수", reason="사유")
    async def adjust_score(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        delta: int,
        reason: str,
    ) -> None:
        """/점수 조정 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        try:
            result = await self.evaluation_service.adjust_score(
                guild_id=guild.id,
                target_discord_id=target_member.id,
                actor_discord_id=interaction.user.id,
                delta=delta,
                reason=reason,
                has_permission=permission,
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception("Manual score adjustment failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "점수 조정 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._manual_score_message(result, target_member.mention),
            ephemeral=True,
        )

    # ----- 지각 감면과 결석 면제 -----

    @scores.command(name="지각감면", description="승인된 사유를 근거로 지각 시간을 감면합니다.")
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
        """/점수 지각감면 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
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
                now=self.time_provider.now_utc(),
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

    @scores.command(name="지각감면취소", description="활성 지각 감면을 취소합니다.")
    @app_commands.rename(target_member="사용자", attendance_date="날짜", reason="취소사유")
    async def cancel_late_reduction(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        attendance_date: str,
        reason: str,
    ) -> None:
        """/점수 지각감면취소 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        result = await self.adjustment_service.cancel_late_reduction(
            guild_id=guild.id,
            target_discord_id=target_member.id,
            attendance_date=attendance_date,
            reason=reason,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(
            self._build_cancel_message(result, "지각 감면"),
            ephemeral=True,
        )

    @scores.command(name="결석면제", description="승인된 사유를 근거로 결석 감점을 면제합니다.")
    @app_commands.rename(target_member="사용자", attendance_date="날짜", reason="사유")
    async def exempt_absence(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        attendance_date: str,
        reason: str,
    ) -> None:
        """/점수 결석면제 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        result = await self.adjustment_service.apply_absence_exemption(
            guild_id=guild.id,
            target_discord_id=target_member.id,
            attendance_date=attendance_date,
            reason=reason,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(
            self._build_absence_message(result, target_member.mention),
            ephemeral=True,
        )

    @scores.command(name="결석면제취소", description="활성 결석 면제를 취소합니다.")
    @app_commands.rename(target_member="사용자", attendance_date="날짜", reason="취소사유")
    async def cancel_absence_exemption(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        attendance_date: str,
        reason: str,
    ) -> None:
        """/점수 결석면제취소 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        result = await self.adjustment_service.cancel_absence_exemption(
            guild_id=guild.id,
            target_discord_id=target_member.id,
            attendance_date=attendance_date,
            reason=reason,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(
            self._build_cancel_message(result, "결석 면제"),
            ephemeral=True,
        )

    # ----- 응답 메시지 -----

    def _evaluation_message(self, result: EvaluationResult, target: str) -> str:
        """평가 생성 또는 취소 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is EvaluationStatus.CREATED:
            return (
                "평가를 등록했습니다.\n"
                f"평가 번호: {result.evaluation_id}\n"
                f"대상: {target}\n"
                f"점수: {result.score:+d}\n"
                f"현재 총점: {result.total_score}점\n"
                f"현재 계급: {result.current_rank}"
            )
        if result.status is EvaluationStatus.CANCELLED:
            return (
                "평가를 취소했습니다.\n"
                f"평가 번호: {result.evaluation_id}\n"
                f"대상: {target}\n"
                f"원래 점수: {result.score:+d}\n"
                f"취소 점수: {result.reversal_delta:+d}\n"
                f"현재 총점: {result.total_score}점\n"
                f"현재 계급: {result.current_rank}"
            )
        messages = {
            EvaluationStatus.PERMISSION_DENIED: "관리 권한이 필요합니다.",
            EvaluationStatus.NOT_FOUND: "평가를 찾을 수 없습니다.",
            EvaluationStatus.ALREADY_CANCELLED: "이미 취소된 평가입니다.",
            EvaluationStatus.INVALID_SCORE: "평가 점수는 -5~+5 사이의 0이 아닌 값이어야 합니다.",
            EvaluationStatus.INVALID_REASON: "사유는 2자 이상 500자 이하로 입력해 주세요.",
            EvaluationStatus.TARGET_NOT_ACTIVE: "대상자가 활성 대상자로 등록되어 있지 않습니다.",
            EvaluationStatus.SELF_EVALUATION_NOT_ALLOWED: "자기 자신은 평가할 수 없습니다.",
        }
        return messages[result.status]

    def _manual_score_message(self, result: ManualScoreResult, target: str) -> str:
        """수동 점수 조정 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is ManualScoreStatus.ADJUSTED:
            return (
                "점수를 조정했습니다.\n"
                f"대상: {target}\n"
                f"조정 점수: {result.delta:+d}\n"
                f"변경 전 총점: {result.previous_total}점\n"
                f"변경 후 총점: {result.total_score}점\n"
                f"현재 계급: {result.current_rank}"
            )
        messages = {
            ManualScoreStatus.PERMISSION_DENIED: "관리 권한이 필요합니다.",
            ManualScoreStatus.INVALID_SCORE: "조정 점수는 -1000~+1000 사이의 0이 아닌 값이어야 합니다.",
            ManualScoreStatus.INVALID_REASON: "사유는 2자 이상 500자 이하로 입력해 주세요.",
            ManualScoreStatus.TARGET_NOT_ACTIVE: "대상자가 활성 대상자로 등록되어 있지 않습니다.",
        }
        return messages[result.status]

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
        return self._adjustment_error_message(result.status)

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
        return self._adjustment_error_message(result.status)

    def _build_cancel_message(self, result: AdjustmentResult, label: str) -> str:
        """감면 또는 면제 취소 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is AdjustmentStatus.CANCELLED:
            return (
                f"{label}을 취소했습니다.\n"
                f"날짜: {result.attendance_date}\n"
                f"조정 번호: {result.adjustment_id}\n"
                f"점수 복원: {result.reversal_delta:+d}"
            )
        return self._adjustment_error_message(result.status)

    def _adjustment_error_message(self, status: AdjustmentStatus) -> str:
        """조정 실패 상태 코드를 사용자 친화적인 한국어 문구로 변환한다."""

        messages = {
            AdjustmentStatus.PERMISSION_DENIED: "관리 권한이 필요합니다.",
            AdjustmentStatus.NOT_CONFIGURED: NOT_CONFIGURED_MESSAGE,
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
