"""Report-related slash commands."""

from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.report_service import (
    PersonalReportResult,
    PublicReportResult,
    RankingResult,
    ReportService,
    WeeklyReportResult,
)
from bot.utils.time_utils import format_local_hhmm


logger = logging.getLogger(__name__)


class ReportsCog(commands.Cog):
    """Slash commands for personal, public, ranking, and weekly reports."""

    def __init__(self, report_service: ReportService) -> None:
        self.report_service = report_service

    @app_commands.command(name="내정보", description="내 출석 통계와 점수를 조회합니다.")
    @app_commands.guild_only()
    async def my_info(self, interaction: discord.Interaction) -> None:
        """Handle /내정보."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "서버에서만 사용할 수 있는 명령입니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.report_service.get_my_report(
                guild_id=guild.id,
                discord_id=interaction.user.id,
            )
        except Exception:
            logger.exception("Personal report failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "내정보 조회 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_personal_message(result),
            ephemeral=True,
        )

    @app_commands.command(name="랭킹", description="현재 출석 점수 랭킹을 조회합니다.")
    @app_commands.guild_only()
    async def ranking(self, interaction: discord.Interaction) -> None:
        """Handle /랭킹."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "서버에서만 사용할 수 있는 명령입니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.report_service.get_ranking(guild_id=guild.id)
        except Exception:
            logger.exception("Ranking report failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "랭킹 조회 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_ranking_message(result),
            ephemeral=False,
        )

    @app_commands.command(name="리포트", description="대상자의 공개 가능한 근태 리포트를 조회합니다.")
    @app_commands.guild_only()
    @app_commands.rename(target_member="사용자")
    async def public_report(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
    ) -> None:
        """Handle /리포트."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "서버에서만 사용할 수 있는 명령입니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.report_service.get_public_report(
                guild_id=guild.id,
                target_discord_id=target_member.id,
            )
        except Exception:
            logger.exception("Public report failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "리포트 조회 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_public_report_message(result, target_member.mention),
            ephemeral=False,
        )

    @app_commands.command(name="주간보고", description="이번 주 또는 지난 주 근태 통계를 조회합니다.")
    @app_commands.guild_only()
    @app_commands.rename(previous_week="지난주")
    async def weekly_report(
        self,
        interaction: discord.Interaction,
        previous_week: bool = False,
    ) -> None:
        """Handle /주간보고."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "서버에서만 사용할 수 있는 명령입니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.report_service.get_weekly_report(
                guild_id=guild.id,
                now=datetime.now(timezone.utc),
                previous_week=previous_week,
            )
        except Exception:
            logger.exception("Weekly report failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "주간보고 생성 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_weekly_report_message(result),
            ephemeral=False,
        )

    def _build_personal_message(self, result: PersonalReportResult) -> str:
        """Build the personal report message."""

        if not result.found:
            return "출석 대상자로 등록되어 있지 않습니다."

        recent_lines = []
        for event in result.recent_events or []:
            occurred_at = format_local_hhmm(
                datetime.fromisoformat(event["created_at"]),
                result.timezone_name or "Asia/Seoul",
            )
            recent_lines.append(
                f"- {occurred_at} {event['delta']:+d}점 {event['description']}"
            )
        if not recent_lines:
            recent_lines.append("- 최근 점수 변동 없음")

        return (
            f"내정보: {result.display_name}\n\n"
            f"총점: {result.total_score}점\n"
            f"현재 계급: {result.rank}\n"
            f"참여 세션: {result.total_sessions}회\n"
            f"정상 출석: {result.present_count}회\n"
            f"지각: {result.late_count}회\n"
            f"결석: {result.absent_count}회\n"
            f"사유 지각: {result.excused_late_count}회\n"
            f"사유 결석: {result.excused_absent_count}회\n"
            f"출석률: {result.attendance_rate:.1f}%\n"
            f"연속 출석: {result.current_streak}회\n\n"
            "최근 점수 변동\n"
            + "\n".join(recent_lines)
        )

    def _build_ranking_message(self, result: RankingResult) -> str:
        """Build the ranking response."""

        if not result.configured:
            return "초기설정이 필요합니다."

        entries = result.entries or []
        if not entries:
            return "랭킹에 표시할 활성 대상자가 없습니다."

        lines = ["출석 랭킹\n"]
        for entry in entries:
            lines.append(
                f"{entry.rank_no}. <@{entry.discord_id}> "
                f"{entry.total_score}점 / {entry.rank} / 연속 {entry.current_streak}회"
            )
        return "\n".join(lines)

    def _build_public_report_message(
        self,
        result: PublicReportResult,
        target_mention: str,
    ) -> str:
        """Build a public-safe member report message."""

        if not result.found:
            return "대상자의 근태 기록을 찾을 수 없습니다."

        event_lines = [
            f"- {event['delta']:+d}점 {event['description']}"
            for event in (result.recent_events or [])[:5]
        ] or ["- 최근 점수 변동 없음"]

        evaluation_lines = []
        for evaluation in (result.recent_evaluations or [])[:3]:
            reason = evaluation["reason"]
            if len(reason) > 100:
                reason = reason[:100] + "..."
            evaluation_lines.append(
                f"- #{evaluation['id']} {evaluation['score']:+d}점 {reason}"
            )
        if not evaluation_lines:
            evaluation_lines.append("- 최근 공개 평가 없음")

        return (
            f"근태 리포트: {target_mention}\n\n"
            f"총점: {result.total_score}점\n"
            f"계급: {result.rank}\n"
            f"출석률: {result.attendance_rate:.1f}%\n"
            f"현재 연속 출석: {result.current_streak}회\n"
            f"참여 세션: {result.total_sessions}회\n"
            f"정상/지각/사유지각: "
            f"{result.present_count}/{result.late_count}/{result.excused_late_count}\n"
            f"무단결석/사유결석: "
            f"{result.absent_count}/{result.excused_absent_count}\n\n"
            "최근 점수 변동\n"
            + "\n".join(event_lines)
            + "\n\n최근 평가\n"
            + "\n".join(evaluation_lines)
        )

    def _build_weekly_report_message(self, result: WeeklyReportResult) -> str:
        """Build a guild weekly report message."""

        if not result.configured:
            return "초기설정이 필요합니다."

        row_lines = []
        for row in (result.member_rows or [])[:10]:
            row_lines.append(
                f"- <@{row.discord_id}> 출석률 {row.attendance_rate:.1f}% / "
                f"주간점수 {row.weekly_score:+d}"
            )
        if not row_lines:
            row_lines.append("- 집계할 기록 없음")

        top_line = "없음"
        if result.top_member is not None:
            top_line = (
                f"<@{result.top_member.discord_id}> "
                f"{result.top_member.weekly_score:+d}점"
            )

        return (
            "주간 근태 보고\n\n"
            f"집계 대상: {result.total_targets}건\n"
            f"전체 출석률: {result.attendance_rate:.1f}%\n"
            f"정상/지각/사유지각: "
            f"{result.present_count}/{result.late_count}/{result.excused_late_count}\n"
            f"무단결석/사유결석: "
            f"{result.absent_count}/{result.excused_absent_count}\n"
            f"최우수 대상자: {top_line}\n\n"
            "대상자별 요약\n"
            + "\n".join(row_lines)
        )
