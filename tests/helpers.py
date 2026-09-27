"""통합 테스트가 공유하는 상수, 시각 생성기, SQLite 조회/시드 헬퍼."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

GUILD_ID = "111"
ADMIN_ID = "9001"
MEMBER_CREATED_AT = "2026-07-01T00:00:00+00:00"

DEFAULT_GUILD_COLUMNS: dict[str, Any] = {
    "attendance_days": "MON,TUE,WED,THU,FRI,SAT,SUN",
    "attendance_start": "21:30",
    "late_deadline": "21:40",
    "close_deadline": "21:45",
    "timezone": "Asia/Seoul",
}


def utc_dt(hour: int, minute: int, second: int = 0, *, day: int = 2) -> datetime:
    """2026-07-``day`` UTC 기준 timezone-aware datetime을 만든다."""

    return datetime(2026, 7, day, hour, minute, second, tzinfo=UTC)


async def configure_guild(database, *, guild_id: str = GUILD_ID, **columns: Any) -> None:
    """테스트 서버를 매일 21:30 출석으로 설정하고, 추가 컬럼은 ``columns``로 덮어쓴다."""

    values = {**DEFAULT_GUILD_COLUMNS, **columns}
    assignments = ", ".join(f"{name} = ?" for name in values)
    connection = await database.connect()
    try:
        await connection.execute(
            f"UPDATE guild_settings SET {assignments} WHERE guild_id = ?;",
            (*values.values(), guild_id),
        )
        await connection.commit()
    finally:
        await connection.close()


async def create_member(
    member_repository,
    discord_id: str,
    name: str,
    *,
    guild_id: str = GUILD_ID,
    now: str = MEMBER_CREATED_AT,
) -> int:
    """활성 대원 한 명을 만들고 members.id를 반환한다."""

    return await member_repository.create(
        guild_id=guild_id,
        discord_id=discord_id,
        display_name=name,
        created_by_discord_id=ADMIN_ID,
        now=now,
    )


async def fetch_all(database, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """조회 결과를 dict 목록으로 반환한다."""

    connection = await database.connect()
    try:
        rows = await connection.execute_fetchall(sql, params)
        return [dict(row) for row in rows]
    finally:
        await connection.close()


async def count_rows(database, table: str) -> int:
    """테이블의 행 수를 반환한다."""

    rows = await fetch_all(database, f"SELECT COUNT(*) AS count FROM {table};")
    return int(rows[0]["count"])


async def list_sessions(database) -> list[dict[str, Any]]:
    """attendance_sessions 행을 id 순서로 반환한다."""

    return await fetch_all(database, "SELECT * FROM attendance_sessions ORDER BY id;")


class FakeGuildService:
    """스케줄러 테스트용 서버 설정 서비스 대역."""

    def __init__(self, guild_repository) -> None:
        self.guild_repository = guild_repository

    async def list_all_settings(self):
        return await self.guild_repository.list_all_settings()
