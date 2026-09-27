"""리포트 서비스 결과를 Discord Embed로 변환한다."""

from __future__ import annotations

import discord

from bot.services.report_service import (
    PersonalReportResult,
    PublicReportResult,
    RankingResult,
    WeeklyReportResult,
)
from bot.ui.embed_factory import EMBEDS
from bot.ui.formatters import format_local_time, truncate

NOT_CONFIGURED_TEXT = "⚙️ 아직 초기설정이 완료되지 않았습니다. 먼저 /설정 초기화를 실행해주세요."


def _attendance_breakdown(
    present: int,
    late: int,
    excused_late: int,
    absent: int,
    excused_absent: int,
) -> str:
    return (
        f"✅ 정상 {present} · ⏰ 지각 {late} · 📋 사유지각 {excused_late}\n"
        f"❌ 결석 {absent} · 🛡️ 사유결석 {excused_absent}"
    )


def build_personal_embed(result: PersonalReportResult) -> discord.Embed:
    """`/내정보` 개인 리포트 Embed를 만든다."""

    if not result.found:
        return EMBEDS.error("내정보", "출석 대상자로 등록되어 있지 않습니다.")

    tz = result.timezone_name or "Asia/Seoul"
    recent_lines = [
        f"`{format_local_time(event['created_at'], tz)}` {event['delta']:+d}점 {event['description']}"
        for event in (result.recent_events or [])[:5]
    ] or ["최근 점수 변동 없음"]

    return EMBEDS.info(
        f"👤 {result.display_name}",
        f"**{result.total_score}점** · {result.rank}",
        fields=(
            ("출석률", f"{result.attendance_rate:.1f}%", True),
            ("참여 세션", f"{result.total_sessions}회", True),
            ("연속 출석", f"🔥 {result.current_streak}회", True),
            (
                "출석 내역",
                _attendance_breakdown(
                    result.present_count,
                    result.late_count,
                    result.excused_late_count,
                    result.absent_count,
                    result.excused_absent_count,
                ),
                False,
            ),
            ("최근 점수 변동", truncate("\n".join(recent_lines)), False),
        ),
    )


def _ranking_badge(rank_no: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank_no, "🔻")


def _ranking_comment(total_score: int, rank_no: int) -> str:
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


def build_ranking_embed(result: RankingResult) -> discord.Embed:
    """`/랭킹` Embed를 만든다."""

    if not result.configured:
        return EMBEDS.error("출석 랭킹", NOT_CONFIGURED_TEXT)
    entries = result.entries or []
    if not entries:
        return EMBEDS.info("출석 랭킹", "랭킹에 표시할 활성 대상자가 없습니다.")

    lines = []
    for entry in entries[:20]:
        lines.append(
            f"{_ranking_badge(entry.rank_no)} **{entry.rank_no}위** <@{entry.discord_id}> "
            f"`{entry.total_score:+d}점` · {entry.rank} · 🔥 {entry.current_streak}회\n"
            f"> {_ranking_comment(entry.total_score, entry.rank_no)}"
        )
    return EMBEDS.build(
        title="🏆 출석 랭킹: 오늘의 생존자 명단",
        description="점수판은 친절하지 않습니다. 위는 번쩍이고, 아래는 차갑습니다.\n\n"
        + truncate("\n".join(lines), 3800),
    )


def build_public_report_embed(
    result: PublicReportResult,
    target_mention: str,
) -> discord.Embed:
    """공개 채널에 노출해도 되는 멤버 리포트 Embed를 만든다."""

    if not result.found:
        return EMBEDS.error("근태 리포트", "대상자의 근태 기록을 찾을 수 없습니다.")

    event_lines = [
        f"{event['delta']:+d}점 {event['description']}"
        for event in (result.recent_events or [])[:5]
    ] or ["최근 점수 변동 없음"]
    evaluation_lines = [
        f"`#{evaluation['id']}` {evaluation['score']:+d}점 {truncate(evaluation['reason'], 100)}"
        for evaluation in (result.recent_evaluations or [])[:3]
    ] or ["최근 공개 평가 없음"]

    return EMBEDS.info(
        f"📋 근태 리포트: {result.display_name}",
        f"{target_mention} · **{result.total_score}점** · {result.rank}",
        fields=(
            ("출석률", f"{result.attendance_rate:.1f}%", True),
            ("참여 세션", f"{result.total_sessions}회", True),
            ("연속 출석", f"🔥 {result.current_streak}회", True),
            (
                "출석 내역",
                _attendance_breakdown(
                    result.present_count,
                    result.late_count,
                    result.excused_late_count,
                    result.absent_count,
                    result.excused_absent_count,
                ),
                False,
            ),
            ("최근 점수 변동", truncate("\n".join(event_lines)), False),
            ("최근 평가", truncate("\n".join(evaluation_lines)), False),
        ),
    )


def build_weekly_embed(result: WeeklyReportResult) -> discord.Embed:
    """`/주간보고` Embed를 만든다."""

    if not result.configured:
        return EMBEDS.error("주간 근태 보고", NOT_CONFIGURED_TEXT)

    row_lines = [
        f"<@{row.discord_id}> 출석률 {row.attendance_rate:.1f}% · 주간점수 {row.weekly_score:+d}"
        for row in (result.member_rows or [])[:10]
    ] or ["집계할 기록 없음"]
    top_line = "없음"
    if result.top_member is not None:
        top_line = f"<@{result.top_member.discord_id}> {result.top_member.weekly_score:+d}점"

    return EMBEDS.info(
        "📅 주간 근태 보고",
        f"집계 대상 {result.total_targets}건 · 전체 출석률 **{result.attendance_rate:.1f}%**",
        fields=(
            (
                "출석 내역",
                _attendance_breakdown(
                    result.present_count,
                    result.late_count,
                    result.excused_late_count,
                    result.absent_count,
                    result.excused_absent_count,
                ),
                False,
            ),
            ("최우수 대상자", top_line, False),
            ("대상자별 요약", truncate("\n".join(row_lines)), False),
        ),
    )
