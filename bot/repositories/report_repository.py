"""개인 출석 리포트용 SQLite 조회 쿼리를 담당한다."""

from typing import Any

from bot.db.database import Database


class ReportRepository:
    """리포트 서비스에 필요한 출석 통계를 조회한다."""

    def __init__(self, database: Database) -> None:
        """저장소 의존성을 초기화한다.

        Args:
            database: Database object that opens configured SQLite connections.
        """

        self.database = database

    async def get_attendance_summary(self, *, member_id: int) -> dict[str, int]:
        """취소되지 않은 세션 기준 멤버의 출석 횟수를 반환한다.

        Args:
            member_id: members.id.

        Returns:
            Dict with total_sessions and status count fields.
        """

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    SUM(CASE
                        WHEN abs_adj.id IS NOT NULL
                             AND gs.exempt_absence_counts_in_attendance_denominator = 0
                            THEN 0
                        ELSE 1
                    END) AS total_sessions,
                    SUM(CASE
                        WHEN late_adj.resulting_status = 'PRESENT' THEN 1
                        WHEN abs_adj.id IS NULL AND ar.status = 'PRESENT' THEN 1
                        ELSE 0
                    END) AS present,
                    SUM(CASE
                        WHEN late_adj.resulting_status = 'LATE' THEN 1
                        WHEN late_adj.id IS NULL AND abs_adj.id IS NULL AND ar.status = 'LATE' THEN 1
                        ELSE 0
                    END) AS late,
                    SUM(CASE WHEN abs_adj.id IS NULL AND ar.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent,
                    SUM(CASE
                        WHEN late_adj.id IS NULL AND abs_adj.id IS NULL AND ar.status = 'EXCUSED_LATE' THEN 1
                        ELSE 0
                    END) AS excused_late,
                    SUM(CASE WHEN abs_adj.id IS NULL AND ar.status = 'EXCUSED_ABSENT' THEN 1 ELSE 0 END) AS excused_absent
                FROM attendance_session_members AS asm
                JOIN attendance_sessions AS s ON s.id = asm.session_id
                JOIN guild_settings AS gs ON gs.guild_id = s.guild_id
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
                WHERE asm.member_id = ?
                  AND s.status != 'CANCELLED';
                """,
                (member_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            assert row is not None
            return {
                "total_sessions": int(row["total_sessions"] or 0),
                "present": int(row["present"] or 0),
                "late": int(row["late"] or 0),
                "absent": int(row["absent"] or 0),
                "excused_late": int(row["excused_late"] or 0),
                "excused_absent": int(row["excused_absent"] or 0),
            }
        finally:
            await connection.close()

    async def get_weekly_summary(
        self,
        *,
        guild_id: str,
        start_at: str,
        end_at: str,
    ) -> dict[str, int]:
        """UTC 범위 안의 세션에 대한 서버 전체 출석 횟수를 반환한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    SUM(CASE
                        WHEN abs_adj.id IS NOT NULL
                             AND gs.exempt_absence_counts_in_attendance_denominator = 0
                            THEN 0
                        ELSE 1
                    END) AS total_targets,
                    SUM(CASE
                        WHEN late_adj.resulting_status = 'PRESENT' THEN 1
                        WHEN abs_adj.id IS NULL AND ar.status = 'PRESENT' THEN 1
                        ELSE 0
                    END) AS present,
                    SUM(CASE
                        WHEN late_adj.resulting_status = 'LATE' THEN 1
                        WHEN late_adj.id IS NULL AND abs_adj.id IS NULL AND ar.status = 'LATE' THEN 1
                        ELSE 0
                    END) AS late,
                    SUM(CASE WHEN abs_adj.id IS NULL AND ar.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent,
                    SUM(CASE
                        WHEN late_adj.id IS NULL AND abs_adj.id IS NULL AND ar.status = 'EXCUSED_LATE' THEN 1
                        ELSE 0
                    END) AS excused_late,
                    SUM(CASE WHEN abs_adj.id IS NULL AND ar.status = 'EXCUSED_ABSENT' THEN 1 ELSE 0 END) AS excused_absent
                FROM attendance_session_members AS asm
                JOIN attendance_sessions AS s ON s.id = asm.session_id
                JOIN guild_settings AS gs ON gs.guild_id = s.guild_id
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
                WHERE s.guild_id = ?
                  AND s.status != 'CANCELLED'
                  AND s.start_at >= ?
                  AND s.start_at < ?;
                """,
                (guild_id, start_at, end_at),
            )
            row = await cursor.fetchone()
            await cursor.close()
            assert row is not None
            return {
                "total_targets": int(row["total_targets"] or 0),
                "present": int(row["present"] or 0),
                "late": int(row["late"] or 0),
                "absent": int(row["absent"] or 0),
                "excused_late": int(row["excused_late"] or 0),
                "excused_absent": int(row["excused_absent"] or 0),
            }
        finally:
            await connection.close()

    async def get_weekly_member_rows(
        self,
        *,
        guild_id: str,
        start_at: str,
        end_at: str,
    ) -> list[dict[str, Any]]:
        """멤버별 주간 출석 횟수와 점수 변동을 반환한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    m.id AS member_id,
                    m.discord_id,
                    m.display_name,
                    SUM(CASE
                        WHEN abs_adj.id IS NOT NULL
                             AND gs.exempt_absence_counts_in_attendance_denominator = 0
                            THEN 0
                        ELSE 1
                    END) AS total_sessions,
                    SUM(CASE
                        WHEN late_adj.resulting_status = 'PRESENT' THEN 1
                        WHEN abs_adj.id IS NULL AND ar.status = 'PRESENT' THEN 1
                        ELSE 0
                    END) AS present,
                    SUM(CASE
                        WHEN late_adj.resulting_status = 'LATE' THEN 1
                        WHEN late_adj.id IS NULL AND abs_adj.id IS NULL AND ar.status = 'LATE' THEN 1
                        ELSE 0
                    END) AS late,
                    SUM(CASE WHEN abs_adj.id IS NULL AND ar.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent,
                    SUM(CASE
                        WHEN late_adj.id IS NULL AND abs_adj.id IS NULL AND ar.status = 'EXCUSED_LATE' THEN 1
                        ELSE 0
                    END) AS excused_late,
                    SUM(CASE WHEN abs_adj.id IS NULL AND ar.status = 'EXCUSED_ABSENT' THEN 1 ELSE 0 END) AS excused_absent,
                    (
                        SELECT COALESCE(SUM(se.delta), 0)
                        FROM score_events AS se
                        WHERE se.member_id = m.id
                          AND se.created_at >= ?
                          AND se.created_at < ?
                    ) AS weekly_score
                FROM members AS m
                JOIN attendance_session_members AS asm ON asm.member_id = m.id
                JOIN attendance_sessions AS s ON s.id = asm.session_id
                JOIN guild_settings AS gs ON gs.guild_id = s.guild_id
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
                WHERE m.guild_id = ?
                  AND s.status != 'CANCELLED'
                  AND s.start_at >= ?
                  AND s.start_at < ?
                GROUP BY m.id
                ORDER BY weekly_score DESC, present DESC, m.display_name COLLATE NOCASE;
                """,
                (start_at, end_at, guild_id, start_at, end_at),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()
