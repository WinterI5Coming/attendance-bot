"""Stage A 출석 검증 데이터에 대한 SQLite 접근을 담당한다."""

from typing import Any

import aiosqlite

from bot.db.database import Database


class StageARepository:
    """출석 정책, 음성 로그, 검증 행을 다루는 SQL을 실행한다."""

    def __init__(self, database: Database) -> None:
        """저장소 의존성을 초기화한다.

        Args:
            database: Database object that opens configured SQLite connections.
        """

        self.database = database

    async def get_policy(
        self,
        *,
        guild_id: str,
        policy_type: str,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """서버의 평일/주말 정책 행을 반환한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM attendance_policies
                WHERE guild_id = ? AND policy_type = ?;
                """,
                (guild_id, policy_type),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def upsert_policy(
        self,
        *,
        guild_id: str,
        policy_type: str,
        enabled: bool,
        start_time: str,
        late_time: str,
        close_time: str,
        verification_end_time: str,
        required_voice_minutes: int,
        present_score: int,
        late_score: int,
        early_leave_penalty: int,
        no_participation_penalty: int,
        absent_score: int,
        now: str,
        connection: aiosqlite.Connection | None = None,
    ) -> None:
        """서버 출석 정책을 생성하거나 갱신한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            await connection.execute(
                """
                INSERT INTO attendance_policies (
                    guild_id,
                    policy_type,
                    enabled,
                    start_time,
                    late_time,
                    close_time,
                    verification_end_time,
                    required_voice_minutes,
                    present_score,
                    late_score,
                    early_leave_penalty,
                    no_participation_penalty,
                    absent_score,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, policy_type) DO UPDATE SET
                    enabled = excluded.enabled,
                    start_time = excluded.start_time,
                    late_time = excluded.late_time,
                    close_time = excluded.close_time,
                    verification_end_time = excluded.verification_end_time,
                    required_voice_minutes = excluded.required_voice_minutes,
                    present_score = excluded.present_score,
                    late_score = excluded.late_score,
                    early_leave_penalty = excluded.early_leave_penalty,
                    no_participation_penalty = excluded.no_participation_penalty,
                    absent_score = excluded.absent_score,
                    updated_at = excluded.updated_at;
                """,
                (
                    guild_id,
                    policy_type,
                    1 if enabled else 0,
                    start_time,
                    late_time,
                    close_time,
                    verification_end_time,
                    required_voice_minutes,
                    present_score,
                    late_score,
                    early_leave_penalty,
                    no_participation_penalty,
                    absent_score,
                    now,
                    now,
                ),
            )
            if owns_connection:
                await connection.commit()
        except Exception:
            if owns_connection:
                await connection.rollback()
            raise
        finally:
            if owns_connection:
                await connection.close()

    async def get_date_override(
        self,
        *,
        guild_id: str,
        attendance_date: str,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """특정 날짜 출석 예외 정책이 있으면 반환한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM attendance_date_overrides
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

    async def upsert_date_override(
        self,
        *,
        guild_id: str,
        attendance_date: str,
        enabled: bool,
        start_time: str,
        late_time: str,
        close_time: str,
        verification_end_time: str,
        required_voice_minutes: int,
        override_reason: str | None,
        created_by_discord_id: str,
        now: str,
    ) -> None:
        """날짜별 예외 정책을 생성하거나 갱신한다."""

        connection = await self.database.connect()
        try:
            await connection.execute(
                """
                INSERT INTO attendance_date_overrides (
                    guild_id,
                    attendance_date,
                    enabled,
                    start_time,
                    late_time,
                    close_time,
                    verification_end_time,
                    required_voice_minutes,
                    override_reason,
                    created_by_discord_id,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, attendance_date) DO UPDATE SET
                    enabled = excluded.enabled,
                    start_time = excluded.start_time,
                    late_time = excluded.late_time,
                    close_time = excluded.close_time,
                    verification_end_time = excluded.verification_end_time,
                    required_voice_minutes = excluded.required_voice_minutes,
                    override_reason = excluded.override_reason,
                    created_by_discord_id = excluded.created_by_discord_id,
                    updated_at = excluded.updated_at;
                """,
                (
                    guild_id,
                    attendance_date,
                    1 if enabled else 0,
                    start_time,
                    late_time,
                    close_time,
                    verification_end_time,
                    required_voice_minutes,
                    override_reason,
                    created_by_discord_id,
                    now,
                    now,
                ),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def delete_date_override(
        self,
        *,
        guild_id: str,
        attendance_date: str,
    ) -> bool:
        """날짜별 예외 정책 하나를 삭제한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                DELETE FROM attendance_date_overrides
                WHERE guild_id = ? AND attendance_date = ?;
                """,
                (guild_id, attendance_date),
            )
            await connection.commit()
            return cursor.rowcount > 0
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def create_verification(
        self,
        *,
        attendance_record_id: int,
        session_id: int,
        member_id: int,
        required_seconds: int,
        verification_end_at: str,
        now: str,
        connection: aiosqlite.Connection,
    ) -> int:
        """존재하지 않을 때 대기 중인 출석 검증 행을 생성한다."""

        cursor = await connection.execute(
            """
            INSERT OR IGNORE INTO attendance_verifications (
                attendance_record_id,
                session_id,
                member_id,
                status,
                required_seconds,
                accumulated_seconds,
                verification_end_at,
                verified_at,
                failed_at,
                failure_reason,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 'PENDING', ?, 0, ?, NULL, NULL, NULL, ?, ?);
            """,
            (
                attendance_record_id,
                session_id,
                member_id,
                required_seconds,
                verification_end_at,
                now,
                now,
            ),
        )
        if cursor.lastrowid is not None and cursor.lastrowid != 0:
            return int(cursor.lastrowid)
        existing = await self.get_verification_by_record_id(
            attendance_record_id=attendance_record_id,
            connection=connection,
        )
        assert existing is not None
        return int(existing["id"])

    async def get_verification_by_record_id(
        self,
        *,
        attendance_record_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """출석 기록 ID로 검증 행을 조회한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM attendance_verifications
                WHERE attendance_record_id = ?;
                """,
                (attendance_record_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def get_pending_verification(
        self,
        *,
        session_id: int,
        member_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """세션 멤버의 대기 중인 검증 행 하나를 조회한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM attendance_verifications
                WHERE session_id = ? AND member_id = ? AND status = 'PENDING';
                """,
                (session_id, member_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def list_pending_verifications(
        self,
        *,
        now: str | None = None,
        connection: aiosqlite.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """대기 중인 검증을 조회하며, 필요하면 종료 시간이 지난 항목만 반환한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            if now is None:
                cursor = await connection.execute(
                    """
                    SELECT *
                    FROM attendance_verifications
                    WHERE status = 'PENDING'
                    ORDER BY verification_end_at, id;
                    """
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT *
                    FROM attendance_verifications
                    WHERE status = 'PENDING' AND verification_end_at <= ?
                    ORDER BY verification_end_at, id;
                    """,
                    (now,),
                )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            if owns_connection:
                await connection.close()

    async def get_open_voice_log(
        self,
        *,
        session_id: int,
        member_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """세션 멤버의 현재 열린 음성 로그를 조회한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM voice_presence_logs
                WHERE session_id = ? AND member_id = ? AND left_at IS NULL;
                """,
                (session_id, member_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def open_voice_log(
        self,
        *,
        guild_id: str,
        session_id: int,
        member_id: int,
        voice_channel_id: str,
        joined_at: str,
        connection: aiosqlite.Connection,
    ) -> int | None:
        """음성 참여 로그를 열고 새로 생성된 ID를 반환한다."""

        cursor = await connection.execute(
            """
            INSERT OR IGNORE INTO voice_presence_logs (
                guild_id,
                session_id,
                member_id,
                voice_channel_id,
                joined_at,
                left_at,
                duration_seconds,
                close_reason,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?);
            """,
            (
                guild_id,
                session_id,
                member_id,
                voice_channel_id,
                joined_at,
                joined_at,
                joined_at,
            ),
        )
        if cursor.lastrowid is None or cursor.lastrowid == 0:
            return None
        return int(cursor.lastrowid)

    async def close_voice_log(
        self,
        *,
        voice_log_id: int,
        left_at: str,
        duration_seconds: int,
        close_reason: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """열려 있는 음성 참여 로그를 닫는다."""

        await connection.execute(
            """
            UPDATE voice_presence_logs
            SET
                left_at = ?,
                duration_seconds = ?,
                close_reason = ?,
                updated_at = ?
            WHERE id = ? AND left_at IS NULL;
            """,
            (
                left_at,
                duration_seconds,
                close_reason,
                left_at,
                voice_log_id,
            ),
        )

    async def list_voice_logs_for_verification(
        self,
        *,
        session_id: int,
        member_id: int,
        connection: aiosqlite.Connection,
    ) -> list[dict[str, Any]]:
        """검증 대상 하나에 대한 모든 음성 로그를 조회한다."""

        cursor = await connection.execute(
            """
            SELECT *
            FROM voice_presence_logs
            WHERE session_id = ? AND member_id = ?
            ORDER BY joined_at, id;
            """,
            (session_id, member_id),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def mark_verified(
        self,
        *,
        verification_id: int,
        accumulated_seconds: int,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """대기 중인 검증을 성공 상태로 표시한다."""

        await connection.execute(
            """
            UPDATE attendance_verifications
            SET
                status = 'VERIFIED',
                accumulated_seconds = ?,
                verified_at = ?,
                failed_at = NULL,
                failure_reason = NULL,
                updated_at = ?
            WHERE id = ? AND status = 'PENDING';
            """,
            (accumulated_seconds, now, now, verification_id),
        )

    async def mark_failed(
        self,
        *,
        verification_id: int,
        accumulated_seconds: int,
        failure_reason: str,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """대기 중인 검증을 실패 상태로 표시한다."""

        await connection.execute(
            """
            UPDATE attendance_verifications
            SET
                status = 'FAILED',
                accumulated_seconds = ?,
                verified_at = NULL,
                failed_at = ?,
                failure_reason = ?,
                updated_at = ?
            WHERE id = ? AND status = 'PENDING';
            """,
            (
                accumulated_seconds,
                now,
                failure_reason,
                now,
                verification_id,
            ),
        )

    async def update_accumulated_seconds(
        self,
        *,
        verification_id: int,
        accumulated_seconds: int,
        now: str,
        connection: aiosqlite.Connection,
    ) -> None:
        """검증 상태를 유지한 채 누적 초를 갱신한다."""

        await connection.execute(
            """
            UPDATE attendance_verifications
            SET accumulated_seconds = ?, updated_at = ?
            WHERE id = ? AND status = 'PENDING';
            """,
            (accumulated_seconds, now, verification_id),
        )
