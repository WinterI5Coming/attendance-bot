"""사유 신청 서비스 결과를 Discord 메시지/Embed로 변환한다."""

from __future__ import annotations

from typing import Any

import discord

from bot.services.excuse_policy import EXCUSE_TYPE_LABELS
from bot.services.excuse_service import ExcuseResult, ExcuseStatus
from bot.ui.embed_factory import EMBEDS
from bot.ui.formatters import format_excuse_status, truncate


def excuse_type_label(code: str | None) -> str:
    """저장된 사유 유형 코드를 한국어 라벨로 바꾼다."""

    return EXCUSE_TYPE_LABELS.get(code, code or "-")


def message_for_status(result: ExcuseResult) -> str:
    """사유 서비스 결과 상태를 사용자 문구로 변환한다."""

    if result.status is ExcuseStatus.TOO_LATE_TO_REQUEST:
        deadline = result.deadline_at.isoformat() if result.deadline_at else "알 수 없음"
        return (
            "사유 신청 기간이 마감되었습니다.\n\n"
            f"신청 마감: {deadline}\n\n"
            "마감 이후에는 일반 사유 신청을 제출할 수 없습니다.\n"
            "긴급한 사정이 있다면 관리자에게 문의해주세요."
        )
    messages = {
        ExcuseStatus.DUPLICATE_ACTIVE_REQUEST: "해당 날짜에 이미 등록된 사유 신청이 있습니다.",
        ExcuseStatus.INVALID_DATE: "날짜는 YYYY-MM-DD 형식으로 입력해주세요.",
        ExcuseStatus.PAST_DATE: "지난 날짜의 사유는 신청할 수 없습니다.",
        ExcuseStatus.NOT_ATTENDANCE_DAY: "선택한 날짜에는 등록된 출석 일정이 없습니다.",
        ExcuseStatus.INVALID_TIME: "시간은 HH:MM 형식으로 입력해주세요.",
        ExcuseStatus.INVALID_REASON: "사유는 2자 이상 500자 이하로 입력해주세요.",
        ExcuseStatus.NOT_REGISTERED: "출석 대원으로 등록되어 있지 않습니다.",
        ExcuseStatus.NOT_SESSION_MEMBER: "해당 날짜 출석 세션의 참여 대상이 아닙니다.",
        ExcuseStatus.NOT_FOUND: "사유 신청을 찾을 수 없습니다.",
        ExcuseStatus.NOT_OWNER: "본인 신청만 조회하거나 취소할 수 있습니다.",
        ExcuseStatus.INVALID_STATUS: "현재 상태에서는 처리할 수 없습니다.",
        ExcuseStatus.CANCELLED: "사유 신청을 취소했습니다.",
        ExcuseStatus.APPROVED: f"✅ 사유 신청을 승인했습니다. 점수 보정: {result.score_delta:+d}",
        ExcuseStatus.REJECTED: "⛔ 사유 신청을 거절했습니다.",
        ExcuseStatus.ALREADY_DECIDED: "이미 처리된 사유 신청입니다.",
        ExcuseStatus.ALREADY_APPLIED: "이미 출석 기록에 반영되어 취소할 수 없습니다.",
        ExcuseStatus.NOT_CONFIGURED: "아직 초기설정이 완료되지 않았습니다.",
        ExcuseStatus.ADMIN_OVERRIDE_CREATED: "관리자 예외 사유가 승인 상태로 등록되었습니다.",
        ExcuseStatus.POLICY_UPDATED: "사유 신청 정책을 변경했습니다.",
    }
    return messages.get(result.status, "요청을 처리했습니다.")


def build_create_message(result: ExcuseResult) -> str:
    """사유 신청 생성 결과 문구를 만든다."""

    if result.request is None or result.status is not ExcuseStatus.CREATED_PENDING:
        return message_for_status(result)

    request = result.request
    return (
        "📝 사유 신청이 접수되었습니다.\n\n"
        f"대상 날짜: {request['target_date']}\n"
        f"신청 유형: {excuse_type_label(request.get('excuse_type'))}\n"
        f"신청 마감: {request.get('deadline_at')}\n"
        "처리 상태: 간부 승인 대기\n\n"
        "간부가 승인하면 출석 판정에 반영됩니다. `/사유 목록`에서 진행 상태를 볼 수 있습니다."
    )


