"""Central score policy for attendance-related events."""


ATTENDANCE_SCORE_BY_STATUS: dict[str, int] = {
    "PRESENT": 3,
    "LATE": 1,
    "ABSENT": -3,
    "EXCUSED_LATE": 0,
    "EXCUSED_ABSENT": -1,
}

STREAK_BONUS_3_DAYS = 2
STREAK_BONUS_7_DAYS = 5


def get_attendance_score(status: str) -> int:
    """Return the point delta for a stored attendance record status.

    Args:
        status: Database attendance status. Expected values are PRESENT, LATE,
            ABSENT, EXCUSED_LATE, and EXCUSED_ABSENT.

    Returns:
        The configured point delta for the status.

    Raises:
        ValueError: If ``status`` is not part of the attendance score policy.
    """

    try:
        return ATTENDANCE_SCORE_BY_STATUS[status]
    except KeyError as exc:
        raise ValueError(f"Unknown attendance status for scoring: {status!r}") from exc
