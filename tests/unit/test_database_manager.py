"""Tests for database manager backup, validation, and restore logic."""

from __future__ import annotations

import asyncio
from contextlib import closing
import sqlite3

import pytest

from bot.db.database import Database
from bot.manager.database_service import DatabaseManagerService
from bot.runtime.instance_lock import InstanceLock


async def _initialize_database(path):
    db = Database(path)
    await db.initialize()


def _insert_marker(path, guild_id: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            INSERT INTO guild_settings (
                guild_id,
                attendance_days,
                created_at,
                updated_at
            )
            VALUES (?, 'MON,TUE,WED,THU,FRI', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
            """,
            (guild_id,),
        )
        connection.commit()


def _guild_ids(path) -> set[str]:
    with closing(sqlite3.connect(path)) as connection:
        return {
            row[0]
            for row in connection.execute("SELECT guild_id FROM guild_settings;")
        }


def test_backup_creates_db_and_metadata(tmp_path):
    service = DatabaseManagerService(tmp_path)
    asyncio.run(_initialize_database(service.database_path))
    _insert_marker(service.database_path, "111")

    result = service.create_backup()

    assert result.backup_path.exists()
    assert result.metadata_path.exists()
    assert result.database_size > 0
    assert result.schema_version == 8
    assert _guild_ids(result.backup_path) == {"111"}


def test_backup_requires_existing_database(tmp_path):
    service = DatabaseManagerService(tmp_path)

    with pytest.raises(FileNotFoundError):
        service.create_backup()


def test_validate_rejects_non_sqlite_file(tmp_path):
    service = DatabaseManagerService(tmp_path)
    bad_file = tmp_path / "not-a-db.txt"
    bad_file.write_text("hello", encoding="utf-8")

    with pytest.raises(RuntimeError, match="SQLite"):
        service.validate_backup(bad_file)


def test_restore_replaces_database_and_creates_pre_restore_backup(tmp_path):
    source_app = tmp_path / "source"
    target_app = tmp_path / "target"
    source = DatabaseManagerService(source_app)
    target = DatabaseManagerService(target_app)

    asyncio.run(_initialize_database(source.database_path))
    asyncio.run(_initialize_database(target.database_path))
    _insert_marker(source.database_path, "source-guild")
    _insert_marker(target.database_path, "target-guild")

    backup = source.create_backup()
    result = target.restore_backup(backup.backup_path)

    assert result.pre_restore_backup is not None
    assert result.pre_restore_backup.exists()
    assert _guild_ids(target.database_path) == {"source-guild"}
    assert _guild_ids(result.pre_restore_backup) == {"target-guild"}


def test_restore_rejects_missing_required_tables(tmp_path):
    service = DatabaseManagerService(tmp_path)
    other_db = tmp_path / "other.db"
    with closing(sqlite3.connect(other_db)) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY);")
        connection.commit()

    with pytest.raises(RuntimeError, match="Required tables|schema_migrations"):
        service.restore_backup(other_db)


def test_restore_is_blocked_while_bot_lock_is_held(tmp_path):
    source_app = tmp_path / "source"
    target_app = tmp_path / "target"
    source = DatabaseManagerService(source_app)
    target = DatabaseManagerService(target_app)

    asyncio.run(_initialize_database(source.database_path))
    asyncio.run(_initialize_database(target.database_path))
    backup = source.create_backup()

    lock = InstanceLock(target_app / "attendance-bot.lock")
    assert lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="현재 실행 중"):
            target.restore_backup(backup.backup_path)
    finally:
        lock.release()
