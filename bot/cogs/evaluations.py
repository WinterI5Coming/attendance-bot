"""평가와 수동 점수 조정 슬래시 명령어를 제공한다."""

from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.evaluation_service import (
    EvaluationResult,
    EvaluationService,
    EvaluationStatus,
    ManualScoreResult,
    ManualScoreStatus,
)
from bot.services.guild_service import GuildService
from bot.utils.permissions import has_officer_permission


logger = logging.getLogger(__name__)


class EvaluationsCog(commands.Cog):
    """간부 평가와 수동 점수 조정 명령어를 제공한다."""

    def __init__(
        self,
        *,
        evaluation_service: EvaluationService,
        guild_service: GuildService,
    ) -> None:
        """Cog가 사용할 평가 서비스와 서버 설정 서비스를 저장한다."""

        self.evaluation_service = evaluation_service
        self.guild_service = guild_service

    @app_commands.command(name="평가", description="대상자에게 평가 점수를 부여합니다.")
    @app_commands.guild_only()
    @app_commands.rename(target_member="사용자", score="점수", reason="사유")
    async def create_evaluation(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        score: int,
        reason: str,
    ) -> None:
        """/평가 명령을 처리한다."""

        permission = await self._has_permission(interaction)
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
                now=datetime.now(timezone.utc),
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

    @app_commands.command(name="평가취소", description="평가를 취소하고 반대 점수를 생성합니다.")
    @app_commands.guild_only()
    @app_commands.rename(evaluation_id="평가번호", reason="취소사유")
    async def cancel_evaluation(
        self,
        interaction: discord.Interaction,
        evaluation_id: int,
        reason: str,
    ) -> None:
        """/평가취소 명령을 처리한다."""

        permission = await self._has_permission(interaction)
        guild = interaction.guild
        assert guild is not None
        try:
            result = await self.evaluation_service.cancel_evaluation(
                guild_id=guild.id,
                evaluation_id=evaluation_id,
                actor_discord_id=interaction.user.id,
                cancellation_reason=reason,
                has_permission=permission,
                now=datetime.now(timezone.utc),
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

    @app_commands.command(name="점수조정", description="대상자의 점수를 수동 조정합니다.")
    @app_commands.guild_only()
    @app_commands.rename(target_member="사용자", delta="점수", reason="사유")
    async def adjust_score(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        delta: int,
        reason: str,
    ) -> None:
        """/점수조정 명령을 처리한다."""

        permission = await self._has_permission(interaction)
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
                now=datetime.now(timezone.utc),
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

    async def _has_permission(self, interaction: discord.Interaction) -> bool:
        """명령 실행자가 평가/점수 조정 권한을 갖고 있는지 확인한다."""

        guild = interaction.guild
        if guild is None:
            return False
        settings = await self.guild_service.get_settings(guild.id)
        if settings is None:
            return False
        return has_officer_permission(interaction, settings["officer_role_id"])

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
