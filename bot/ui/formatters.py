"""Discord 메시지에 표시할 값을 한국어로 포맷팅하는 함수 모음."""

from datetime import datetime

from bot.utils.time_utils import format_local_hhmm


ATTENDANCE_STATUS_LABELS = {
    "PRESENT": "정상 출석",
    "LATE": "지각",
    "ABSENT": "결석",
    "EXCUSED_LATE": "사유 지각",
    "EXCUSED_ABSENT": "사유 결석",
}

VERIFICATION_STATUS_LABELS = {
    "PENDING": "검증 대기",
    "VERIFIED": "검증 성공",
    "FAILED": "검증 실패",
    "WAIVED": "검증 면제",
}

ADJUSTMENT_STATUS_LABELS = {
    "ACTIVE": "활성",
    "CANCELLED": "취소됨",
}


def truncate(value: str, limit: int = 1024) -> str:
    """Discord Embed field 제한에 맞게 긴 문자열을 잘라낸다."""

    if len(value) <= limit:
        return value
    return value[: max(limit - 3, 0)] + "..."


def format_score(delta: int) -> str:
    """점수 증감을 사용자가 읽기 좋은 문자열로 변환한다."""

    if delta == 0:
        return "변경 없음"
    return f"{delta:+d}점"


def format_bool(value: bool) -> str:
    """bool 값을 한국어 상태로 표시한다."""

    return "사용" if value else "미사용"


def format_attendance_status(status: str | None) -> str:
    """저장된 출석 상태 코드를 한국어 라벨로 바꾼다."""

    if status is None:
        return "-"
    return ATTENDANCE_STATUS_LABELS.get(status, status)


def format_verification_status(status: str | None) -> str:
    """저장된 음성 검증 상태 코드를 한국어 라벨로 바꾼다."""

    if status is None:
        return "-"
    return VERIFICATION_STATUS_LABELS.get(status, status)


def format_adjustment_status(status: str | None) -> str:
    """저장된 감면/면제 상태 코드를 한국어 라벨로 바꾼다."""

    if status is None:
        return "-"
    return ADJUSTMENT_STATUS_LABELS.get(status, status)


def format_local_time(value: str | None, timezone_name: str | None) -> str:
    """UTC ISO 문자열을 서버 시간대의 `HH:MM` 문자열로 변환한다."""

    if value is None or timezone_name is None:
        return "-"
    parsed = datetime.fromisoformat(value)
    formatted = format_local_hhmm(parsed, timezone_name)
    return "-" if formatted is None else formatted
