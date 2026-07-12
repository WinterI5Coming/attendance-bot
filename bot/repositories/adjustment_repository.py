"""출석 조정 기록에 대한 SQLite 접근을 담당한다."""

from typing import Any

import aiosqlite

from bot.db.database import Database


class AdjustmentRepository:
    """``attendance_adjustments`` 행을 다루는 SQL을 실행한다."""

    def __init__(self, database: Database) -> None:
        """저장소 의존성을 초기화한다.

        Args:
            database: Database object that opens configured SQLite connections.
        """

        self.database = database

    async def get_active_for_record(
        self,
        *,
        attendance_record_id: int,
        adjustment_type: str,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """출석 기록과 조정 유형에 맞는 활성 조정 하나를 반환한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM attendance_adjustments
                WHERE attendance_record_id = ?
                  AND adjustment_type = ?
                  AND status = 'ACTIVE'
                ORDER BY id DESC
                LIMIT 1;
                """,
                (attendance_record_id, adjustment_type),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def get_active_for_member_date(
        self,
        *,
        guild_id: str,
        member_id: int,
        attendance_date: str,
        adjustment_type: str,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """멤버, 날짜, 유형에 맞는 활성 조정을 반환한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT aa.*
                FROM attendance_adjustments AS aa
                JOIN attendance_sessions AS s ON s.id = aa.session_id
                WHERE aa.guild_id = ?
                  AND aa.member_id = ?
                  AND s.attendance_date = ?
                  AND aa.adjustment_type = ?
                  AND aa.status = 'ACTIVE'
                ORDER BY aa.id DESC
                LIMIT 1;
                """,
                (guild_id, member_id, attendance_date, adjustment_type),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def create_adjustment(
        self,
        *,
        guild_id: str,
        session_id: int,
        member_id: int,
        attendance_record_id: int,
        excuse_request_id: int,
        adjustment_type: str,
        requested_reduction_seconds: int | None,
        original_status: str,
        resulting_status: str,
        original_late_seconds: int | None,
        resulting_late_seconds: int | None,
        applied_by_discord_id: str,
        applied_at: str,
        reason: str,
        connection: aiosqlite.Connection,
    ) -> int:
        """점수 이벤트 연결 전의 ACTIVE 조정 행을 생성한다."""

        cursor = await connection.execute(
            """
            INSERT INTO attendance_adjustments (
                guild_id,
                session_id,
                member_id,
                attendance_record_id,
                excuse_request_id,
                adjustment_type,
                status,
                requested_reduction_seconds,
                original_status,
                resulting_status,
                original_late_seconds,
                resulting_late_seconds,
                score_event_id,
                reversal_score_event_id,
                applied_by_discord_id,
                applied_at,
                reason,
                cancelled_by_discord_id,
                cancelled_at,
                cancellation_reason,
                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, NULL, NULL,
                ?, ?, ?, NULL, NULL, NULL, ?, ?
            );
            """,
            (
                guild_id,
                session_id,
                member_id,
                attendance_record_id,
                excuse_request_id,
                adjustment_type,
                requested_reduction_seconds,
                original_status,
                resulting_status,
                original_late_seconds,
                resulting_late_seconds,
                applied_by_discord_id,
                applied_at,
                reason,
                applied_at,
                applied_at,
            ),
        )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)

    async def set_score_event(
        self,
        *,
        adjustment_id: int,
        score_event_id: int,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """조정 행에 적용 점수 이벤트를 연결한다."""

        await connection.execute(
            """
            UPDATE attendance_adjustments
            SET score_event_id = ?, updated_at = ?
            WHERE id = ?;
            """,
            (score_event_id, now, adjustment_id),
        )

    async def cancel_adjustment(
        self,
        *,
        adjustment_id: int,
        cancelled_by_discord_id: str,
        cancelled_at: str,
        cancellation_reason: str,
        reversal_score_event_id: int | None,
        connection: aiosqlite.Connection,
    ) -> None:
        """ACTIVE 조정 행을 취소 상태로 변경한다."""

        await connection.execute(
            """
            UPDATE attendance_adjustments
            SET
                status = 'CANCELLED',
                cancelled_by_discord_id = ?,
                cancelled_at = ?,
                cancellation_reason = ?,
                reversal_score_event_id = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'ACTIVE';
            """,
            (
                cancelled_by_discord_id,
                cancelled_at,
                cancellation_reason,
                reversal_score_event_id,
                cancelled_at,
                adjustment_id,
            ),
        )

    async def get_by_id(
        self,
        *,
        adjustment_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """ID로 조정 행 하나를 조회한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM attendance_adjustments
                WHERE id = ?;
                """,
                (adjustment_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def get_attendance_score_for_record(
        self,
        *,
        attendance_record_id: int,
        connection: aiosqlite.Connection,
    ) -> int:
        """출석 기록이 현재 점수에 기여하는 값을 반환한다.

        The original attendance, excuse reconciliation, manual correction, and
        active adjustment score events all live in ``score_events``. Summing the
        record-linked events plus active adjustment events gives the current
        attendance-specific contribution without editing historical events.
        """

        rows = await connection.execute_fetchall(
            """
            SELECT COALESCE(SUM(delta), 0) AS total
            FROM (
                SELECT se.delta
                FROM score_events AS se
                WHERE se.reference_type = 'ATTENDANCE'
                  AND se.reference_id = ?

                UNION ALL

                SELECT se.delta
                FROM attendance_adjustments AS aa
                JOIN score_events AS se
                    ON se.id = aa.score_event_id
                WHERE aa.attendance_record_id = ?
                  AND aa.status = 'ACTIVE'

                UNION ALL

                SELECT se.delta
                FROM attendance_adjustments AS aa
                JOIN score_events AS se
                    ON se.id = aa.reversal_score_event_id
                WHERE aa.attendance_record_id = ?
                  AND aa.status = 'CANCELLED'
            );
            """,
            (
                attendance_record_id,
                attendance_record_id,
                attendance_record_id,
            ),
        )
        return int(rows[0]["total"] or 0)
