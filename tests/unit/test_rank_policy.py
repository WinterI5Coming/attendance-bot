"""Tests for score-to-rank policy boundaries."""

import pytest

from bot.policies.rank_policy import get_rank


@pytest.mark.parametrize(
    ("score", "rank"),
    [
        (-101, "지하 100층 폐급왕"),
        (-100, "지하 100층 폐급왕"),
        (-99, "분노유발 낙오병"),
        (-50, "분노유발 낙오병"),
        (-49, "굴욕의 바닥병"),
        (-10, "굴욕의 바닥병"),
        (-9, "수치의 폐급"),
        (-1, "수치의 폐급"),
        (0, "먼지 이병"),
        (9, "먼지 이병"),
        (10, "불꽃 일병"),
        (24, "불꽃 일병"),
        (25, "폭주 상병"),
        (44, "폭주 상병"),
        (45, "폭풍 병장"),
        (69, "폭풍 병장"),
        (70, "찬란한 특급전사"),
        (149, "찬란한 특급전사"),
        (150, "황금 전장의 영웅"),
        (499, "황금 전장의 영웅"),
        (500, "전설의 광휘 사령관"),
    ],
)
def test_get_rank_boundaries(score, rank):
    assert get_rank(score) == rank
