"""SQLite 백업 서비스를 주기적으로 실행하는 스케줄러."""

from datetime import datetime, time, timezone
import logging

from discord.ext import tasks

from bot.services.backup_service import BackupService


logger = logging.getLogger(__name__)


class BackupScheduler:
    """UTC 날짜 기준 하루 한 번 SQLite 백업을 실행한다."""

    def __init__(self, *, backup_service: BackupService) -> None:
        """백업 실행에 사용할 서비스를 저장하고 내부 상태를 초기화한다."""

        self.backup_service = backup_service
        self._started = False
        self._last_backup_date: str | None = None

    def start(self) -> None:
        """필요한 경우 주기 백업 루프를 시작한다."""

        if self._started:
            return
        self._started = True
        logger.info("Backup scheduler started.")
        self._loop.start()

    def stop(self) -> None:
        """주기 백업 루프를 중지한다."""

        if self._loop.is_running():
            self._loop.cancel()
        self._started = False

    async def run_once(self, now: datetime) -> bool:
        """해당 UTC 날짜에 아직 백업하지 않았다면 한 번 백업한다."""

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
        """매 시간 실행되는 백업 루프 본문."""

        try:
            await self.run_once(datetime.now(timezone.utc))
        except Exception:
            logger.exception("Backup scheduler tick failed.")
