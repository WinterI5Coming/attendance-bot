"""Tests for score-to-rank policy boundaries."""

import pytest

from bot.policies.rank_policy import get_rank


@pytest.mark.parametrize(
    ("score", "rank"),
    [
        (-1, "폐급"),
        (0, "이병"),
        (9, "이병"),
        (10, "일병"),
        (24, "일병"),
        (25, "상병"),
        (44, "상병"),
        (45, "병장"),
        (69, "병장"),
        (70, "특급전사"),
    ],
)
def test_get_rank_boundaries(score, rank):
    assert get_rank(score) == rank
