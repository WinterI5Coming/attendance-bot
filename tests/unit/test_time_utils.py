"""Tests for attendance time parsing and timezone conversion."""

from datetime import date, datetime, time, timezone

import pytest

from bot.utils.time_utils import build_session_window, parse_hhmm


def test_parse_hhmm_accepts_strict_24_hour_values():
    assert parse_hhmm("21:30") == time(21, 30)
    assert parse_hhmm("00:00") == time(0, 0)
    assert parse_hhmm("23:59") == time(23, 59)


@pytest.mark.parametrize(
    "value",
    [
        "9:30",
        "21:70",
        "hello",
        "",
        " ",
    ],
)
def test_parse_hhmm_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_hhmm(value)


def test_build_session_window_converts_asia_seoul_to_utc():
    window = build_session_window(
        attendance_date=date(2026, 7, 2),
        attendance_start="21:30",
        late_deadline="21:40",
        close_deadline="21:45",
        timezone_name="Asia/Seoul",
    )

    assert window.start_at == datetime(2026, 7, 2, 12, 30, tzinfo=timezone.utc)
    assert window.late_at == datetime(2026, 7, 2, 12, 40, tzinfo=timezone.utc)
    assert window.close_at == datetime(2026, 7, 2, 12, 45, tzinfo=timezone.utc)


def test_build_session_window_rejects_invalid_timezone():
    with pytest.raises(ValueError):
        build_session_window(
            attendance_date=date(2026, 7, 2),
            attendance_start="21:30",
            late_deadline="21:40",
            close_deadline="21:45",
            timezone_name="Invalid/Timezone",
        )


def test_build_session_window_rejects_equal_start_and_late():
    with pytest.raises(ValueError):
        build_session_window(
            attendance_date=date(2026, 7, 2),
            attendance_start="21:30",
            late_deadline="21:30",
            close_deadline="21:45",
            timezone_name="Asia/Seoul",
        )


def test_build_session_window_rejects_late_after_close():
    with pytest.raises(ValueError):
        build_session_window(
            attendance_date=date(2026, 7, 2),
            attendance_start="21:30",
            late_deadline="21:50",
            close_deadline="21:45",
            timezone_name="Asia/Seoul",
        )
