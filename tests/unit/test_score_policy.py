"""Tests for attendance score policy."""

import pytest

from bot.policies.score_policy import get_attendance_score


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("PRESENT", 3),
        ("LATE", 1),
        ("ABSENT", -3),
        ("EXCUSED_LATE", 0),
        ("EXCUSED_ABSENT", -1),
    ],
)
def test_get_attendance_score_returns_configured_delta(status, expected):
    assert get_attendance_score(status) == expected


def test_get_attendance_score_rejects_unknown_status():
    with pytest.raises(ValueError):
        get_attendance_score("UNKNOWN")
