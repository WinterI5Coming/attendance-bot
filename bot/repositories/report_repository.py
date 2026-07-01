"""SQLite read queries for personal attendance reports."""

from typing import Any

from bot.db.database import Database


class ReportRepository:
    """Read attendance statistics for report services."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database: Database object that opens configured SQLite connections.
        """

        self.database = database

    async def get_attendance_summary(self, *, member_id: int) -> dict[str, int]:
        """Return attendance counts for a member's non-cancelled sessions.

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
                    COUNT(*) AS total_sessions,
                    SUM(CASE WHEN ar.status = 'PRESENT' THEN 1 ELSE 0 END) AS present,
                    SUM(CASE WHEN ar.status = 'LATE' THEN 1 ELSE 0 END) AS late,
                    SUM(CASE WHEN ar.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent,
                    SUM(CASE WHEN ar.status = 'EXCUSED_LATE' THEN 1 ELSE 0 END) AS excused_late,
                    SUM(CASE WHEN ar.status = 'EXCUSED_ABSENT' THEN 1 ELSE 0 END) AS excused_absent
                FROM attendance_session_members AS asm
                JOIN attendance_sessions AS s ON s.id = asm.session_id
                LEFT JOIN attendance_records AS ar
                    ON ar.session_id = asm.session_id
                    AND ar.member_id = asm.member_id
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
        """Return guild-wide attendance counts for sessions in a UTC range."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    COUNT(asm.id) AS total_targets,
                    SUM(CASE WHEN ar.status = 'PRESENT' THEN 1 ELSE 0 END) AS present,
                    SUM(CASE WHEN ar.status = 'LATE' THEN 1 ELSE 0 END) AS late,
                    SUM(CASE WHEN ar.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent,
                    SUM(CASE WHEN ar.status = 'EXCUSED_LATE' THEN 1 ELSE 0 END) AS excused_late,
                    SUM(CASE WHEN ar.status = 'EXCUSED_ABSENT' THEN 1 ELSE 0 END) AS excused_absent
                FROM attendance_session_members AS asm
                JOIN attendance_sessions AS s ON s.id = asm.session_id
                LEFT JOIN attendance_records AS ar
                    ON ar.session_id = asm.session_id
                    AND ar.member_id = asm.member_id
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
        """Return per-member weekly attendance and score deltas."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    m.id AS member_id,
                    m.discord_id,
                    m.display_name,
                    COUNT(asm.id) AS total_sessions,
                    SUM(CASE WHEN ar.status = 'PRESENT' THEN 1 ELSE 0 END) AS present,
                    SUM(CASE WHEN ar.status = 'LATE' THEN 1 ELSE 0 END) AS late,
                    SUM(CASE WHEN ar.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent,
                    SUM(CASE WHEN ar.status = 'EXCUSED_LATE' THEN 1 ELSE 0 END) AS excused_late,
                    SUM(CASE WHEN ar.status = 'EXCUSED_ABSENT' THEN 1 ELSE 0 END) AS excused_absent,
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
                LEFT JOIN attendance_records AS ar
                    ON ar.session_id = asm.session_id
                    AND ar.member_id = asm.member_id
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
