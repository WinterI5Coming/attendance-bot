"""점수 원장 이벤트에 대한 SQLite 접근을 담당한다."""

from typing import Any

import aiosqlite

from bot.db.database import Database


class ScoreRepository:
    """``score_events`` 원장 행을 다루는 SQL을 실행한다."""

    def __init__(self, database: Database) -> None:
        """저장소 의존성을 초기화한다.

        Args:
            database: Database object that opens configured SQLite connections.
        """

        self.database = database

    async def create_attendance_event(
        self,
        *,
        guild_id: str,
        member_id: int,
        attendance_record_id: int,
        attendance_status: str,
        delta: int,
        description: str,
        created_at: str,
        connection: aiosqlite.Connection,
    ) -> int:
        """출석 기록 하나에 대한 점수 이벤트를 생성한다.

        Args:
            guild_id: Discord guild ID stored as text.
            member_id: ``members.id`` receiving the score delta.
            attendance_record_id: ``attendance_records.id`` used as the
                reference and deduplication key.
            attendance_status: PRESENT or LATE, used to choose the event type.
            delta: Score delta from ``score_policy``.
            description: User-facing ledger description.
            created_at: UTC ISO 8601 creation timestamp.
            connection: Existing transaction connection owned by the service.

        Returns:
            Created ``score_events.id``.

        Raises:
            ValueError: If ``attendance_status`` is not supported here.
            aiosqlite.IntegrityError: If a UNIQUE or FOREIGN KEY constraint is
                violated.
        """

        event_types = {
            "PRESENT": "ATTENDANCE_PRESENT",
            "LATE": "ATTENDANCE_LATE",
            "ABSENT": "ATTENDANCE_ABSENT",
            "EXCUSED_LATE": "ATTENDANCE_EXCUSED_LATE",
            "EXCUSED_ABSENT": "ATTENDANCE_EXCUSED_ABSENT",
        }

        try:
            event_type = event_types[attendance_status]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported attendance score event status: {attendance_status!r}"
            ) from exc

        # 중복 방지 키(dedup_key)는 재시도나 경합이 여기까지 도달해도
        # 하나의 출석 기록에 대한 점수 생성이 한 번만 일어나게 한다.
        cursor = await connection.execute(
            """
            INSERT INTO score_events (
                guild_id,
                member_id,
                event_type,
                delta,
                reference_type,
                reference_id,
                dedup_key,
                description,
                created_by_discord_id,
                created_at,
                reversed_event_id
            )
            VALUES (?, ?, ?, ?, 'ATTENDANCE', ?, ?, ?, NULL, ?, NULL);
            """,
            (
                guild_id,
                member_id,
                event_type,
                delta,
                attendance_record_id,
                f"attendance:{attendance_record_id}",
                description,
                created_at,
            ),
        )

        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_total_score(
        self,
        *,
        member_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> int:
        """점수 원장에서 멤버의 현재 총점을 반환한다.

        Args:
            member_id: ``members.id``.
            connection: Optional existing connection for transaction reuse.

        Returns:
            Sum of all score event deltas for the member, or 0 if none exist.
        """

        owns_connection = connection is None
        if connection is None:
            connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT COALESCE(SUM(delta), 0) AS total_score
                FROM score_events
                WHERE member_id = ?;
                """,
                (member_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            assert row is not None
            return int(row["total_score"])
        finally:
            if owns_connection:
                await connection.close()

    async def create_correction_event(
        self,
        *,
        guild_id: str,
        member_id: int,
        attendance_record_id: int,
        delta: int,
        dedup_key: str,
        description: str,
        created_by_discord_id: str,
        created_at: str,
        connection: aiosqlite.Connection,
    ) -> int:
        """출석 정정 점수 이벤트를 생성한다.

        Args:
            guild_id: Discord 서버 ID.
            member_id: members.id.
            attendance_record_id: 참조 출석 기록 ID.
            delta: score_policy 기반 점수 차이.
            dedup_key: correction:{record_id}:{uuid} 형식.
            description: 원장 설명.
            created_by_discord_id: 정정 실행자 Discord ID.
            created_at: UTC ISO 8601 생성 시각.
            connection: 정정 트랜잭션 connection.

        Returns:
            생성된 score_events.id.
        """

        cursor = await connection.execute(
            """
            INSERT INTO score_events (
                guild_id,
                member_id,
                event_type,
                delta,
                reference_type,
                reference_id,
                dedup_key,
                description,
                created_by_discord_id,
                created_at,
                reversed_event_id
            )
            VALUES (?, ?, 'ATTENDANCE_CORRECTION', ?, 'ATTENDANCE', ?, ?, ?, ?, ?, NULL);
            """,
            (
                guild_id,
                member_id,
                delta,
                attendance_record_id,
                dedup_key,
                description,
                created_by_discord_id,
                created_at,
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def create_event(
        self,
        *,
        guild_id: str,
        member_id: int,
        event_type: str,
        delta: int,
        reference_type: str | None,
        reference_id: int | None,
        dedup_key: str,
        description: str,
        created_by_discord_id: str | None,
        created_at: str,
        connection: aiosqlite.Connection,
    ) -> int:
        """명시적인 중복 방지 키를 가진 일반 점수 이벤트를 생성한다."""

        cursor = await connection.execute(
            """
            INSERT INTO score_events (
                guild_id,
                member_id,
                event_type,
                delta,
                reference_type,
                reference_id,
                dedup_key,
                description,
                created_by_discord_id,
                created_at,
                reversed_event_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL);
            """,
            (
                guild_id,
                member_id,
                event_type,
                delta,
                reference_type,
                reference_id,
                dedup_key,
                description,
                created_by_discord_id,
                created_at,
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def create_reversal_event(
        self,
        *,
        guild_id: str,
        member_id: int,
        event_type: str,
        delta: int,
        reference_type: str | None,
        reference_id: int | None,
        dedup_key: str,
        description: str,
        created_by_discord_id: str | None,
        created_at: str,
        reversed_event_id: int,
        connection: aiosqlite.Connection,
    ) -> int:
        """다른 점수 이벤트를 명시적으로 되돌리는 점수 이벤트를 생성한다.

        Score rows are append-only, so cancellation never deletes or edits the
        original points. A reversal keeps totals correct while preserving the
        historical reason for the original score.
        """

        cursor = await connection.execute(
            """
            INSERT INTO score_events (
                guild_id,
                member_id,
                event_type,
                delta,
                reference_type,
                reference_id,
                dedup_key,
                description,
                created_by_discord_id,
                created_at,
                reversed_event_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                guild_id,
                member_id,
                event_type,
                delta,
                reference_type,
                reference_id,
                dedup_key,
                description,
                created_by_discord_id,
                created_at,
                reversed_event_id,
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_by_id(
        self,
        *,
        score_event_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        """ID로 점수 원장 행 하나를 조회한다."""

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
                    event_type,
                    delta,
                    reference_type,
                    reference_id,
                    dedup_key,
                    description,
                    created_by_discord_id,
                    created_at,
                    reversed_event_id
                FROM score_events
                WHERE id = ?;
                """,
                (score_event_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            if owns_connection:
                await connection.close()

    async def list_recent_events(
        self,
        *,
        member_id: int,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """최근 점수 이벤트를 조회한다.

        Args:
            member_id: members.id.
            limit: 조회할 최대 이벤트 수.

        Returns:
            created_at 내림차순 점수 이벤트 목록.
        """

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    event_type,
                    delta,
                    description,
                    created_at
                FROM score_events
                WHERE member_id = ?
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
