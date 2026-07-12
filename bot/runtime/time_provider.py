"""현재 시각을 일관된 방식으로 제공한다."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class TimeProvider:
    """
    애플리케이션 전반에서 사용할 현재 시각 공급자.

    운영 코드에서는 실제 현재 시각을 반환하고, 테스트에서는 같은 인터페이스를
    가진 대체 객체를 주입해 시간 의존 동작을 고정할 수 있다.
    """

    def __init__(self, *, local_timezone: str = "Asia/Seoul") -> None:
        """
        지역 시각 변환에 사용할 기본 시간대를 설정한다.

        Args:
            local_timezone: 사용자에게 표시할 기본 IANA 시간대 이름.
        """

        self.local_timezone = ZoneInfo(local_timezone)

    def now_utc(self) -> datetime:
        """
        timezone-aware UTC 현재 시각을 반환한다.

        Returns:
            UTC 시간대가 지정된 현재 datetime.
        """

        return datetime.now(timezone.utc)

    def now_local(self) -> datetime:
        """
        기본 지역 시간대 기준 현재 시각을 반환한다.

        Returns:
            생성 시 지정한 지역 시간대가 적용된 현재 datetime.
        """

        return self.now_utc().astimezone(self.local_timezone)
