"""누적 점수에 따른 계급 산정 정책을 정의한다."""


def get_rank(total_score: int) -> str:
    """누적 점수에 해당하는 계급 이름을 반환한다.

    인자:
        total_score: score_events.delta를 합산한 누적 점수.

    반환:
        한국어 계급 라벨.
    """

    if total_score <= -100:
        return "지하 100층 폐급왕"
    if total_score <= -50:
        return "분노유발 낙오병"
    if total_score <= -10:
        return "굴욕의 바닥병"
    if total_score < 0:
        return "수치의 폐급"
    if total_score <= 9:
        return "먼지 이병"
    if total_score <= 24:
        return "불꽃 일병"
    if total_score <= 44:
        return "폭주 상병"
    if total_score <= 69:
        return "폭풍 병장"
    if total_score <= 149:
        return "찬란한 특급전사"
    if total_score <= 499:
        return "황금 전장의 영웅"
    return "전설의 광휘 사령관"
