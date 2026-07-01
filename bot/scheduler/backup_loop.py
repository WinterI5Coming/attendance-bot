"""Daily SQLite backup scheduler."""

from datetime import datetime, time, timezone
import logging

from discord.ext import tasks

from bot.services.backup_service import BackupService


logger = logging.getLogger(__name__)


class BackupScheduler:
    """Runs the backup service once per UTC day."""

    def __init__(self, *, backup_service: BackupService) -> None:
        self.backup_service = backup_service
        self._started = False
        self._last_backup_date: str | None = None

    def start(self) -> None:
        """Start the periodic backup loop if needed."""

        if self._started:
            return
        self._started = True
        logger.info("Backup scheduler started.")
        self._loop.start()

    def stop(self) -> None:
        """Stop the periodic backup loop."""

        if self._loop.is_running():
            self._loop.cancel()
        self._started = False

    async def run_once(self, now: datetime) -> bool:
        """Create one backup if this UTC date has not already run."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")

        date_key = now.astimezone(timezone.utc).date().isoformat()
        if self._last_backup_date == date_key:
            return False

        await self.backup_service.create_backup(now=now)
        self._last_backup_date = date_key
        return True

    @tasks.loop(hours=1)
    async def _loop(self) -> None:
        """Periodic backup task body."""

        try:
            await self.run_once(datetime.now(timezone.utc))
        except Exception:
            logger.exception("Backup scheduler tick failed.")
