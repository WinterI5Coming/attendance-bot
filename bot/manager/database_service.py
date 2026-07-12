"""Backup and restore service used by the manager GUI and CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import asyncio
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import sqlite3
from zoneinfo import ZoneInfo

from bot.db.database import Database
from bot.manager.database_validation import (
    APPLICATION_VERSION,
    SUPPORTED_SCHEMA_VERSION,
    DatabaseValidationService,
    calculate_sha256,
)
from bot.runtime.instance_lock import InstanceLock
from bot.runtime.paths import ensure_runtime_directories


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackupResult:
    """Successful backup result."""

    backup_path: Path
    metadata_path: Path
    database_size: int
    sha256: str
    schema_version: int


@dataclass(frozen=True)
class RestoreResult:
    """Successful restore result."""

    restored_from: Path
    current_database: Path
    pre_restore_backup: Path | None


class DatabaseManagerService:
    """Perform safe SQLite backup, validation, and restore operations."""

    def __init__(self, app_directory: Path) -> None:
        self.app_directory = app_directory
        self.data_directory, self.logs_directory = ensure_runtime_directories(
            app_directory
        )
        self.backups_directory = app_directory / "backups"
        self.backups_directory.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_directory / "attendance.db"
        self.validation_service = DatabaseValidationService()

    def get_status(self) -> dict[str, str]:
        """Return strings used by the GUI status panel."""

        validation = (
            self.validation_service.validate_current_database(self.database_path)
            if self.database_path.exists()
            else None
        )
        last_backup = self.get_last_backup()
        return {
            "db_status": validation.message if validation else "DB file does not exist.",
            "db_path": str(self.database_path),
            "last_backup": str(last_backup) if last_backup else "No backups yet.",
        }

    def get_last_backup(self) -> Path | None:
        """Return the newest manager backup file."""

        backups = self.list_backups()
        return backups[0] if backups else None

    def list_backups(self) -> list[Path]:
        """List backup DB files newest first."""

        return sorted(
            self.backups_directory.glob("*backup_*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def create_backup(self, *, prefix: str = "attendance_backup") -> BackupResult:
        """Create a consistent SQLite backup and metadata file."""

        logger.info("Backup started. source=%s", self.database_path)
        if not self.database_path.exists():
            raise FileNotFoundError(
                f"Current database does not exist: {self.database_path}"
            )

        validation = self.validation_service.validate_for_restore(self.database_path)
        if not validation.ok:
            raise RuntimeError(f"Current database is not valid: {validation.message}")

        backup_path = self._unique_backup_path(prefix)
        temp_path = backup_path.with_suffix(".tmp")

        try:
            with closing(sqlite3.connect(self.database_path)) as source:
                source.execute("PRAGMA wal_checkpoint(FULL);")
                with closing(sqlite3.connect(temp_path)) as destination:
                    source.backup(destination)
                    destination.commit()

            integrity = self.validation_service.validate_sqlite_database(
                temp_path,
                require_current_schema=False,
            )
            if not integrity.ok:
                raise RuntimeError(integrity.message)

            os.replace(temp_path, backup_path)
            sha256 = calculate_sha256(backup_path)
            metadata_path = backup_path.with_suffix(".json")
            metadata = {
                "backup_created_at": datetime.now(
                    ZoneInfo("Asia/Seoul")
                ).isoformat(),
                "database_filename": backup_path.name,
                "database_size": backup_path.stat().st_size,
                "sha256": sha256,
                "schema_version": integrity.schema_version,
                "application_version": APPLICATION_VERSION,
                "source_computer": platform.node(),
            }
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            logger.info(
                "Backup completed. backup=%s size=%s schema=%s",
                backup_path,
                metadata["database_size"],
                integrity.schema_version,
            )
            return BackupResult(
                backup_path=backup_path,
                metadata_path=metadata_path,
                database_size=int(metadata["database_size"]),
                sha256=sha256,
                schema_version=int(integrity.schema_version or 0),
            )
        except Exception:
            temp_path.unlink(missing_ok=True)
            failed_path = backup_path.with_suffix(".failed")
            if backup_path.exists():
                backup_path.replace(failed_path)
            logger.exception("Backup failed.")
            raise

    def validate_backup(self, path: Path) -> str:
        """Validate a backup file and return a human-readable message."""

        result = self.validation_service.validate_for_restore(path)
        if not result.ok:
            raise RuntimeError(result.message)
        return (
            f"Validation succeeded. schema_version={result.schema_version}, "
            f"tables={len(result.tables or set())}"
        )

    def restore_backup(self, backup_path: Path) -> RestoreResult:
        """Restore a validated backup using temp files and rollback protection."""

        logger.info("Restore started. backup=%s", backup_path)
        backup_path = backup_path.resolve()
        validation = self.validation_service.validate_for_restore(backup_path)
        if not validation.ok:
            raise RuntimeError(validation.message)

        self._assert_bot_not_running()
        pre_restore: BackupResult | None = None
        if self.database_path.exists():
            pre_restore = self.create_backup(prefix="pre_restore_backup")
        else:
            logger.info("No current database exists; pre-restore backup skipped.")

        restore_temp = self.database_path.with_name("attendance.restore.tmp")
        previous_path = self.database_path.with_name("attendance.previous.tmp")
        restore_temp.unlink(missing_ok=True)
        previous_path.unlink(missing_ok=True)

        try:
            shutil.copy2(backup_path, restore_temp)
            temp_validation = self.validation_service.validate_sqlite_database(
                restore_temp,
                require_current_schema=False,
            )
            if not temp_validation.ok:
                raise RuntimeError(temp_validation.message)

            if self.database_path.exists():
                os.replace(self.database_path, previous_path)

            self._remove_sidecar_files(self.database_path)
            os.replace(restore_temp, self.database_path)
            asyncio.run(Database(self.database_path).initialize())

            final_validation = self.validation_service.validate_current_database(
                self.database_path
            )
            if not final_validation.ok:
                raise RuntimeError(final_validation.message)

            previous_path.unlink(missing_ok=True)
            logger.info(
                "Restore completed. backup=%s current=%s pre_restore=%s",
                backup_path,
                self.database_path,
                pre_restore.backup_path if pre_restore else None,
            )
            return RestoreResult(
                restored_from=backup_path,
                current_database=self.database_path,
                pre_restore_backup=pre_restore.backup_path if pre_restore else None,
            )
        except Exception:
            logger.exception("Restore failed; attempting rollback.")
            restore_temp.unlink(missing_ok=True)
            if previous_path.exists():
                self._remove_sidecar_files(self.database_path)
                if self.database_path.exists():
                    self.database_path.unlink()
                os.replace(previous_path, self.database_path)
                logger.info("Rollback completed.")
            raise

    def _unique_backup_path(self, prefix: str) -> Path:
        """Return a non-conflicting backup path."""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate = self.backups_directory / f"{prefix}_{timestamp}.db"
        counter = 1
        while candidate.exists() or candidate.with_suffix(".json").exists():
            candidate = self.backups_directory / f"{prefix}_{timestamp}_{counter}.db"
            counter += 1
        return candidate

    def _assert_bot_not_running(self) -> None:
        """Ensure restore only happens while the bot executable is stopped."""

        lock = InstanceLock(self.app_directory / "attendance-bot.lock")
        if not lock.acquire():
            raise RuntimeError(
                "Discord 출석 봇이 현재 실행 중입니다.\n\n"
                "데이터베이스를 복원하려면 AttendanceBot.exe를 먼저 종료해주세요.\n"
                "봇을 종료한 뒤 다시 복원을 시도해주세요."
            )
        lock.release()

    def _remove_sidecar_files(self, database_path: Path) -> None:
        """Remove old SQLite WAL/SHM/journal files for the target database."""

        for suffix in ("-wal", "-shm", "-journal"):
            database_path.with_name(database_path.name + suffix).unlink(
                missing_ok=True
            )
