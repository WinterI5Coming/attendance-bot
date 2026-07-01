"""Report-related slash commands."""

from datetime import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.report_service import PersonalReportResult, ReportService
from bot.utils.time_utils import format_local_hhmm


logger = logging.getLogger(__name__)


class ReportsCog(commands.Cog):
    """Slash commands for personal attendance reports."""

    def __init__(self, report_service: ReportService) -> None:
        """Create the Cog.

        Args:
            report_service: Service that builds personal reports.
        """

        self.report_service = report_service

    @app_commands.command(
        name="내정보",
        description="내 출석 통계와 점수를 조회합니다.",
    )
    @app_commands.guild_only()
    async def my_info(self, interaction: discord.Interaction) -> None:
        """Handle the /내정보 command."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.report_service.get_my_report(
                guild_id=guild.id,
                discord_id=interaction.user.id,
            )
        except Exception:
            logger.exception(
                "내정보 조회 중 오류가 발생했습니다. guild_id=%s discord_id=%s",
                guild.id,
                interaction.user.id,
            )
            await interaction.response.send_message(
                "내정보 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_message(result),
            ephemeral=True,
        )

    def _build_message(self, result: PersonalReportResult) -> str:
        """Build the personal report message."""

        if not result.found:
            return "출석 대원으로 등록되어 있지 않습니다."

        recent_lines = []
        for event in result.recent_events or []:
            occurred_at = format_local_hhmm(
                datetime.fromisoformat(event["created_at"]),
                result.timezone_name or "Asia/Seoul",
            )
            recent_lines.append(
                f"- {occurred_at} {event['delta']:+d}점: {event['description']}"
            )

        if not recent_lines:
            recent_lines.append("- 최근 점수 변화가 없습니다.")

        return (
            f"내정보: {result.display_name}\n\n"
            f"총점: {result.total_score}점\n"
            f"현재 계급: {result.rank}\n"
            f"참여 대상 세션: {result.total_sessions}회\n"
            f"정상 출석: {result.present_count}회\n"
            f"지각: {result.late_count}회\n"
            f"결석: {result.absent_count}회\n"
            f"사유 지각: {result.excused_late_count}회\n"
            f"사유 결석: {result.excused_absent_count}회\n"
            f"출석률: {result.attendance_rate:.1f}%\n"
            "연속 출석: 다음 단계에서 제공 예정\n\n"
            "최근 점수 변화\n"
            + "\n".join(recent_lines)
        )
