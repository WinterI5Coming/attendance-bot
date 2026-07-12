"""Discord UI 포매터의 기본 동작을 검증한다."""

from bot.ui.formatters import (
    format_adjustment_status,
    format_attendance_status,
    format_bool,
    format_score,
    truncate,
)


def test_format_score_uses_sign_and_korean_suffix():
    """점수 증감은 부호와 `점` 단위를 함께 표시한다."""

    assert format_score(3) == "+3점"
    assert format_score(-2) == "-2점"
    assert format_score(0) == "변경 없음"


def test_status_formatters_translate_known_codes():
    """출석과 조정 상태 코드는 사용자용 한국어 라벨로 변환한다."""

    assert format_attendance_status("PRESENT") == "정상 출석"
    assert format_attendance_status("EXCUSED_ABSENT") == "사유 결석"
    assert format_adjustment_status("ACTIVE") == "활성"
    assert format_bool(True) == "사용"
    assert format_bool(False) == "미사용"


def test_truncate_keeps_short_text_and_limits_long_text():
    """긴 field 값은 Discord 제한에 맞춰 말줄임 처리한다."""

    assert truncate("abc", 5) == "abc"
    assert truncate("abcdef", 5) == "ab..."
