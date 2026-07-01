"""SQLite backup creation and retention management."""

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
    """Result of a SQLite backup run."""

    created: bool
    backup_path: Path | None = None
    pruned_count: int = 0
    integrity_ok: bool = False


class BackupService:
    """Create SQLite backups and prune old backup files.

    SQLite WAL mode can keep recent writes outside the main database file. The
    service uses SQLite's backup API after a WAL checkpoint so the copied file is
    a consistent standalone snapshot.
    """

    def __init__(
        self,
        *,
        database: Database,
        backup_directory: Path | None = None,
        retention_count: int = 14,
    ) -> None:
        self.database = database
        self.backup_directory = backup_directory or database.db_path.parent / "backups"
        self.retention_count = retention_count

    async def create_backup(self, *, now: datetime | None = None) -> BackupResult:
        """Create a timestamped database backup and prune old files."""

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
        connection = await aiosqlite.connect(path)
        try:
            cursor = await connection.execute("PRAGMA integrity_check;")
            row = await cursor.fetchone()
            await cursor.close()
            return row is not None and row[0] == "ok"
        finally:
            await connection.close()

    def _prune_old_backups(self) -> int:
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
