"""Discord 서버 설정에 대한 SQLite 접근을 담당한다."""

from typing import Any

from bot.db.database import Database


class GuildRepository:
    """guild_settings 테이블의 조회와 저장을 담당한다."""

    def __init__(self, database: Database) -> None:
        """Repository를 초기화한다.

        Args:
            database:
                SQLite 연결을 제공하는 Database 객체.
        """

        self.database = database

    async def get_by_guild_id(
        self,
        guild_id: str,
    ) -> dict[str, Any] | None:
        """서버 ID에 해당하는 설정을 조회한다.

        Args:
            guild_id:
                Discord 서버 ID.

        Returns:
            설정이 있으면 딕셔너리, 없으면 None.
        """

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    guild_id,
                    timezone,
                    attendance_days,
                    attendance_start,
                    late_deadline,
                    close_deadline,
                    excuse_mode,
                    officer_role_id,
                    attendance_channel_id,
                    announcement_channel_id,
                    weekly_report_enabled,
                    excuse_deadline_time,
                    excuse_deadline_days_before,
                    require_excuse_approval,
                    allow_late_excuse,
                    voice_verification_enabled,
                    voice_channel_ids,
                    voice_category_ids,
                    exempt_absence_counts_in_attendance_denominator,
                    created_at,
                    updated_at
                FROM guild_settings
                WHERE guild_id = ?;
                """,
                (guild_id,),
            )

            row = await cursor.fetchone()
            await cursor.close()

            if row is None:
                return None

            return dict(row)
        finally:
            await connection.close()

    async def create_settings(
        self,
        *,
        guild_id: str,
        timezone_name: str,
        attendance_days: str,
        attendance_start: str,
        late_deadline: str,
        close_deadline: str,
        excuse_mode: str,
        officer_role_id: str,
        attendance_channel_id: str,
        announcement_channel_id: str,
        created_at: str,
        excuse_deadline_time: str = "23:00",
        excuse_deadline_days_before: int = 1,
        require_excuse_approval: bool = True,
        allow_late_excuse: bool = False,
    ) -> bool:
        """서버 설정을 최초 생성한다.

        이미 설정이 존재하면 수정하지 않고 False를 반환한다.

        Returns:
            새 설정을 만들었으면 True, 이미 존재하면 False.
        """

        connection = await self.database.connect()

        try:
            await connection.execute(
                "BEGIN IMMEDIATE;"
            )

            cursor = await connection.execute(
                """
                SELECT 1
                FROM guild_settings
                WHERE guild_id = ?;
                """,
                (guild_id,),
            )

            existing_row = await cursor.fetchone()
            await cursor.close()

            if existing_row is not None:
                await connection.rollback()
                return False

            await connection.execute(
                """
                INSERT INTO guild_settings (
                    guild_id,
                    timezone,
                    attendance_days,
                    attendance_start,
                    late_deadline,
                    close_deadline,
                    excuse_mode,
                    officer_role_id,
                    attendance_channel_id,
                    announcement_channel_id,
                    weekly_report_enabled,
                    excuse_deadline_time,
                    excuse_deadline_days_before,
                    require_excuse_approval,
                    allow_late_excuse,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?);
                """,
                (
                    guild_id,
                    timezone_name,
                    attendance_days,
                    attendance_start,
                    late_deadline,
                    close_deadline,
                    excuse_mode,
                    officer_role_id,
                    attendance_channel_id,
                    announcement_channel_id,
                    excuse_deadline_time,
                    excuse_deadline_days_before,
                    1 if require_excuse_approval else 0,
                    1 if allow_late_excuse else 0,
                    created_at,
                    created_at,
                ),
            )

            await connection.commit()
            return True

        except Exception:
            await connection.rollback()
            raise

        finally:
            await connection.close()

    async def update_attendance_times(
        self,
        *,
        guild_id: str,
        attendance_start: str,
        late_deadline: str,
        close_deadline: str,
        now: str,
    ) -> bool:
        """설정된 서버의 출석 시간 설정을 변경한다."""

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                UPDATE guild_settings
                SET
                    attendance_start = ?,
                    late_deadline = ?,
                    close_deadline = ?,
                    updated_at = ?
                WHERE guild_id = ?;
                """,
                (
                    attendance_start,
                    late_deadline,
                    close_deadline,
                    now,
                    guild_id,
                ),
            )
            await connection.commit()
            return cursor.rowcount > 0
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def update_settings(
        self,
        *,
        guild_id: str,
        fields: dict[str, str],
        now: str,
        connection,
    ) -> None:
        """검증된 서버 설정 일부를 호출자 트랜잭션 안에서 변경한다."""

        allowed_fields = {
            "timezone",
            "attendance_days",
            "attendance_start",
            "late_deadline",
            "close_deadline",
            "excuse_mode",
            "officer_role_id",
            "attendance_channel_id",
            "announcement_channel_id",
            "excuse_deadline_time",
            "excuse_deadline_days_before",
            "require_excuse_approval",
            "allow_late_excuse",
            "voice_verification_enabled",
            "voice_channel_ids",
            "voice_category_ids",
            "exempt_absence_counts_in_attendance_denominator",
        }
        invalid_fields = set(fields) - allowed_fields
        if invalid_fields:
            raise ValueError(f"Unsupported guild setting fields: {sorted(invalid_fields)!r}")
        if not fields:
            return

        assignments = ", ".join([f"{field} = ?" for field in fields])
        values = list(fields.values())
        values.extend([now, guild_id])
        await connection.execute(
            f"""
            UPDATE guild_settings
            SET {assignments}, updated_at = ?
            WHERE guild_id = ?;
            """,
            values,
        )

    async def update_session_window_if_unrecorded(
        self,
        *,
        guild_id: str,
        attendance_date: str,
        start_at: str,
        late_at: str,
        close_at: str,
        status: str,
        opened_at: str | None,
        now: str,
    ) -> str:
        """기록이 없을 때 오늘의 OPEN/SCHEDULED 세션 시간을 변경한다."""

        connection = await self.database.connect()

        try:
            session_rows = await connection.execute_fetchall(
                """
                SELECT id, status
                FROM attendance_sessions
                WHERE guild_id = ? AND attendance_date = ?;
                """,
                (guild_id, attendance_date),
            )
            if not session_rows:
                return "NO_SESSION"

            session = session_rows[0]
            if session["status"] not in {"SCHEDULED", "OPEN"}:
                return "SESSION_LOCKED"

            record_rows = await connection.execute_fetchall(
                """
                SELECT 1
                FROM attendance_records
                WHERE session_id = ?
                LIMIT 1;
                """,
                (session["id"],),
            )
            if record_rows:
                return "HAS_RECORDS"

            await connection.execute(
                """
                UPDATE attendance_sessions
                SET
                    start_at = ?,
                    late_at = ?,
                    close_at = ?,
                    status = ?,
                    opened_at = ?,
                    start_announced_at = NULL,
                    close_announced_at = NULL,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    start_at,
                    late_at,
                    close_at,
                    status,
                    opened_at,
                    now,
                    session["id"],
                ),
            )
            await connection.commit()
            return "UPDATED"
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_all_settings(self) -> list[dict[str, Any]]:
        """설정이 완료된 모든 서버 설정을 조회한다.

        Returns:
            자동 스케줄러가 순회할 guild_settings 행 목록.
        """

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    guild_id,
                    timezone,
                    attendance_days,
                    attendance_start,
                    late_deadline,
                    close_deadline,
                    excuse_mode,
                    officer_role_id,
                    attendance_channel_id,
                    announcement_channel_id,
                    weekly_report_enabled,
                    excuse_deadline_time,
                    excuse_deadline_days_before,
                    require_excuse_approval,
                    allow_late_excuse,
                    voice_verification_enabled,
                    voice_channel_ids,
                    voice_category_ids,
                    exempt_absence_counts_in_attendance_denominator,
                    created_at,
                    updated_at
                FROM guild_settings
                ORDER BY guild_id;
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()
