"""사유 신청 데이터에 대한 SQLite 접근을 담당한다."""

from typing import Any

import aiosqlite

from bot.db.database import Database


ACTIVE_EXCUSE_STATUSES = ("PENDING", "APPROVED", "AUTO_APPROVED")
EFFECTIVE_EXCUSE_STATUSES = ("APPROVED", "AUTO_APPROVED")


class ExcuseRepository:
    """``excuse_requests`` 행을 다루는 SQL을 실행한다."""

    def __init__(self, database: Database) -> None:
        """저장소 의존성을 초기화한다.

        Args:
            database: Database object that opens configured SQLite connections.
        """

        self.database = database

    async def create(
        self,
        *,
        guild_id: str,
        member_id: int,
        target_date: str,
        reason: str,
        expected_time: str | None,
        status: str,
        requested_at: str,
        excuse_type: str = "ABSENCE",
        deadline_at: str | None = None,
        attendance_session_id: int | None = None,
        is_admin_override: bool = False,
        approval_type: str = "STANDARD",
        decided_by_discord_id: str | None = None,
        decided_at: str | None = None,
        processed_by: str | None = None,
        processed_at: str | None = None,
        admin_note: str | None = None,
    ) -> dict[str, Any]:
        """사유 신청 행을 생성한다.

        Args:
            guild_id: Discord guild ID.
            member_id: members.id.
            target_date: Guild-local YYYY-MM-DD date.
            reason: Private excuse reason.
            expected_time: Optional HH:MM expected arrival.
            status: PENDING or AUTO_APPROVED.
            requested_at: UTC ISO 8601 request time.

        Returns:
            Created excuse request row.

        Raises:
            aiosqlite.IntegrityError: If constraints are violated.
        """

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                INSERT INTO excuse_requests (
                    guild_id,
                    member_id,
                    attendance_session_id,
                    target_date,
                    excuse_type,
                    reason,
                    expected_time,
                    status,
                    requested_at,
                    deadline_at,
                    decided_by_discord_id,
                    decided_at,
                    processed_by,
                    processed_at,
                    admin_note,
                    is_admin_override,
                    approval_type,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    guild_id,
                    member_id,
                    attendance_session_id,
                    target_date,
                    excuse_type,
                    reason,
                    expected_time,
                    status,
                    requested_at,
                    deadline_at,
                    decided_by_discord_id,
                    decided_at,
                    processed_by,
                    processed_at,
                    admin_note,
                    1 if is_admin_override else 0,
                    approval_type,
                    requested_at,
                ),
            )
            await connection.commit()
            assert cursor.lastrowid is not None
            row = await self.get_by_id(excuse_request_id=int(cursor.lastrowid))
            assert row is not None
            return row
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_by_id(
        self,
        *,
        excuse_request_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """ID로 사유 신청 하나를 조회한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    guild_id,
                    member_id,
                    attendance_session_id,
                    target_date,
                    excuse_type,
                    reason,
                    expected_time,
                    status,
                    requested_at,
                    deadline_at,
                    decided_by_discord_id,
                    decided_at,
                    processed_by,
                    processed_at,
                    admin_note,
                    rejection_reason,
                    cancelled_at,
                    is_admin_override,
                    approval_type,
                    updated_at
                FROM excuse_requests
                WHERE id = ?;
                """,
                (excuse_request_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def get_active_by_member_and_date(
        self,
        *,
        guild_id: str,
        member_id: int,
        target_date: str,
    ) -> dict[str, Any] | None:
        """멤버와 날짜에 해당하는 활성 사유 신청을 반환한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM excuse_requests
                WHERE guild_id = ?
                  AND member_id = ?
                  AND target_date = ?
                  AND (? IS NULL OR excuse_type = ?)
                  AND status IN ('PENDING', 'APPROVED', 'AUTO_APPROVED')
                ORDER BY id DESC
                LIMIT 1;
                """,
                (guild_id, member_id, target_date, None, None),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            await connection.close()

    async def get_effective_approved_request(
        self,
        *,
        guild_id: str,
        member_id: int,
        target_date: str,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """출석에 영향을 주는 승인된 사유 신청이 있으면 반환한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM excuse_requests
                WHERE guild_id = ?
                  AND member_id = ?
                  AND target_date = ?
                  AND status IN ('APPROVED', 'AUTO_APPROVED')
                ORDER BY id DESC
                LIMIT 1;
                """,
                (guild_id, member_id, target_date),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def list_by_member(
        self,
        *,
        guild_id: str,
        member_id: int,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """한 멤버의 사유 신청 목록을 조회한다."""

        return await self._list(
            guild_id=guild_id,
            member_id=member_id,
            status=status,
        )

    async def list_by_guild(
        self,
        *,
        guild_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """서버의 모든 사유 신청 목록을 조회한다."""

        return await self._list(
            guild_id=guild_id,
            member_id=None,
            status=status,
        )

    async def _list(
        self,
        *,
        guild_id: str,
        member_id: int | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        """사유 신청 목록을 본인 또는 전체 범위로 조회한다."""

        connection = await self.database.connect()
        try:
            params: list[Any] = [guild_id]
            filters = ["er.guild_id = ?"]
            if member_id is not None:
                filters.append("er.member_id = ?")
                params.append(member_id)
            if status:
                filters.append("er.status = ?")
                params.append(status)
            where_sql = " AND ".join(filters)
            cursor = await connection.execute(
                f"""
                SELECT
                    er.*,
                    m.discord_id,
                    m.display_name
                FROM excuse_requests AS er
                JOIN members AS m ON m.id = er.member_id
                WHERE {where_sql}
                ORDER BY er.target_date DESC, er.id DESC
                LIMIT 20;
                """,
                params,
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def approve_pending(
        self,
        *,
        excuse_request_id: int,
        actor_discord_id: str,
        decided_at: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """호출자 트랜잭션 안에서 PENDING 사유 신청을 승인한다."""

        await connection.execute(
            """
            UPDATE excuse_requests
            SET
                status = 'APPROVED',
                decided_by_discord_id = ?,
                decided_at = ?,
                processed_by = ?,
                processed_at = ?,
                rejection_reason = NULL
            WHERE id = ?
              AND status = 'PENDING';
            """,
            (actor_discord_id, decided_at, actor_discord_id, decided_at, excuse_request_id),
        )

    async def reject_pending(
        self,
        *,
        excuse_request_id: int,
        actor_discord_id: str,
        decided_at: str,
        rejection_reason: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """호출자 트랜잭션 안에서 PENDING 사유 신청을 거절한다."""

        await connection.execute(
            """
            UPDATE excuse_requests
            SET
                status = 'REJECTED',
                decided_by_discord_id = ?,
                decided_at = ?,
                processed_by = ?,
                processed_at = ?,
                rejection_reason = ?
            WHERE id = ?
              AND status = 'PENDING';
            """,
            (
                actor_discord_id,
                decided_at,
                actor_discord_id,
                decided_at,
                rejection_reason,
                excuse_request_id,
            ),
        )

    async def cancel_active(
        self,
        *,
        excuse_request_id: int,
        cancelled_at: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """호출자 트랜잭션 안에서 활성 사유 신청을 취소한다."""

        await connection.execute(
            """
            UPDATE excuse_requests
            SET
                status = 'CANCELLED',
                cancelled_at = ?,
                updated_at = ?
            WHERE id = ?
              AND status IN ('PENDING', 'APPROVED', 'AUTO_APPROVED');
            """,
            (cancelled_at, cancelled_at, excuse_request_id),
        )

    async def update_policy(
        self,
        *,
        guild_id: str,
        deadline_time: str,
        deadline_days_before: int,
        actor_discord_id: str,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """Update excuse deadline policy fields for a guild."""

        await connection.execute(
            """
            UPDATE guild_settings
            SET
                excuse_deadline_time = ?,
                excuse_deadline_days_before = ?,
                require_excuse_approval = 1,
                allow_late_excuse = 0,
                updated_at = ?
            WHERE guild_id = ?;
            """,
            (deadline_time, deadline_days_before, now, guild_id),
        )
