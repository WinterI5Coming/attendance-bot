"""출석 관련 이벤트의 중앙 점수 정책을 정의한다."""


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
    """저장된 출석 상태에 대응하는 점수 변동값을 반환한다.

    인자:
        status: 데이터베이스에 저장된 출석 상태. PRESENT, LATE, ABSENT,
            EXCUSED_LATE, EXCUSED_ABSENT 값을 기대한다.

    반환:
        해당 상태에 설정된 점수 변동값.

    예외:
        ValueError: ``status``가 출석 점수 정책에 포함되지 않은 경우.
    """

    try:
        return ATTENDANCE_SCORE_BY_STATUS[status]
    except KeyError as exc:
        raise ValueError(f"Unknown attendance status for scoring: {status!r}") from exc
