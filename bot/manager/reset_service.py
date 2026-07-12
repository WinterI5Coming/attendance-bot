"""Full operational data reset with mandatory pre-reset backup."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from bot.manager.database_validation import APPLICATION_VERSION, calculate_sha256


logger = logging.getLogger(__name__)

RESET_CONFIRMATION = "RESET ALL DATA"
OPERATIONAL_TABLES = [
    "officer_role_change_logs",
    "officer_reviews",
    "officer_review_settings",
    "achievement_role_mappings",
    "member_titles",
    "title_definitions",
    "member_achievements",
    "achievement_definitions",
    "season_member_stats",
    "seasons",
    "attendance_adjustments",
    "attendance_verifications",
    "voice_presence_logs",
    "attendance_records",
    "attendance_session_members",
    "excuse_requests",
    "evaluations",
    "score_events",
    "audit_logs",
    "attendance_sessions",
    "attendance_date_overrides",
    "attendance_policies",
    "members",
    "guild_settings",
]


@dataclass(frozen=True)
class ResetResult:
    """Result of a successful full reset."""

    backup_path: Path
    metadata_path: Path
    deleted_counts: dict[str, int]
    completed_at: datetime


class DataResetService:
    """Backup then delete all operational rows while preserving schema metadata."""

    def __init__(self, *, database_path: Path, backups_directory: Path) -> None:
        self.database_path = database_path
        self.backups_directory = backups_directory
        self.backups_directory.mkdir(parents=True, exist_ok=True)

    def reset_all_data(self) -> ResetResult:
        """Create a verified backup and reset all operational tables."""

        logger.info("Policy reset requested. database=%s", self.database_path)
        if not self.database_path.exists():
            raise FileNotFoundError(f"Database does not exist: {self.database_path}")

        backup_path = self._create_pre_reset_backup()
        metadata_path = self._write_metadata(backup_path)
        deleted_counts = self._delete_operational_data()
        completed_at = datetime.now(ZoneInfo("Asia/Seoul"))
        logger.info(
            "Policy reset completed. backup=%s deleted_counts=%s",
            backup_path,
            deleted_counts,
        )
        return ResetResult(
            backup_path=backup_path,
            metadata_path=metadata_path,
            deleted_counts=deleted_counts,
            completed_at=completed_at,
        )

    def _create_pre_reset_backup(self) -> Path:
        """Create a SQLite backup and verify integrity before reset."""

        backup_path = self._unique_backup_path()
        temp_path = backup_path.with_suffix(".tmp")
        try:
            with closing(sqlite3.connect(self.database_path)) as source:
                source.execute("PRAGMA wal_checkpoint(FULL);")
                with closing(sqlite3.connect(temp_path)) as destination:
                    source.backup(destination)
                    destination.commit()

            with closing(sqlite3.connect(temp_path)) as connection:
                row = connection.execute("PRAGMA integrity_check;").fetchone()
            if row is None or row[0] != "ok":
                raise RuntimeError(f"Backup integrity check failed: {row}")

            os.replace(temp_path, backup_path)
            return backup_path
        except Exception:
            temp_path.unlink(missing_ok=True)
            logger.exception("Pre-reset backup failed.")
            raise

    def _write_metadata(self, backup_path: Path) -> Path:
        """Write reset backup metadata next to the backup DB."""

        metadata_path = backup_path.with_suffix(".json")
        metadata = {
            "created_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
            "reason": "attendance_policy_reset",
            "database_file": backup_path.name,
            "sha256": calculate_sha256(backup_path),
            "application_version": APPLICATION_VERSION,
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return metadata_path

    def _delete_operational_data(self) -> dict[str, int]:
        """Delete operational rows in FK-safe order inside one transaction."""

        deleted_counts: dict[str, int] = {}
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON;")
            try:
                connection.execute("BEGIN IMMEDIATE;")
                existing_tables = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table';"
                    )
                }
                for table in OPERATIONAL_TABLES:
                    if table not in existing_tables:
                        continue
                    count = connection.execute(
                        f"SELECT COUNT(*) FROM {table};"
                    ).fetchone()[0]
                    connection.execute(f"DELETE FROM {table};")
                    deleted_counts[table] = int(count)

                if "sqlite_sequence" in existing_tables:
                    placeholders = ",".join("?" for _ in OPERATIONAL_TABLES)
                    connection.execute(
                        f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders});",
                        OPERATIONAL_TABLES,
                    )

                connection.commit()
            except Exception:
                connection.rollback()
                logger.exception("Operational data reset failed; rolled back.")
                raise

        return deleted_counts

    def _unique_backup_path(self) -> Path:
        """Return a non-conflicting before-policy-reset backup path."""

        timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
        candidate = self.backups_directory / f"before_policy_reset_{timestamp}.db"
        counter = 1
        while candidate.exists() or candidate.with_suffix(".json").exists():
            candidate = (
                self.backups_directory
                / f"before_policy_reset_{timestamp}_{counter}.db"
            )
            counter += 1
        return candidate
