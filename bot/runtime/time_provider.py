"""현재 시각을 일관된 방식으로 제공한다."""

from __future__ import annotations

from datetime import UTC, datetime


class TimeProvider:
    """
    애플리케이션 전반에서 사용할 현재 시각 공급자.

    운영 코드에서는 실제 현재 시각을 반환하고, 테스트에서는 같은 인터페이스를
    가진 대체 객체를 주입해 시간 의존 동작을 고정할 수 있다.
    """

    def now_utc(self) -> datetime:
        """timezone-aware UTC 현재 시각을 반환한다."""

        return datetime.now(UTC)
