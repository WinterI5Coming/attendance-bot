"""SQLite access for attendance sessions and session member snapshots."""

from typing import Any

import aiosqlite

from bot.db.database import Database


class SessionRepository:
    """Run SQL for attendance sessions and their fixed member snapshots."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database: Database object that opens configured SQLite connections.
        """

        self.database = database

    async def get_by_guild_and_date(
        self,
        *,
        guild_id: str,
        attendance_date: str,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """Fetch one session by guild and guild-local attendance date.

        Args:
            guild_id: Discord guild ID stored as text.
            attendance_date: Guild-local date in YYYY-MM-DD format.
            connection: Optional existing connection for transaction reuse.

        Returns:
            The session row as a dict, or ``None`` if no session exists.
        """

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    guild_id,
                    attendance_date,
                    start_at,
                    late_at,
                    close_at,
                    status,
                    opened_at,
                    closed_at,
                    cancelled_at,
                    cancel_reason,
                    start_announced_at,
                    close_announced_at,
                    created_at,
                    updated_at
                FROM attendance_sessions
                WHERE guild_id = ? AND attendance_date = ?;
                """,
                (guild_id, attendance_date),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def get_by_id(
        self,
        *,
        session_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """Fetch one session by its internal ID.

        Args:
            session_id: ``attendance_sessions.id``.
            connection: Optional existing connection for transaction reuse.

        Returns:
            The session row as a dict, or ``None`` if no session exists.
        """

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    guild_id,
                    attendance_date,
                    start_at,
                    late_at,
                    close_at,
                    status,
                    opened_at,
                    closed_at,
                    cancelled_at,
                    cancel_reason,
                    start_announced_at,
                    close_announced_at,
                    created_at,
                    updated_at
                FROM attendance_sessions
                WHERE id = ?;
                """,
                (session_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def create_with_members(
        self,
        *,
        guild_id: str,
        attendance_date: str,
        start_at: str,
        late_at: str,
        close_at: str,
        status: str,
        opened_at: str | None,
        member_ids: list[int],
        now: str,
    ) -> dict[str, Any]:
        """Create a session and its participant snapshot in one transaction.

        Args:
            guild_id: Discord guild ID stored as text.
            attendance_date: Guild-local date in YYYY-MM-DD format.
            start_at: UTC ISO 8601 session start.
            late_at: UTC ISO 8601 late threshold.
            close_at: UTC ISO 8601 close threshold.
            status: Initial session status, SCHEDULED or OPEN.
            opened_at: UTC ISO 8601 open time when initially OPEN, otherwise
                ``None``.
            member_ids: Active member IDs to freeze into the session snapshot.
            now: UTC ISO 8601 creation/update timestamp.

        Returns:
            The created session row.

        Raises:
            aiosqlite.IntegrityError: If a UNIQUE or FOREIGN KEY constraint is
                violated. Callers may recover from duplicate session creation.
        """

        connection = await self.database.connect()

        try:
            await connection.execute("BEGIN IMMEDIATE;")
            cursor = await connection.execute(
                """
                INSERT INTO attendance_sessions (
                    guild_id,
                    attendance_date,
                    start_at,
                    late_at,
                    close_at,
                    status,
                    opened_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    guild_id,
                    attendance_date,
                    start_at,
                    late_at,
                    close_at,
                    status,
                    opened_at,
                    now,
                    now,
                ),
            )

            assert cursor.lastrowid is not None
            session_id = cursor.lastrowid

            # This snapshot is the guardrail that keeps historical attendance
            # targets stable even if members are added or deactivated later.
            await connection.executemany(
                """
                INSERT INTO attendance_session_members (
                    session_id,
                    member_id,
                    included_at
                )
                VALUES (?, ?, ?);
                """,
                [
                    (session_id, member_id, now)
                    for member_id in member_ids
                ],
            )

            await connection.commit()
            session = await self.get_by_id(
                session_id=session_id,
                connection=connection,
            )
            assert session is not None
            return session
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def is_session_member(
        self,
        *,
        session_id: int,
        member_id: int,
    ) -> bool:
        """Return whether a member belongs to a session snapshot.

        Args:
            session_id: ``attendance_sessions.id``.
            member_id: ``members.id``.

        Returns:
            ``True`` when the member was included in the snapshot.
        """

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT 1
                FROM attendance_session_members
                WHERE session_id = ? AND member_id = ?;
                """,
                (session_id, member_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return row is not None
        finally:
            await connection.close()

    async def open_scheduled_session(
        self,
        *,
        session_id: int,
        now: str,
    ) -> dict[str, Any] | None:
        """Open a scheduled session if it has not already changed state.

        Args:
            session_id: ``attendance_sessions.id``.
            now: UTC ISO 8601 timestamp for ``opened_at`` and ``updated_at``.

        Returns:
            The refreshed session row, or ``None`` if the session is missing.
        """

        connection = await self.database.connect()

        try:
            await connection.execute(
                """
                UPDATE attendance_sessions
                SET
                    status = 'OPEN',
                    opened_at = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'SCHEDULED';
                """,
                (now, now, session_id),
            )
            await connection.commit()
            return await self.get_by_id(
                session_id=session_id,
                connection=connection,
            )
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_members_with_attendance(
        self,
        *,
        session_id: int,
    ) -> list[dict[str, Any]]:
        """List session snapshot members with any existing attendance record.

        Args:
            session_id: ``attendance_sessions.id``.

        Returns:
            Rows containing member identity and optional attendance record
            fields. Members without records are included through a LEFT JOIN.
        """

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    m.id AS member_id,
                    m.discord_id,
                    m.display_name,
                    ar.id AS attendance_record_id,
                    ar.status AS attendance_status,
                    ar.checked_at
                FROM attendance_session_members AS asm
                JOIN members AS m ON m.id = asm.member_id
                LEFT JOIN attendance_records AS ar
                    ON ar.session_id = asm.session_id
                    AND ar.member_id = asm.member_id
                WHERE asm.session_id = ?
                ORDER BY m.display_name COLLATE NOCASE;
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def list_overdue_sessions(
        self,
        *,
        now: str,
    ) -> list[dict[str, Any]]:
        """마감 시각이 지났지만 아직 종료되지 않은 세션을 조회한다.

        Args:
            now: UTC ISO 8601 기준 시각.

        Returns:
            CLOSED/CANCELLED가 아닌 overdue session 목록.
        """

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    guild_id,
                    attendance_date,
                    start_at,
                    late_at,
                    close_at,
                    status,
                    opened_at,
                    closed_at,
                    cancelled_at,
                    cancel_reason,
                    start_announced_at,
                    close_announced_at,
                    created_at,
                    updated_at
                FROM attendance_sessions
                WHERE close_at <= ?
                  AND status NOT IN ('CLOSED', 'CANCELLED')
                ORDER BY close_at, id;
                """,
                (now,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def list_unchecked_members(
        self,
        *,
        session_id: int,
        connection: aiosqlite.Connection,
    ) -> list[dict[str, Any]]:
        """세션 스냅샷에는 있지만 출석 기록이 없는 참여자를 조회한다.

        Args:
            session_id: attendance_sessions.id.
            connection: 마감 트랜잭션에서 공유하는 connection.

        Returns:
            미체크 참여자 목록. 현재 members.is_active 값과 무관하게
            스냅샷을 기준으로 한다.
        """

        cursor = await connection.execute(
            """
            SELECT
                m.id AS member_id,
                m.discord_id,
                m.display_name
            FROM attendance_session_members AS asm
            JOIN members AS m ON m.id = asm.member_id
            LEFT JOIN attendance_records AS ar
                ON ar.session_id = asm.session_id
                AND ar.member_id = asm.member_id
            WHERE asm.session_id = ?
              AND ar.id IS NULL
            ORDER BY m.display_name COLLATE NOCASE;
            """,
            (session_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def close_session(
        self,
        *,
        session_id: int,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """세션을 CLOSED로 변경한다.

        Args:
            session_id: attendance_sessions.id.
            now: UTC ISO 8601 closed_at/updated_at 시각.
            connection: 마감 트랜잭션에서 공유하는 connection.
        """

        await connection.execute(
            """
            UPDATE attendance_sessions
            SET
                status = 'CLOSED',
                closed_at = ?,
                updated_at = ?
            WHERE id = ?
              AND status NOT IN ('CLOSED', 'CANCELLED');
            """,
            (now, now, session_id),
        )