def build_list_embed(result: ExcuseResult, *, title: str = "사유 신청 목록") -> discord.Embed:
    """사유 신청 목록 Embed를 만든다."""

    if result.status is ExcuseStatus.NOT_OWNER:
        return EMBEDS.error(title, message_for_status(result))
    rows = [] if result.request is None else result.request.get("rows", [])
    if not rows:
        return EMBEDS.info(title, "조회할 사유 신청이 없습니다.")
    lines = [
        f"`#{row['id']}` {row['target_date']} · {excuse_type_label(row.get('excuse_type'))} · "
        f"{format_excuse_status(row['status'])} · <@{row['discord_id']}>"
        for row in rows[:20]
    ]
    return EMBEDS.info(title, truncate("\n".join(lines), 3500))


def build_detail_embed(row: dict[str, Any]) -> discord.Embed:
    """사유 신청 한 건의 상세 Embed를 만든다."""

    return EMBEDS.info(
        f"사유 신청 #{row['id']}",
        truncate(row["reason"], 1000),
        fields=(
            ("신청자", f"<@{row['discord_id']}>", True),
            ("대상 날짜", row["target_date"], True),
            ("유형", excuse_type_label(row.get("excuse_type")), True),
            ("상태", format_excuse_status(row["status"]), True),
            ("신청 시각", str(row.get("requested_at") or "-"), True),
            ("마감 시각", str(row.get("deadline_at") or "-"), True),
            ("관리자 예외", "예" if row.get("is_admin_override") else "아니오", True),
        ),
    )


def build_pending_notice_embed(request: dict[str, Any], requester: discord.abc.User) -> discord.Embed:
    """새 사유 신청을 간부에게 알리는 공지 Embed. 사유 본문은 포함하지 않는다."""

    return EMBEDS.admin(
        "📝 새 사유 신청이 접수되었습니다",
        "아래 **검토하기** 버튼으로 승인 또는 거절할 수 있습니다. (간부 전용)",
        fields=(
            ("신청자", requester.mention, True),
            ("대상 날짜", request["target_date"], True),
            ("유형", excuse_type_label(request.get("excuse_type")), True),
        ),
    )


def build_policy_message(settings: dict[str, Any]) -> str:
    """현재 사유 신청 정책 안내 문구를 만든다."""

    days = int(settings.get("excuse_deadline_days_before") or 1)
    time_text = settings.get("excuse_deadline_time") or "23:00"
    return (
        "현재 사유 신청 정책\n\n"
        f"기준 시간대: {settings['timezone']}\n"
        f"신청 마감: 출석일 {days}일 전 {time_text}\n"
        "관리자 승인: 필수\n"
        "마감 이후 일반 신청: 불가능\n"
        "긴급 예외 등록: 관리자만 가능"
    )


def build_policy_notice(settings: dict[str, Any]) -> str:
    """채널에 공개 공지할 사유 신청 정책 문구를 만든다."""

    days = int(settings.get("excuse_deadline_days_before") or 1)
    time_text = settings.get("excuse_deadline_time") or "23:00"
    return (
        "[출석 사유 신청 안내]\n\n"
        f"결석, 지각 또는 조퇴가 예상되는 경우 대상 출석일 {days}일 전 "
        f"{time_text}까지 사유를 신청해주세요.\n\n"
        "사유 신청 명령어: /사유 신청\n\n"
        "사유 신청은 관리자 승인 후 효력이 발생합니다.\n"
        "마감 이후에는 일반 신청이 불가능합니다.\n\n"
        "사고, 응급 질병 등 긴급한 사정은 관리자에게 별도로 문의해주세요.\n\n"
        "봇이 실행 중인 시간에만 사유 신청이 가능하므로 마감 전에 미리 신청해주세요."
    )
