"""출석 리포트와 랭킹 관련 슬래시 명령어를 제공한다."""

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
    """개인, 공개, 랭킹, 주간 리포트 명령어를 제공한다."""

    def __init__(self, report_service: ReportService) -> None:
        """리포트 조회에 사용할 서비스를 저장한다."""

        self.report_service = report_service

    @app_commands.command(name="내정보", description="내 출석 통계와 점수를 조회합니다.")
    @app_commands.guild_only()
    async def my_info(self, interaction: discord.Interaction) -> None:
        """/내정보 명령을 처리한다."""

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
        """/랭킹 명령을 처리한다."""

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
        """/리포트 명령을 처리한다."""

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
        """/주간보고 명령을 처리한다."""

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
        """개인 리포트 메시지를 만든다."""

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
        """랭킹 응답 메시지를 만든다."""

        if not result.configured:
            return "초기설정이 필요합니다."

        entries = result.entries or []
        if not entries:
            return "랭킹에 표시할 활성 대상자가 없습니다."

        lines = [
            "🏆 **출석 랭킹: 오늘의 생존자 명단**",
            "점수판은 친절하지 않습니다. 위는 번쩍이고, 아래는 차갑습니다.\n",
        ]
        for entry in entries:
            badge = self._ranking_badge(entry.rank_no)
            comment = self._ranking_comment(entry.total_score, entry.rank_no)
            lines.append(
                f"{badge} **{entry.rank_no}위** <@{entry.discord_id}>\n"
                f"> `점수 {entry.total_score:+d}` · **{entry.rank}** · "
                f"🔥 연속 {entry.current_streak}회\n"
                f"> {comment}"
            )
        return "\n".join(lines)

    def _ranking_badge(self, rank_no: int) -> str:
        """순위에 맞는 장식 배지를 반환한다."""

        badges = {
            1: "🥇",
            2: "🥈",
            3: "🥉",
        }
        return badges.get(rank_no, "🔻")

    def _ranking_comment(self, total_score: int, rank_no: int) -> str:
        """랭킹 줄에 붙일 짧은 평가 멘트를 만든다."""

        if total_score >= 500:
            return "✨ 점수판 위에 이름을 새겼습니다. 보는 맛이 있습니다."
        if total_score >= 150:
            return "🌟 상위권 공기가 다릅니다. 지금 꽤 화려합니다."
        if total_score >= 70:
            return "⚔️ 제법 매섭습니다. 아래쪽에서 올려다보면 목 아픈 위치."
        if total_score >= 25:
            return "🔥 아직 왕관은 멀지만, 체면은 확실히 챙겼습니다."
        if total_score >= 10:
            return "🧱 중간은 지켰습니다. 여기서 미끄러지면 바로 추락입니다."
        if total_score >= 0:
            return "🫥 살아는 있습니다. 점수판이 아직 봐주는 중입니다."
        if rank_no == 1:
            return "🕳️ 음수인데 1위라면 서버 전체가 같이 반성해야 합니다."
        if total_score <= -100:
            return "💀 바닥 밑 지하실입니다. 점수판도 눈을 피했습니다."
        if total_score <= -50:
            return "🧨 내려가는 폼이 예술입니다. 분노를 부르는 역주행."
        if total_score <= -10:
            return "🪦 굴욕 구간 입성. 이제부터는 올라오는 것도 콘텐츠입니다."
        return "🥀 음수입니다. 점수판 맨바닥 청소 담당."

    def _build_public_report_message(
        self,
        result: PublicReportResult,
        target_mention: str,
    ) -> str:
        """공개 채널에 노출해도 되는 멤버 리포트 메시지를 만든다."""

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
        """서버 주간 리포트 메시지를 만든다."""

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
