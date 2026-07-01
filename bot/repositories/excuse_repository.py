"""SQLite access for excuse requests."""

from typing import Any

import aiosqlite

from bot.db.database import Database


ACTIVE_EXCUSE_STATUSES = ("PENDING", "APPROVED", "AUTO_APPROVED")
EFFECTIVE_EXCUSE_STATUSES = ("APPROVED", "AUTO_APPROVED")


class ExcuseRepository:
    """Run SQL for ``excuse_requests`` rows."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

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
    ) -> dict[str, Any]:
        """Create an excuse request.

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
                    target_date,
                    reason,
                    expected_time,
                    status,
                    requested_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    guild_id,
                    member_id,
                    target_date,
                    reason,
                    expected_time,
                    status,
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
        """Fetch one excuse request by ID."""

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
                    target_date,
                    reason,
                    expected_time,
                    status,
                    requested_at,
                    decided_by_discord_id,
                    decided_at,
                    rejection_reason,
                    cancelled_at
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
        """Return an active request for a member/date if one exists."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM excuse_requests
                WHERE guild_id = ?
                  AND member_id = ?
                  AND target_date = ?
                  AND status IN ('PENDING', 'APPROVED', 'AUTO_APPROVED')
                ORDER BY id DESC
                LIMIT 1;
                """,
                (guild_id, member_id, target_date),
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
        """Return the approved request that affects attendance, if any."""

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
        """List one member's excuse requests."""

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
        """List all excuse requests for a guild."""

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
        """Approve a PENDING request in a caller-owned transaction."""

        await connection.execute(
            """
            UPDATE excuse_requests
            SET
                status = 'APPROVED',
                decided_by_discord_id = ?,
                decided_at = ?,
                rejection_reason = NULL
            WHERE id = ?
              AND status = 'PENDING';
            """,
            (actor_discord_id, decided_at, excuse_request_id),
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
        """Reject a PENDING request in a caller-owned transaction."""

        await connection.execute(
            """
            UPDATE excuse_requests
            SET
                status = 'REJECTED',
                decided_by_discord_id = ?,
                decided_at = ?,
                rejection_reason = ?
            WHERE id = ?
              AND status = 'PENDING';
            """,
            (actor_discord_id, decided_at, rejection_reason, excuse_request_id),
        )

    async def cancel_active(
        self,
        *,
        excuse_request_id: int,
        cancelled_at: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """Cancel an active request in a caller-owned transaction."""

        await connection.execute(
            """
            UPDATE excuse_requests
            SET
                status = 'CANCELLED',
                cancelled_at = ?
            WHERE id = ?
              AND status IN ('PENDING', 'APPROVED', 'AUTO_APPROVED');
            """,
            (cancelled_at, excuse_request_id),
        )
