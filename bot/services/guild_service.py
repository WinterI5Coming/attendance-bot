"""Discord 서버의 최초 설정 규칙을 담당한다."""

from typing import Any

from dataclasses import dataclass
from datetime import datetime, timezone

from bot.config import Settings
from bot.repositories.guild_repository import GuildRepository
from bot.utils.time_utils import build_session_window, get_server_today, parse_hhmm


@dataclass(frozen=True)
class GuildSetupResult:
    """서버 초기설정 처리 결과."""

    created: bool
    attendance_days: str
    attendance_start: str
    late_deadline: str
    close_deadline: str
    excuse_mode: str


@dataclass(frozen=True)
class AttendanceTimeUpdateResult:
    """출석 시간 설정 변경 결과."""

    status: str
    attendance_start: str | None = None
    late_deadline: str | None = None
    close_deadline: str | None = None
    today_session_status: str | None = None


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
            excuse_deadline_time=self.settings.default_excuse_deadline_time,
            excuse_deadline_days_before=self.settings.default_excuse_deadline_days_before,
            require_excuse_approval=self.settings.default_require_excuse_approval,
            allow_late_excuse=self.settings.default_allow_late_excuse,
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

    async def list_all_settings(self) -> list[dict[str, Any]]:
        """자동 출석 작업이 순회할 모든 서버 설정을 조회한다.

        Returns:
            guild_settings 행 목록.
        """

        return await self.repository.list_all_settings()

    async def update_attendance_times(
        self,
        *,
        guild_id: int,
        attendance_start: str,
        late_deadline: str,
        close_deadline: str,
        now: datetime,
    ) -> AttendanceTimeUpdateResult:
        """서버의 출석 시작, 지각, 마감 시각을 변경한다."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")

        try:
            start_time = parse_hhmm(attendance_start)
            late_time = parse_hhmm(late_deadline)
            close_time = parse_hhmm(close_deadline)
        except ValueError:
            return AttendanceTimeUpdateResult(status="INVALID_TIME")

        if not start_time < late_time < close_time:
            return AttendanceTimeUpdateResult(status="INVALID_ORDER")

        guild_id_text = str(guild_id)
        settings = await self.repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return AttendanceTimeUpdateResult(status="NOT_CONFIGURED")

        now_text = now.isoformat()
        updated = await self.repository.update_attendance_times(
            guild_id=guild_id_text,
            attendance_start=attendance_start,
            late_deadline=late_deadline,
            close_deadline=close_deadline,
            now=now_text,
        )
        if not updated:
            return AttendanceTimeUpdateResult(status="NOT_CONFIGURED")

        local_date = get_server_today(now, settings["timezone"])
        window = build_session_window(
            attendance_date=local_date,
            attendance_start=attendance_start,
            late_deadline=late_deadline,
            close_deadline=close_deadline,
            timezone_name=settings["timezone"],
        )
        if now < window.start_at:
            session_status = "SCHEDULED"
            opened_at = None
        else:
            session_status = "OPEN"
            opened_at = now_text

        today_session_status = await self.repository.update_session_window_if_unrecorded(
            guild_id=guild_id_text,
            attendance_date=local_date.isoformat(),
            start_at=window.start_at.isoformat(),
            late_at=window.late_at.isoformat(),
            close_at=window.close_at.isoformat(),
            status=session_status,
            opened_at=opened_at,
            now=now_text,
        )

        return AttendanceTimeUpdateResult(
            status="UPDATED",
            attendance_start=attendance_start,
            late_deadline=late_deadline,
            close_deadline=close_deadline,
            today_session_status=today_session_status,
        )
