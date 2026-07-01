"""Minute-based attendance scheduler."""

from datetime import datetime, timezone
import logging
from typing import Any

from discord.ext import tasks

from bot.services.guild_service import GuildService
from bot.services.session_service import SessionService
from bot.utils.time_utils import format_local_hhmm


logger = logging.getLogger(__name__)


class AttendanceScheduler:
    """Runs automatic attendance preparation, opening, closing, and recovery."""

    def __init__(
        self,
        *,
        guild_service: GuildService,
        session_service: SessionService,
        bot: Any | None = None,
    ) -> None:
        """Create the scheduler.

        Args:
            guild_service: Service used to list configured guilds.
            session_service: Service used to prepare and close sessions.
        """

        self.guild_service = guild_service
        self.session_service = session_service
        self.bot = bot
        self._started = False

    def start(self) -> None:
        """Start the 1-minute scheduler loop if it is not already running."""

        if self._started:
            return

        self._started = True
        logger.info("Attendance scheduler started.")
        self._loop.start()

    def stop(self) -> None:
        """Stop the scheduler loop if it is running."""

        if self._loop.is_running():
            self._loop.cancel()
        self._started = False
        logger.info("Attendance scheduler stopped.")

    async def run_once(self, now: datetime) -> None:
        """Run one scheduler tick without waiting for a real minute.

        Args:
            now: Current timezone-aware UTC time supplied by caller.

        Raises:
            ValueError: If ``now`` is naive.
        """

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")

        settings_rows = await self.guild_service.list_all_settings()

        for settings in settings_rows:
            try:
                result = await self.session_service.prepare_today_session(
                    guild_id=settings["guild_id"],
                    now=now,
                )
                if result.session is not None:
                    logger.info(
                        "Attendance scheduler prepared session: guild_id=%s session_id=%s status=%s",
                        settings["guild_id"],
                        result.session["id"],
                        result.session["status"],
                    )
            except Exception:
                logger.exception(
                    "Attendance scheduler session preparation failed: guild_id=%s",
                    settings["guild_id"],
                )

        await self._announce_starts(now)

        try:
            await self.session_service.process_overdue_sessions(now=now)
        except Exception:
            logger.exception("Attendance scheduler overdue processing failed.")

        await self._announce_closes(now)

    async def recover_overdue_sessions(self, now: datetime) -> None:
        """Run restart recovery once before the periodic loop starts.

        Args:
            now: Current timezone-aware UTC time.
        """

        await self.session_service.process_overdue_sessions(now=now)

    @tasks.loop(minutes=1)
    async def _loop(self) -> None:
        """Periodic task body."""

        try:
            await self.run_once(datetime.now(timezone.utc))
        except Exception:
            logger.exception("Attendance scheduler tick failed.")

    async def _announce_starts(self, now: datetime) -> None:
        """Send start announcements for newly opened sessions."""

        if self.bot is None:
            return

        sessions = (
            await self.session_service.session_repository.list_start_announcement_targets()
        )
        for session in sessions:
            channel_id = session["announcement_channel_id"] or session["attendance_channel_id"]
            if await self._send_channel_message(
                channel_id=channel_id,
                content=self._build_start_message(session),
            ):
                await self.session_service.session_repository.mark_start_announced(
                    session_id=int(session["id"]),
                    now=now.isoformat(),
                )

    async def _announce_closes(self, now: datetime) -> None:
        """Send close announcements for closed sessions."""

        if self.bot is None:
            return

        sessions = (
            await self.session_service.session_repository.list_close_announcement_targets()
        )
        for session in sessions:
            channel_id = session["announcement_channel_id"] or session["attendance_channel_id"]
            if await self._send_channel_message(
                channel_id=channel_id,
                content=self._build_close_message(session),
            ):
                await self.session_service.session_repository.mark_close_announced(
                    session_id=int(session["id"]),
                    now=now.isoformat(),
                )

    async def _send_channel_message(self, *, channel_id: str | None, content: str) -> bool:
        """Send a message to a Discord text channel if it can be resolved."""

        if self.bot is None or not channel_id:
            return False

        try:
            channel_id_int = int(channel_id)
        except (TypeError, ValueError):
            logger.warning("Invalid announcement channel id: %s", channel_id)
            return False

        channel = self.bot.get_channel(channel_id_int)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id_int)
            except Exception:
                logger.exception("Announcement channel lookup failed: %s", channel_id)
                return False

        try:
            await channel.send(content)
        except Exception:
            logger.exception("Announcement send failed: channel_id=%s", channel_id)
            return False
        return True

    def _build_start_message(self, session: dict[str, Any]) -> str:
        timezone_name = session["timezone"]
        return (
            "🚀 출석이 시작되었습니다.\n"
            f"⏰ 정상 출석 마감: {format_local_hhmm(datetime.fromisoformat(session['late_at']), timezone_name)}\n"
            f"🔒 전체 마감: {format_local_hhmm(datetime.fromisoformat(session['close_at']), timezone_name)}\n"
            "✅ 지금 /출석 명령어로 체크인해주세요."
        )

    def _build_close_message(self, session: dict[str, Any]) -> str:
        timezone_name = session["timezone"]
        closed_at = session["closed_at"] or session["close_at"]
        return (
            "🔒 출석이 마감되었습니다.\n"
            f"🕒 마감 시각: {format_local_hhmm(datetime.fromisoformat(closed_at), timezone_name)}\n"
            "📊 결과는 /출석현황 또는 /랭킹에서 확인할 수 있습니다."
        )
