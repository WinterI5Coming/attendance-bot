"""Minute-based attendance scheduler."""

from datetime import datetime, timezone
import logging

from discord.ext import tasks

from bot.services.guild_service import GuildService
from bot.services.session_service import SessionService


logger = logging.getLogger(__name__)


class AttendanceScheduler:
    """Runs automatic attendance preparation, opening, closing, and recovery."""

    def __init__(
        self,
        *,
        guild_service: GuildService,
        session_service: SessionService,
    ) -> None:
        """Create the scheduler.

        Args:
            guild_service: Service used to list configured guilds.
            session_service: Service used to prepare and close sessions.
        """

        self.guild_service = guild_service
        self.session_service = session_service
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

        try:
            await self.session_service.process_overdue_sessions(now=now)
        except Exception:
            logger.exception("Attendance scheduler overdue processing failed.")

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
