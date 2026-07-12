"""SQLite 백업 생성과 보관 정책을 관리하는 서비스."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import shutil

import aiosqlite

from bot.db.database import Database


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackupResult:
    """SQLite 백업 실행 결과."""

    created: bool
    backup_path: Path | None = None
    pruned_count: int = 0
    integrity_ok: bool = False


class BackupService:
    """SQLite 백업 파일을 생성하고 오래된 백업을 정리한다.

    SQLite WAL 모드에서는 최근 쓰기가 메인 DB 파일 밖에 남을 수 있다. 그래서
    백업 직전에 WAL checkpoint를 수행하고 SQLite backup API로 일관된 스냅샷을
    만든다.
    """

    def __init__(
        self,
        *,
        database: Database,
        backup_directory: Path | None = None,
        retention_count: int = 14,
    ) -> None:
        """백업 대상 DB, 백업 디렉터리, 보관 개수를 설정한다."""

        self.database = database
        self.backup_directory = backup_directory or database.db_path.parent / "backups"
        self.retention_count = retention_count

    async def create_backup(self, *, now: datetime | None = None) -> BackupResult:
        """타임스탬프가 붙은 DB 백업을 만들고 오래된 백업을 정리한다."""

        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")

        self.backup_directory.mkdir(parents=True, exist_ok=True)
        backup_path = self.backup_directory / (
            f"attendance-{now.strftime('%Y%m%d-%H%M%S')}.db"
        )
        temp_path = backup_path.with_suffix(".tmp")

        source = await self.database.connect()
        destination = None
        try:
            await source.execute("PRAGMA wal_checkpoint(FULL);")
            destination = await aiosqlite.connect(temp_path)
            await source.backup(destination)
            await destination.close()
            destination = None

            integrity_ok = await self._check_integrity(temp_path)
            if not integrity_ok:
                temp_path.unlink(missing_ok=True)
                return BackupResult(created=False, integrity_ok=False)

            temp_path.replace(backup_path)
            pruned = self._prune_old_backups()
            logger.info("SQLite backup created: %s pruned=%s", backup_path, pruned)
            return BackupResult(
                created=True,
                backup_path=backup_path,
                pruned_count=pruned,
                integrity_ok=True,
            )
        except Exception:
            temp_path.unlink(missing_ok=True)
            logger.exception("SQLite backup failed.")
            raise
        finally:
            if destination is not None:
                await destination.close()
            await source.close()

    async def _check_integrity(self, path: Path) -> bool:
        """백업 DB에 `PRAGMA integrity_check`를 실행한다."""

        connection = await aiosqlite.connect(path)
        try:
            cursor = await connection.execute("PRAGMA integrity_check;")
            row = await cursor.fetchone()
            await cursor.close()
            return row is not None and row[0] == "ok"
        finally:
            await connection.close()

    def _prune_old_backups(self) -> int:
        """보관 개수를 초과한 오래된 백업 파일을 삭제한다."""

        backups = sorted(
            self.backup_directory.glob("attendance-*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        pruned = 0
        for path in backups[self.retention_count :]:
            path.unlink(missing_ok=True)
            pruned += 1
        return pruned
