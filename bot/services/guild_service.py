"""Discord 서버의 최초 설정 규칙을 담당한다."""

from typing import Any

from dataclasses import dataclass
from datetime import datetime, timezone

from bot.config import Settings
from bot.repositories.guild_repository import GuildRepository


@dataclass(frozen=True)
class GuildSetupResult:
    """서버 초기설정 처리 결과."""

    created: bool
    attendance_days: str
    attendance_start: str
    late_deadline: str
    close_deadline: str
    excuse_mode: str


class GuildService:
    """Discord 서버 초기설정과 조회를 담당한다."""

    def __init__(
        self,
        repository: GuildRepository,
        settings: Settings,
    ) -> None:
        """Service 의존성을 초기화한다."""

        self.repository = repository
        self.settings = settings

    async def initialize_guild(
        self,
        *,
        guild_id: int,
        officer_role_id: int,
        attendance_channel_id: int,
        announcement_channel_id: int,
    ) -> GuildSetupResult:
        """Discord 서버의 기본 근태 설정을 생성한다.

        Args:
            guild_id:
                설정할 Discord 서버 ID.
            officer_role_id:
                간부 권한으로 사용할 Discord 역할 ID.
            attendance_channel_id:
                출석 명령을 사용할 채널 ID.
            announcement_channel_id:
                자동 공지를 보낼 채널 ID.

        Returns:
            생성 여부와 기본 출석 설정.
        """

        created_at = datetime.now(
            timezone.utc
        ).isoformat()

        created = await self.repository.create_settings(
            guild_id=str(guild_id),
            timezone_name=self.settings.timezone,
            attendance_days=self.settings.default_attendance_days,
            attendance_start=self.settings.default_attendance_start,
            late_deadline=self.settings.default_late_deadline,
            close_deadline=self.settings.default_close_deadline,
            excuse_mode=self.settings.default_excuse_mode,
            officer_role_id=str(officer_role_id),
            attendance_channel_id=str(attendance_channel_id),
            announcement_channel_id=str(
                announcement_channel_id
            ),
            created_at=created_at,
        )

        return GuildSetupResult(
            created=created,
            attendance_days=self.settings.default_attendance_days,
            attendance_start=self.settings.default_attendance_start,
            late_deadline=self.settings.default_late_deadline,
            close_deadline=self.settings.default_close_deadline,
            excuse_mode=self.settings.default_excuse_mode,
        )
        
    async def get_settings(
        self,
        guild_id: int,
    ) -> dict[str, Any] | None:
        """Discord 서버의 근태 설정을 조회한다.

        Args:
            guild_id:
                조회할 Discord 서버 ID.

        Returns:
            설정이 존재하면 딕셔너리, 없으면 None.
        """

        return await self.repository.get_by_guild_id(
            str(guild_id)
        )