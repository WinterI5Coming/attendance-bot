"""Tests for full operational data reset."""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import sqlite3

from bot.db.database import Database
from bot.manager.reset_service import DataResetService


async def _initialize(path):
    database = Database(path)
    await database.initialize()


def _insert_operational_rows(path):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            INSERT INTO guild_settings (
                guild_id,
                attendance_days,
                created_at,
                updated_at
            )
            VALUES ('111', 'MON,TUE,WED,THU,FRI', '2026-01-01', '2026-01-01');
            """
        )
        connection.execute(
            """
            INSERT INTO members (
                guild_id,
                discord_id,
                display_name,
                activated_at,
                created_by_discord_id,
                updated_at
            )
            VALUES ('111', '2001', 'A', '2026-01-01', '9001', '2026-01-01');
            """
        )
        connection.commit()


def _count(path, table):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0]


def _schema_version(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[
            0
        ]


def test_reset_backs_up_then_deletes_operational_rows(tmp_path):
    database_path = tmp_path / "data" / "attendance.db"
    asyncio.run(_initialize(database_path))
    _insert_operational_rows(database_path)

    service = DataResetService(
        database_path=database_path,
        backups_directory=tmp_path / "backups",
    )
    result = service.reset_all_data()

    assert result.backup_path.exists()
    assert result.metadata_path.exists()
    assert _count(result.backup_path, "guild_settings") == 1
    assert _count(result.backup_path, "members") == 1
    assert _count(database_path, "guild_settings") == 0
    assert _count(database_path, "members") == 0
    assert _schema_version(database_path) == 8
    assert result.deleted_counts["guild_settings"] == 1
    assert result.deleted_counts["members"] == 1

    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["reason"] == "attendance_policy_reset"
    assert metadata["database_file"] == result.backup_path.name
