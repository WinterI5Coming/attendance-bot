"""출석 세션과 세션 멤버 스냅샷에 대한 SQLite 접근을 담당한다."""

from typing import Any

import aiosqlite

from bot.db.database import Database


class SessionRepository:
    """출석 세션과 고정 멤버 스냅샷을 다루는 SQL을 실행한다."""

    def __init__(self, database: Database) -> None:
        """저장소 의존성을 초기화한다.

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
        """서버와 서버 로컬 출석일로 세션 하나를 조회한다.

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
                    verification_end_at,
                    required_voice_seconds,
                    early_leave_penalty,
                    no_participation_penalty,
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
        """내부 ID로 세션 하나를 조회한다.

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
                    verification_end_at,
                    required_voice_seconds,
                    early_leave_penalty,
                    no_participation_penalty,
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
        verification_end_at: str | None = None,
        required_voice_seconds: int | None = None,
        early_leave_penalty: int | None = None,
        no_participation_penalty: int | None = None,
        status: str,
        opened_at: str | None,
        member_ids: list[int],
        now: str,
    ) -> dict[str, Any]:
        """세션과 참여자 스냅샷을 하나의 트랜잭션으로 생성한다.

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
                    verification_end_at,
                    required_voice_seconds,
                    early_leave_penalty,
                    no_participation_penalty,
                    status,
                    opened_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    guild_id,
                    attendance_date,
                    start_at,
                    late_at,
                    close_at,
                    verification_end_at,
                    required_voice_seconds,
                    early_leave_penalty,
                    no_participation_penalty,
                    status,
                    opened_at,
                    now,
                    now,
                ),
            )

            assert cursor.lastrowid is not None
            session_id = cursor.lastrowid

            # 이 스냅샷은 이후 멤버가 추가되거나 비활성화되어도
            # 과거 출석 대상자가 흔들리지 않게 고정하는 안전장치다.
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
        """멤버가 세션 스냅샷에 포함되어 있는지 반환한다.

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
        """상태가 아직 바뀌지 않은 예약 세션을 연다.

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
        """세션 스냅샷 멤버와 기존 출석 기록을 함께 조회한다.

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
                    CASE
                        WHEN abs_adj.id IS NOT NULL THEN 'EXCUSED_ABSENT'
                        WHEN late_adj.resulting_status IS NOT NULL THEN late_adj.resulting_status
                        ELSE ar.status
                    END AS attendance_status,
                    ar.checked_at
                FROM attendance_session_members AS asm
                JOIN members AS m ON m.id = asm.member_id
                LEFT JOIN attendance_records AS ar
                    ON ar.session_id = asm.session_id
                    AND ar.member_id = asm.member_id
                LEFT JOIN attendance_adjustments AS late_adj
                    ON late_adj.attendance_record_id = ar.id
                    AND late_adj.adjustment_type = 'LATE_REDUCTION'
                    AND late_adj.status = 'ACTIVE'
                LEFT JOIN attendance_adjustments AS abs_adj
                    ON abs_adj.attendance_record_id = ar.id
                    AND abs_adj.adjustment_type = 'ABSENCE_EXEMPTION'
                    AND abs_adj.status = 'ACTIVE'
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
                    verification_end_at,
                    required_voice_seconds,
                    early_leave_penalty,
                    no_participation_penalty,
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

    async def list_start_announcement_targets(
        self,
    ) -> list[dict[str, Any]]:
        """시작 안내가 아직 전송되지 않은 열린 세션을 조회한다."""

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    s.id,
                    s.guild_id,
                    s.attendance_date,
                    s.start_at,
                    s.late_at,
                    s.close_at,
                    s.verification_end_at,
                    s.required_voice_seconds,
                    s.early_leave_penalty,
                    s.no_participation_penalty,
                    gs.attendance_channel_id,
                    gs.announcement_channel_id,
                    gs.timezone
                FROM attendance_sessions AS s
                JOIN guild_settings AS gs ON gs.guild_id = s.guild_id
                WHERE s.status = 'OPEN'
                  AND s.start_announced_at IS NULL
                ORDER BY s.start_at, s.id;
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def list_close_announcement_targets(
        self,
    ) -> list[dict[str, Any]]:
        """마감 안내가 아직 전송되지 않은 종료 세션을 조회한다."""

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    s.id,
                    s.guild_id,
                    s.attendance_date,
                    s.start_at,
                    s.late_at,
                    s.close_at,
                    s.verification_end_at,
                    s.required_voice_seconds,
                    s.early_leave_penalty,
                    s.no_participation_penalty,
                    s.closed_at,
                    gs.attendance_channel_id,
                    gs.announcement_channel_id,
                    gs.timezone
                FROM attendance_sessions AS s
                JOIN guild_settings AS gs ON gs.guild_id = s.guild_id
                WHERE s.status = 'CLOSED'
                  AND s.close_announced_at IS NULL
                ORDER BY s.closed_at, s.id;
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def mark_start_announced(
        self,
        *,
        session_id: int,
        now: str,
    ) -> None:
        """세션 시작 안내를 전송 완료로 표시한다."""

        connection = await self.database.connect()

        try:
            await connection.execute(
                """
                UPDATE attendance_sessions
                SET start_announced_at = ?, updated_at = ?
                WHERE id = ? AND start_announced_at IS NULL;
                """,
                (now, now, session_id),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def mark_close_announced(
        self,
        *,
        session_id: int,
        now: str,
    ) -> None:
        """세션 마감 안내를 전송 완료로 표시한다."""

        connection = await self.database.connect()

        try:
            await connection.execute(
                """
                UPDATE attendance_sessions
                SET close_announced_at = ?, updated_at = ?
                WHERE id = ? AND close_announced_at IS NULL;
                """,
                (now, now, session_id),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
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

    async def cancel_session(
        self,
        *,
        session_id: int,
        reason: str,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """예약 또는 열린 세션을 취소 상태로 표시한다."""

        await connection.execute(
            """
            UPDATE attendance_sessions
            SET
                status = 'CANCELLED',
                cancelled_at = ?,
                cancel_reason = ?,
                updated_at = ?
            WHERE id = ?
              AND status IN ('SCHEDULED', 'OPEN');
            """,
            (now, reason, now, session_id),
        )

    async def resume_cancelled_session(
        self,
        *,
        session_id: int,
        status: str,
        opened_at: str | None,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """취소된 세션을 SCHEDULED 또는 OPEN 상태로 재개한다."""

        if status not in {"SCHEDULED", "OPEN"}:
            raise ValueError("Resumed session status must be SCHEDULED or OPEN.")

        await connection.execute(
            """
            UPDATE attendance_sessions
            SET
                status = ?,
                opened_at = ?,
                cancelled_at = NULL,
                cancel_reason = NULL,
                start_announced_at = NULL,
                close_announced_at = NULL,
                updated_at = ?
            WHERE id = ?
              AND status = 'CANCELLED';
            """,
            (status, opened_at, now, session_id),
        )

    async def list_attendance_score_events(
        self,
        *,
        session_id: int,
        connection: aiosqlite.Connection,
    ) -> list[dict[str, Any]]:
        """세션의 출석 기록에서 생성된 점수 이벤트를 조회한다."""

        cursor = await connection.execute(
            """
            SELECT
                se.id,
                se.guild_id,
                se.member_id,
                se.event_type,
                se.delta,
                se.reference_type,
                se.reference_id,
                se.description
            FROM attendance_records AS ar
            JOIN score_events AS se
                ON se.reference_type = 'ATTENDANCE'
                AND se.reference_id = ar.id
            WHERE ar.session_id = ?
              AND se.reversed_event_id IS NULL
            ORDER BY se.id;
            """,
            (session_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def list_session_reversal_events(
        self,
        *,
        session_id: int,
        prefix: str,
        connection: aiosqlite.Connection,
    ) -> list[dict[str, Any]]:
        """중복 방지 키 접두사로 세션 취소/재개 되돌림 이벤트를 조회한다."""

        cursor = await connection.execute(
            """
            SELECT
                id,
                guild_id,
                member_id,
                event_type,
                delta,
                reference_type,
                reference_id,
                dedup_key,
                description,
                reversed_event_id
            FROM score_events
            WHERE reference_type = 'SESSION'
              AND reference_id = ?
              AND dedup_key LIKE ?
            ORDER BY id;
            """,
            (session_id, f"{prefix}:{session_id}:%"),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]
