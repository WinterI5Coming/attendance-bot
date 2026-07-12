"""SQLite database validation for backup and restore operations."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


APPLICATION_VERSION = "1.0.0"
SQLITE_HEADER = b"SQLite format 3\x00"
REQUIRED_TABLES = {
    "schema_migrations",
    "guild_settings",
    "members",
    "attendance_sessions",
    "attendance_session_members",
    "attendance_records",
    "score_events",
    "audit_logs",
    "excuse_requests",
}
MINIMUM_PROJECT_TABLES = {
    "schema_migrations",
    "guild_settings",
    "members",
}
SUPPORTED_SCHEMA_VERSION = 8


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a SQLite database file."""

    ok: bool
    message: str
    schema_version: int | None = None
    tables: set[str] | None = None


def calculate_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DatabaseValidationService:
    """Validate attendance-bot SQLite files before backup or restore."""

    def validate_for_restore(self, path: Path) -> ValidationResult:
        """Validate a user-selected backup file before restore."""

        result = self.validate_sqlite_database(path, require_current_schema=False)
        if not result.ok:
            return result

        metadata_result = self._validate_metadata_hash(path)
        if not metadata_result.ok:
            return metadata_result

        return result

    def validate_current_database(self, path: Path) -> ValidationResult:
        """Validate that the database has the current supported schema."""

        return self.validate_sqlite_database(path, require_current_schema=True)

    def validate_sqlite_database(
        self,
        path: Path,
        *,
        require_current_schema: bool,
    ) -> ValidationResult:
        """Validate SQLite format, integrity, tables, and schema version."""

        if not path.exists():
            return ValidationResult(False, f"Database file does not exist: {path}")
        if not path.is_file():
            return ValidationResult(False, f"Selected path is not a file: {path}")

        with path.open("rb") as file:
            if file.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                return ValidationResult(False, "Selected file is not a SQLite database.")

        try:
            with closing(sqlite3.connect(path)) as connection:
                integrity_row = connection.execute(
                    "PRAGMA integrity_check;"
                ).fetchone()
                if integrity_row is None or integrity_row[0] != "ok":
                    return ValidationResult(
                        False,
                        f"SQLite integrity check failed: {integrity_row}",
                    )

                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table';"
                    ).fetchall()
                }

                required_tables = (
                    REQUIRED_TABLES if require_current_schema else MINIMUM_PROJECT_TABLES
                )
                missing_tables = sorted(required_tables - tables)
                if missing_tables:
                    return ValidationResult(
                        False,
                        "Required tables are missing: " + ", ".join(missing_tables),
                        tables=tables,
                    )

                schema_version = self._read_schema_version(connection, tables)
        except sqlite3.Error as exc:
            return ValidationResult(False, f"SQLite validation failed: {exc}")

        if schema_version is None:
            return ValidationResult(False, "schema_migrations table has no version.")
        if schema_version > SUPPORTED_SCHEMA_VERSION:
            return ValidationResult(
                False,
                "Backup schema version "
                f"{schema_version} is newer than supported version "
                f"{SUPPORTED_SCHEMA_VERSION}.",
                schema_version=schema_version,
                tables=tables,
            )

        if require_current_schema and schema_version < SUPPORTED_SCHEMA_VERSION:
            return ValidationResult(
                False,
                "Database schema was not migrated to the current version.",
                schema_version=schema_version,
                tables=tables,
            )

        return ValidationResult(
            True,
            "Database validation succeeded.",
            schema_version=schema_version,
            tables=tables,
        )

    def _read_schema_version(
        self,
        connection: sqlite3.Connection,
        tables: set[str],
    ) -> int | None:
        """Read the latest applied migration version."""

        if "schema_migrations" not in tables:
            return None
        row = connection.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def _validate_metadata_hash(self, path: Path) -> ValidationResult:
        """If a sibling JSON metadata file exists, validate its SHA-256 hash."""

        metadata_path = path.with_suffix(".json")
        if not metadata_path.exists():
            return ValidationResult(True, "No metadata file found; hash check skipped.")

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return ValidationResult(False, f"Metadata file is unreadable: {exc}")

        expected_hash = metadata.get("sha256")
        if not expected_hash:
            return ValidationResult(False, "Metadata file does not include sha256.")

        actual_hash = calculate_sha256(path)
        if actual_hash != expected_hash:
            return ValidationResult(
                False,
                "Backup hash does not match metadata. The file may be changed or damaged.",
            )

        return ValidationResult(True, "Metadata hash validation succeeded.")
