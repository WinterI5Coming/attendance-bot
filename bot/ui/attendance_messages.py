"""출석 관련 서비스 결과를 Discord 메시지/Embed로 변환한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import discord

from bot.services.attendance_service import (
    AttendanceCheckInResult,
    AttendanceCheckInStatus,
    AttendanceCorrectionResult,
    AttendanceCorrectionStatus,
    AttendanceStatusMember,
    AttendanceStatusResult,
)
from bot.services.session_service import SessionPrepareStatus
from bot.services.voice_verification_service import VerificationOverview
from bot.ui.embed_factory import EMBEDS
from bot.ui.formatters import (
    format_attendance_status,
    format_local_time,
    format_verification_failure,
    format_verification_status,
    truncate,
)
from bot.utils.time_utils import format_local_hhmm

NOT_CONFIGURED_TEXT = "⚙️ 아직 초기설정이 완료되지 않았습니다. 먼저 /설정 초기화를 실행해주세요."


def current_voice_location(user: discord.abc.User) -> tuple[int | None, int | None]:
    """사용자가 현재 접속한 음성 채널 ID와 그 카테고리 ID를 반환한다."""

    if not isinstance(user, discord.Member):
        return None, None
    voice_state = user.voice
    if voice_state is None or voice_state.channel is None:
        return None, None
    channel = voice_state.channel
    category = getattr(channel, "category", None)
    return channel.id, None if category is None else category.id


VERIFICATION_STATUS_ICONS = {
    "PENDING": "⏳",
    "VERIFIED": "✅",
    "FAILED": "❌",
    "WAIVED": "🛡️",
}


def build_verification_overview_embed(overview: VerificationOverview) -> discord.Embed:
    """`/출석 검증현황` Embed를 만든다."""

    if not overview.configured:
        return EMBEDS.error("음성 검증 현황", NOT_CONFIGURED_TEXT)
    if not overview.enabled:
        return EMBEDS.info(
            "음성 검증 현황",
            "음성 검증이 꺼져 있습니다. `/설정 음성검증 사용:True 채널:#음성채널`로 켤 수 있습니다.",
        )
    targets = [f"<#{channel_id}>" for channel_id in overview.voice_channel_ids] + [
        f"카테고리 `{category_id}`" for category_id in overview.voice_category_ids
    ]
    if not overview.has_targets:
        return EMBEDS.warning(
            "음성 검증 현황",
            "음성 검증은 켜져 있지만 대상 채널이 없어 동작하지 않습니다. "
            "`/설정 음성검증 채널:#음성채널`로 대상을 지정하세요.",
        )
    if overview.session is None:
        return EMBEDS.info(
            "음성 검증 현황",
            "오늘 출석 세션이 아직 없습니다.",
            fields=(("검증 대상 채널", ", ".join(targets), False),),
        )

    session = overview.session
    tz = overview.timezone_name or "Asia/Seoul"
    required_minutes = int(session.get("required_voice_seconds") or 0) // 60
    end_at = format_local_time(session.get("verification_end_at"), tz)
    lines = []
    for row in overview.rows:
        icon = VERIFICATION_STATUS_ICONS.get(row["status"], "•")
        minutes = int(row["accumulated_seconds"]) // 60
        detail = f"{minutes}분 / {int(row['required_seconds']) // 60}분"
        if row["status"] == "FAILED" and row.get("failure_reason"):
            detail += f" · {format_verification_failure(row['failure_reason'])}"
        lines.append(f"{icon} <@{row['discord_id']}> {format_verification_status(row['status'])} · {detail}")
    counts = {status: sum(1 for row in overview.rows if row["status"] == status) for status in VERIFICATION_STATUS_ICONS}
    return EMBEDS.info(
        f"🎧 음성 검증 현황 ({session['attendance_date']})",
        f"체크인 후 **{required_minutes}분** 이상 음성 채널 체류 시 검증 성공 · 검증 마감 **{end_at}**\n"
        f"⏳ 대기 {counts['PENDING']} · ✅ 성공 {counts['VERIFIED']} · ❌ 실패 {counts['FAILED']}",
        fields=(
            ("검증 대상 채널", ", ".join(targets), False),
            ("대상자", truncate("\n".join(lines) or "체크인한 대원이 없습니다."), False),
        ),
    )


def _score_progress(result: AttendanceCheckInResult) -> str:
    """점수, 연속 출석 보너스, 계급 변경 안내 문구를 만든다."""

    lines = [f"🎯 이번 점수: {result.score_delta:+d}"]
    if result.streak_bonus_delta:
        lines.append(f"🔥 연속 출석 보너스: {result.streak_bonus_delta:+d}")
    lines.append(f"💯 현재 총점: {result.total_score}점")
    if result.rank_changed:
        lines.append(f"🏅 계급 변경: {result.previous_rank} → {result.current_rank}")
    return "\n".join(lines)


def build_check_in_message(result: AttendanceCheckInResult) -> str:
    """체크인 결과를 사용자에게 보여 줄 문구로 변환한다."""

    tz = result.timezone_name
    checked_at = format_local_time(result.checked_at, tz)

    if result.status is AttendanceCheckInStatus.PRESENT:
        return f"✅ 출석 완료: 정상 출석\n{_score_progress(result)}\n🕒 처리 시각: {checked_at}"
    if result.status is AttendanceCheckInStatus.LATE:
        return f"⏰ 출석 완료: 지각\n{_score_progress(result)}\n🕒 처리 시각: {checked_at}"
    if result.status is AttendanceCheckInStatus.EXCUSED_LATE:
        return f"📋 출석 완료: 사유 지각\n{_score_progress(result)}\n🕒 처리 시각: {checked_at}"
    if result.status is AttendanceCheckInStatus.ALREADY_CHECKED:
        return (
            "ℹ️ 이미 오늘 출석 처리가 완료되었습니다.\n"
            f"📌 상태: {format_attendance_status(result.attendance_status)}\n"
            f"🕒 처리 시각: {checked_at}\n"
            f"💯 현재 총점: {result.total_score}점"
        )
    if result.status is AttendanceCheckInStatus.NOT_OPEN:
        return (
            "⏳ 출석 시작 전입니다.\n"
            f"🕒 출석 시작: {format_local_time(result.start_at, tz)}\n"
            f"⏰ 정상 출석 마감: {format_local_time(result.late_at, tz)}\n"
            f"🔒 전체 마감: {format_local_time(result.close_at, tz)}"
        )
    if result.status is AttendanceCheckInStatus.CLOSED:
        return (
            "🔒 오늘 출석은 이미 마감되었습니다.\n"
            f"🕒 마감 시각: {format_local_time(result.close_at, tz)}"
        )
    if result.status is AttendanceCheckInStatus.NOT_REGISTERED:
        return "🚫 출석 대원으로 등록되어 있지 않습니다.\n간부에게 대원 등록을 요청해주세요."
    if result.status is AttendanceCheckInStatus.NOT_SESSION_MEMBER:
        return "🚫 오늘 출석 세션의 참여 대상이 아닙니다.\n다음 출석일부터 참여할 수 있습니다."
    if result.status is AttendanceCheckInStatus.NOT_ATTENDANCE_DAY:
        return "📅 오늘은 출석 일정이 없는 날입니다."
    if result.status is AttendanceCheckInStatus.NO_ACTIVE_MEMBERS:
        return "⚠️ 등록된 활성 대원이 없어 출석 세션을 만들 수 없습니다."
    if result.status is AttendanceCheckInStatus.CANCELLED:
        if result.cancel_reason:
            return f"🚫 오늘 출석 일정은 취소되었습니다.\n사유: {result.cancel_reason}"
        return "🚫 오늘 출석 일정은 취소되었습니다."
    return NOT_CONFIGURED_TEXT


def _member_lines(members: list[AttendanceStatusMember]) -> str:
    return "\n".join(f"<@{member.discord_id}>" for member in members) or "-"


def build_status_embed(result: AttendanceStatusResult) -> discord.Embed:
    """오늘 출석 현황을 Embed로 만든다."""

    if result.status is SessionPrepareStatus.NOT_CONFIGURED:
        return EMBEDS.error("출석 현황", NOT_CONFIGURED_TEXT)
    if result.status is SessionPrepareStatus.NOT_ATTENDANCE_DAY:
        return EMBEDS.info("출석 현황", "📅 오늘은 출석 일정이 없는 날입니다.")
    if result.status is SessionPrepareStatus.NO_ACTIVE_MEMBERS:
        return EMBEDS.warning("출석 현황", "⚠️ 등록된 활성 대원이 없습니다.")
    if result.status is SessionPrepareStatus.ALREADY_CLOSED and result.session is None:
        return EMBEDS.info("출석 현황", "🔒 오늘 출석은 이미 마감되었고 생성된 출석 세션이 없습니다.")

    if result.status is SessionPrepareStatus.CANCELLED:
        description = "🚫 오늘 출석 일정은 취소되었습니다."
        if result.cancel_reason:
            description += f"\n사유: {result.cancel_reason}"
        color = EMBEDS.theme.warning
    else:
        description = (
            f"👥 총원 {result.total_count}명 · ✅ 출석 완료 {result.checked_count}명 · "
            f"❔ 미체크 {len(result.unchecked)}명"
        )
        color = EMBEDS.theme.info

    sections = (
        ("✅ 정상 출석", result.present),
        ("⏰ 지각", result.late),
        ("📋 사유 지각", result.excused_late),
        ("🛡️ 사유 결석", result.excused_absent),
        ("❌ 결석", result.absent),
        ("❔ 미체크", result.unchecked),
    )
    fields = [
        (f"{title} ({len(members)})", truncate(_member_lines(members)), True)
        for title, members in sections
        if members
    ]
    return EMBEDS.build(
        title=f"📊 오늘 출석 현황 ({result.attendance_date})",
        description=description,
        color=color,
        fields=fields,
    )


def build_correction_message(
    result: AttendanceCorrectionResult,
    target_mention: str,
) -> str:
    """출석 정정 결과를 사용자에게 보여 줄 문구로 변환한다."""

    if result.status is AttendanceCorrectionStatus.UPDATED:
        return (
            "✏️ 출석 기록을 수정했습니다.\n\n"
            f"👤 대상: {target_mention}\n"
            f"🗓️ 날짜: {result.attendance_date}\n"
            f"📌 기존 상태: {format_attendance_status(result.previous_status)}\n"
            f"📌 변경 상태: {format_attendance_status(result.new_status)}\n"
            f"🎯 점수 보정: {result.score_delta:+d}\n"
            f"📝 정정 사유: {result.reason}"
        )
    if result.status is AttendanceCorrectionStatus.CREATED:
        return (
            "🆕 출석 기록을 생성했습니다.\n\n"
            f"👤 대상: {target_mention}\n"
            f"🗓️ 날짜: {result.attendance_date}\n"
            f"📌 상태: {format_attendance_status(result.new_status)}\n"
            f"🎯 점수 반영: {result.score_delta:+d}\n"
            f"📝 정정 사유: {result.reason}"
        )
    messages = {
        AttendanceCorrectionStatus.SAME_STATUS: "⚠️ 기존 출석 상태와 변경할 상태가 같습니다.",
        AttendanceCorrectionStatus.NOT_CONFIGURED: NOT_CONFIGURED_TEXT,
        AttendanceCorrectionStatus.INVALID_DATE: "⚠️ 날짜는 YYYY-MM-DD 형식이어야 합니다.",
        AttendanceCorrectionStatus.FUTURE_DATE: "⚠️ 미래 날짜의 출석은 수정할 수 없습니다.",
        AttendanceCorrectionStatus.SESSION_NOT_FOUND: "⚠️ 해당 날짜의 출석 세션이 없습니다.",
        AttendanceCorrectionStatus.TARGET_NOT_FOUND: "⚠️ 대상 사용자가 대원으로 등록된 기록이 없습니다.",
        AttendanceCorrectionStatus.NOT_SESSION_MEMBER: "⚠️ 대상 사용자는 해당 날짜 출석 세션의 참여 대상이 아닙니다.",
        AttendanceCorrectionStatus.INVALID_REASON: "⚠️ 정정 사유는 2자 이상 500자 이하로 입력해주세요.",
    }
    return messages[result.status]


def build_start_announcement_embed(session: dict[str, Any]) -> discord.Embed:
    """출석 시작 공지 Embed를 만든다. 아래에 [출석하기] 버튼이 붙는다."""

    tz = session["timezone"]
    late = format_local_hhmm(datetime.fromisoformat(session["late_at"]), tz)
    close = format_local_hhmm(datetime.fromisoformat(session["close_at"]), tz)
    return EMBEDS.success(
        f"🚀 출석이 시작되었습니다 ({session['attendance_date']})",
        "아래 **출석하기** 버튼을 누르면 바로 체크인됩니다.",
        fields=(
            ("⏰ 정상 출석 마감", late, True),
            ("🔒 전체 마감", close, True),
        ),
    )


def build_close_announcement_embed(session: dict[str, Any]) -> discord.Embed:
    """출석 마감 공지 Embed를 만든다."""

    tz = session["timezone"]
    closed_at = session["closed_at"] or session["close_at"]
    return EMBEDS.info(
        "🔒 출석이 마감되었습니다",
        f"🕒 마감 시각: {format_local_hhmm(datetime.fromisoformat(closed_at), tz)}\n"
        "📊 결과는 `/출석 현황` 또는 `/랭킹`에서 확인할 수 있습니다.",
    )
