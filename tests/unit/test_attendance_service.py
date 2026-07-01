"""Tests for pure attendance time classification."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.services.attendance_service import AttendanceTimeResult, classify_attendance


SEOUL = ZoneInfo("Asia/Seoul")


def _dt(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, 2, hour, minute, second, tzinfo=SEOUL)


@pytest.fixture
def attendance_window():
    return {
        "start_at": _dt(21, 30),
        "late_at": _dt(21, 40),
        "close_at": _dt(21, 45),
    }


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_dt(21, 29, 59), AttendanceTimeResult.NOT_OPEN),
        (_dt(21, 30), AttendanceTimeResult.PRESENT),
        (_dt(21, 39, 59), AttendanceTimeResult.PRESENT),
        (_dt(21, 40), AttendanceTimeResult.LATE),
        (_dt(21, 44, 59), AttendanceTimeResult.LATE),
        (_dt(21, 45), AttendanceTimeResult.CLOSED),
        (_dt(21, 50), AttendanceTimeResult.CLOSED),
    ],
)
def test_classify_attendance_boundaries(attendance_window, now, expected):
    assert classify_attendance(now=now, **attendance_window) is expected


def test_classify_attendance_rejects_naive_now(attendance_window):
    with pytest.raises(ValueError):
        classify_attendance(
            now=datetime(2026, 7, 2, 21, 30),
            **attendance_window,
        )


def test_classify_attendance_rejects_naive_start(attendance_window):
    attendance_window["start_at"] = datetime(2026, 7, 2, 21, 30)

    with pytest.raises(ValueError):
        classify_attendance(
            now=_dt(21, 30),
            **attendance_window,
        )


def test_classify_attendance_rejects_equal_start_and_late(attendance_window):
    attendance_window["late_at"] = attendance_window["start_at"]

    with pytest.raises(ValueError):
        classify_attendance(
            now=_dt(21, 30),
            **attendance_window,
        )


def test_classify_attendance_rejects_late_after_close(attendance_window):
    attendance_window["late_at"] = _dt(21, 50)

    with pytest.raises(ValueError):
        classify_attendance(
            now=_dt(21, 30),
            **attendance_window,
        )
