"""SQLite access for member attendance records."""

from typing import Any

import aiosqlite

from bot.db.database import Database


class AttendanceRepository:
    """Run SQL for ``attendance_records`` rows."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database: Database object that opens configured SQLite connections.
        """

        self.database = database

    async def get_by_session_and_member(
        self,
        *,
        session_id: int,
        member_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """Fetch a member's attendance record in one session.

        Args:
            session_id: ``attendance_sessions.id``.
            member_id: ``members.id``.
            connection: Optional existing connection for transaction reuse.

        Returns:
            Attendance record as a dict, or ``None`` when not checked in.
        """

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    session_id,
                    member_id,
                    status,
                    checked_at,
                    source,
                    excuse_request_id,
                    note,
                    created_at,
                    updated_at
                FROM attendance_records
                WHERE session_id = ? AND member_id = ?;
                """,
                (session_id, member_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def create_user_record(
        self,
        *,
        session_id: int,
        member_id: int,
        status: str,
        checked_at: str,
        connection: aiosqlite.Connection,
    ) -> dict[str, Any]:
        """Create a user-generated PRESENT or LATE attendance record.

        Args:
            session_id: ``attendance_sessions.id``.
            member_id: ``members.id``.
            status: Attendance status. This method accepts PRESENT, LATE, or
                EXCUSED_LATE because user check-in can apply an approved
                excuse during the late window.
            checked_at: UTC ISO 8601 timestamp for the check-in.
            connection: Existing transaction connection owned by the service.

        Returns:
            The created attendance record.

        Raises:
            ValueError: If ``status`` is not PRESENT, LATE, or EXCUSED_LATE.
            aiosqlite.IntegrityError: If a UNIQUE or FOREIGN KEY constraint is
                violated.
        """

        if status not in {"PRESENT", "LATE", "EXCUSED_LATE"}:
            raise ValueError(
                "User check-in can create only PRESENT, LATE, or EXCUSED_LATE records."
            )

        cursor = await connection.execute(
            """
            INSERT INTO attendance_records (
                session_id,
                member_id,
                status,
                checked_at,
                source,
                excuse_request_id,
                note,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 'USER', NULL, NULL, ?, ?);
            """,
            (
                session_id,
                member_id,
                status,
                checked_at,
                checked_at,
                checked_at,
            ),
        )

        assert cursor.lastrowid is not None
        record = await self.get_by_session_and_member(
            session_id=session_id,
            member_id=member_id,
            connection=connection,
        )
        assert record is not None
        return record

    async def set_excuse_request(
        self,
        *,
        attendance_record_id: int,
        excuse_request_id: int,
        connection: aiosqlite.Connection,
    ) -> None:
        """Link an attendance record to an approved excuse request."""

        await connection.execute(
            """
            UPDATE attendance_records
            SET excuse_request_id = ?
            WHERE id = ?;
            """,
            (excuse_request_id, attendance_record_id),
        )

    async def create_auto_absent_record(
        self,
        *,
        session_id: int,
        member_id: int,
        status: str = "ABSENT",
        excuse_request_id: int | None = None,
        now: str,
        connection: aiosqlite.Connection,
    ) -> dict[str, Any]:
        """자동 마감으로 ABSENT 출석 기록을 생성한다.

        Args:
            session_id: attendance_sessions.id.
            member_id: members.id.
            status: ABSENT or EXCUSED_ABSENT.
            excuse_request_id: Approved excuse request ID when excused.
            now: UTC ISO 8601 생성/수정 시각.
            connection: 마감 트랜잭션에서 공유하는 connection.

        Returns:
            생성된 attendance_records 행.

        Raises:
            aiosqlite.IntegrityError: UNIQUE/FK 제약 위반 시.
        """

        if status not in {"ABSENT", "EXCUSED_ABSENT"}:
            raise ValueError("Auto close can create only ABSENT or EXCUSED_ABSENT.")

        cursor = await connection.execute(
            """
            INSERT INTO attendance_records (
                session_id,
                member_id,
                status,
                checked_at,
                source,
                excuse_request_id,
                note,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, NULL, 'AUTO', ?, NULL, ?, ?);
            """,
            (session_id, member_id, status, excuse_request_id, now, now),
        )
        assert cursor.lastrowid is not None
        record = await self.get_by_session_and_member(
            session_id=session_id,
            member_id=member_id,
            connection=connection,
        )
        assert record is not None
        return record

    async def create_admin_record(
        self,
        *,
        session_id: int,
        member_id: int,
        status: str,
        checked_at: str | None,
        note: str,
        now: str,
        connection: aiosqlite.Connection,
    ) -> dict[str, Any]:
        """관리자 정정으로 새 출석 기록을 생성한다.

        Args:
            session_id: attendance_sessions.id.
            member_id: members.id.
            status: PRESENT, LATE, ABSENT 중 하나.
            checked_at: PRESENT/LATE면 UTC ISO 8601, ABSENT면 None.
            note: 정정 사유.
            now: UTC ISO 8601 생성/수정 시각.
            connection: 정정 트랜잭션 connection.

        Returns:
            생성된 attendance_records 행.
        """

        if status not in {"PRESENT", "LATE", "ABSENT"}:
            raise ValueError("Admin correction supports only PRESENT, LATE, ABSENT.")

        await connection.execute(
            """
            INSERT INTO attendance_records (
                session_id,
                member_id,
                status,
                checked_at,
                source,
                excuse_request_id,
                note,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 'ADMIN', NULL, ?, ?, ?);
            """,
            (session_id, member_id, status, checked_at, note, now, now),
        )
        record = await self.get_by_session_and_member(
            session_id=session_id,
            member_id=member_id,
            connection=connection,
        )
        assert record is not None
        return record

    async def update_admin_record(
        self,
        *,
        attendance_record_id: int,
        status: str,
        checked_at: str | None,
        note: str,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """관리자 정정으로 기존 출석 기록을 갱신한다.

        Args:
            attendance_record_id: attendance_records.id.
            status: 새 상태.
            checked_at: 갱신할 checked_at.
            note: 정정 사유.
            now: UTC ISO 8601 updated_at.
            connection: 정정 트랜잭션 connection.
        """

        if status not in {"PRESENT", "LATE", "ABSENT"}:
            raise ValueError("Admin correction supports only PRESENT, LATE, ABSENT.")

        await connection.execute(
            """
            UPDATE attendance_records
            SET
                status = ?,
                checked_at = ?,
                source = 'ADMIN',
                note = ?,
                updated_at = ?
            WHERE id = ?;
            """,
            (status, checked_at, note, now, attendance_record_id),
        )

    async def update_status_for_excuse(
        self,
        *,
        attendance_record_id: int,
        new_status: str,
        excuse_request_id: int,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """Apply an approved excuse to an existing attendance record."""

        if new_status not in {"PRESENT", "EXCUSED_LATE", "EXCUSED_ABSENT"}:
            raise ValueError("Invalid status for excuse reconciliation.")

        await connection.execute(
            """
            UPDATE attendance_records
            SET
                status = ?,
                excuse_request_id = ?,
                updated_at = ?
            WHERE id = ?;
            """,
            (new_status, excuse_request_id, now, attendance_record_id),
        )
