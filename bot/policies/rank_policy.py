"""Rank policy based on total score."""


def get_rank(total_score: int) -> str:
    """Return the rank name for a total score.

    Args:
        total_score: Sum of score_events.delta.

    Returns:
        Korean rank label.
    """

    if total_score < 0:
        return "폐급"
    if total_score <= 9:
        return "이병"
    if total_score <= 24:
        return "일병"
    if total_score <= 44:
        return "상병"
    if total_score <= 69:
        return "병장"
    return "특급전사"
