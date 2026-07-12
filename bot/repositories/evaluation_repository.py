"""간부 평가 데이터에 대한 SQLite 접근을 담당한다."""

from typing import Any

import aiosqlite

from bot.db.database import Database


class EvaluationRepository:
    """``evaluations`` 행을 다루는 SQL을 실행한다.

    The repository only persists and fetches rows. Business rules such as score
    range, self-evaluation prevention, and reversal creation live in the
    service layer so database access stays easy to test.
    """

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
        evaluator_discord_id: str,
        score: int,
        reason: str,
        created_at: str,
        connection: aiosqlite.Connection,
    ) -> int:
        """호출자 트랜잭션 안에서 ACTIVE 평가 행을 생성한다."""

        cursor = await connection.execute(
            """
            INSERT INTO evaluations (
                guild_id,
                member_id,
                score_event_id,
                evaluator_discord_id,
                score,
                reason,
                status,
                created_at
            )
            VALUES (?, ?, NULL, ?, ?, ?, 'ACTIVE', ?);
            """,
            (
                guild_id,
                member_id,
                evaluator_discord_id,
                score,
                reason,
                created_at,
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def set_score_event_id(
        self,
        *,
        evaluation_id: int,
        score_event_id: int,
        connection: aiosqlite.Connection,
    ) -> None:
        """평가 행에 기본 점수 이벤트를 연결한다."""

        await connection.execute(
            """
            UPDATE evaluations
            SET score_event_id = ?
            WHERE id = ?;
            """,
            (score_event_id, evaluation_id),
        )

    async def get_by_id(
        self,
        *,
        evaluation_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """ID로 평가 행 하나를 조회한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    e.id,
                    e.guild_id,
                    e.member_id,
                    e.score_event_id,
                    e.evaluator_discord_id,
                    e.score,
                    e.reason,
                    e.status,
                    e.created_at,
                    e.cancelled_at,
                    e.cancelled_by_discord_id,
                    e.cancellation_reason,
                    e.reversal_score_event_id,
                    m.discord_id,
                    m.display_name
                FROM evaluations AS e
                JOIN members AS m ON m.id = e.member_id
                WHERE e.id = ?;
                """,
                (evaluation_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def mark_cancelled(
        self,
        *,
        evaluation_id: int,
        cancelled_at: str,
        cancelled_by_discord_id: str,
        cancellation_reason: str,
        reversal_score_event_id: int,
        connection: aiosqlite.Connection,
    ) -> None:
        """ACTIVE 평가 행을 취소 상태로 변경한다."""

        await connection.execute(
            """
            UPDATE evaluations
            SET
                status = 'CANCELLED',
                cancelled_at = ?,
                cancelled_by_discord_id = ?,
                cancellation_reason = ?,
                reversal_score_event_id = ?
            WHERE id = ? AND status = 'ACTIVE';
            """,
            (
                cancelled_at,
                cancelled_by_discord_id,
                cancellation_reason,
                reversal_score_event_id,
                evaluation_id,
            ),
        )

    async def list_recent_active_for_member(
        self,
        *,
        member_id: int,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """멤버의 최근 공개 평가 요약을 반환한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT id, score, reason, created_at, evaluator_discord_id
                FROM evaluations
                WHERE member_id = ?
                  AND status = 'ACTIVE'
                ORDER BY created_at DESC, id DESC
                LIMIT ?;
                """,
                (member_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()
