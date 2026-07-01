"""출석 대상 대원(members)에 대한 SQLite 접근을 담당한다."""

from typing import Any

from bot.db.database import Database


class MemberRepository:
    """members 테이블의 조회, 등록, 재활성화, 비활성화를 담당한다."""

    def __init__(self, database: Database) -> None:
        """Repository를 초기화한다.

        Args:
            database:
                SQLite 연결을 제공하는 Database 객체.
        """

        self.database = database

    async def get_by_discord_id(
        self,
        *,
        guild_id: str,
        discord_id: str,
    ) -> dict[str, Any] | None:
        """서버 ID와 Discord ID에 해당하는 대원 행을 조회한다.

        Args:
            guild_id:
                Discord 서버 ID.
            discord_id:
                조회할 대상 사용자의 Discord ID.

        Returns:
            대원 행이 있으면 딕셔너리, 없으면 None.
        """

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    guild_id,
                    discord_id,
                    display_name,
                    is_active,
                    activated_at,
                    deactivated_at,
                    created_by_discord_id,
                    updated_at
                FROM members
                WHERE guild_id = ? AND discord_id = ?;
                """,
                (guild_id, discord_id),
            )

            row = await cursor.fetchone()
            await cursor.close()

            if row is None:
                return None

            return dict(row)
        finally:
            await connection.close()

    async def create(
        self,
        *,
        guild_id: str,
        discord_id: str,
        display_name: str,
        created_by_discord_id: str,
        now: str,
    ) -> int:
        """새로운 대원 행을 활성 상태로 생성한다.

        `UNIQUE(guild_id, discord_id)` 제약이 있으므로 동일 서버에
        동일 사용자가 동시에 등록을 시도하면 둘 중 하나는 반드시
        `aiosqlite.IntegrityError`로 실패한다. 이 경우 호출자가
        현재 행을 다시 조회해 적절한 결과를 판단해야 한다.

        Args:
            guild_id:
                Discord 서버 ID.
            discord_id:
                등록할 대상 사용자의 Discord ID.
            display_name:
                등록 시점의 Discord 표시 이름.
            created_by_discord_id:
                등록 명령을 실행한 사용자의 Discord ID.
            now:
                timezone-aware UTC ISO 8601 형식의 현재 시각.

        Returns:
            새로 생성된 members 행의 id.

        Raises:
            aiosqlite.IntegrityError:
                `UNIQUE(guild_id, discord_id)` 제약을 위반한 경우.
        """

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                INSERT INTO members (
                    guild_id,
                    discord_id,
                    display_name,
                    is_active,
                    activated_at,
                    deactivated_at,
                    created_by_discord_id,
                    updated_at
                )
                VALUES (?, ?, ?, 1, ?, NULL, ?, ?);
                """,
                (
                    guild_id,
                    discord_id,
                    display_name,
                    now,
                    created_by_discord_id,
                    now,
                ),
            )

            await connection.commit()

            assert cursor.lastrowid is not None
            return cursor.lastrowid

        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def reactivate(
        self,
        *,
        guild_id: str,
        discord_id: str,
        display_name: str,
        now: str,
    ) -> int:
        """과거에 제외된 대원 행을 다시 활성 상태로 되돌린다.

        기존 행을 재사용하며 새로운 행을 만들지 않는다.
        `activated_at`을 갱신하고 `deactivated_at`을 NULL로 되돌린다.

        Args:
            guild_id:
                Discord 서버 ID.
            discord_id:
                재활성화할 대상 사용자의 Discord ID.
            display_name:
                재등록 시점의 최신 Discord 표시 이름.
            now:
                timezone-aware UTC ISO 8601 형식의 현재 시각.

        Returns:
            재활성화된 members 행의 id.
        """

        connection = await self.database.connect()

        try:
            await connection.execute(
                """
                UPDATE members
                SET
                    display_name = ?,
                    is_active = 1,
                    activated_at = ?,
                    deactivated_at = NULL,
                    updated_at = ?
                WHERE guild_id = ? AND discord_id = ?;
                """,
                (display_name, now, now, guild_id, discord_id),
            )

            await connection.commit()

            cursor = await connection.execute(
                """
                SELECT id
                FROM members
                WHERE guild_id = ? AND discord_id = ?;
                """,
                (guild_id, discord_id),
            )

            row = await cursor.fetchone()
            await cursor.close()

            assert row is not None
            return row["id"]

        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def deactivate(
        self,
        *,
        guild_id: str,
        discord_id: str,
        display_name: str,
        now: str,
    ) -> None:
        """활성 대원을 물리 삭제하지 않고 비활성화한다.

        과거 출석 기록과 연결될 수 있는 members 행 자체는 유지하고
        `is_active`만 0으로 변경한다.

        Args:
            guild_id:
                Discord 서버 ID.
            discord_id:
                제외할 대상 사용자의 Discord ID.
            display_name:
                제외 시점의 최신 Discord 표시 이름.
            now:
                timezone-aware UTC ISO 8601 형식의 현재 시각.
        """

        connection = await self.database.connect()

        try:
            await connection.execute(
                """
                UPDATE members
                SET
                    display_name = ?,
                    is_active = 0,
                    deactivated_at = ?,
                    updated_at = ?
                WHERE guild_id = ? AND discord_id = ?;
                """,
                (display_name, now, now, guild_id, discord_id),
            )

            await connection.commit()

        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_active(
        self,
        *,
        guild_id: str,
    ) -> list[dict[str, Any]]:
        """현재 서버의 활성 대원 목록을 조회한다.

        Args:
            guild_id:
                Discord 서버 ID.

        Returns:
            `display_name` 오름차순으로 정렬된 활성 대원 목록.
        """

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    discord_id,
                    display_name
                FROM members
                WHERE guild_id = ? AND is_active = 1
                ORDER BY display_name COLLATE NOCASE;
                """,
                (guild_id,),
            )

            rows = await cursor.fetchall()
            await cursor.close()

            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def list_active_with_ids(
        self,
        *,
        guild_id: str,
    ) -> list[dict[str, Any]]:
        """현재 서버의 활성 대원을 내부 member id와 함께 조회한다.

        Args:
            guild_id:
                Discord 서버 ID.

        Returns:
            세션 참여자 스냅샷 생성에 사용할 활성 대원 목록. 각 행에는
            ``id``, ``discord_id``, ``display_name``이 포함된다.
        """

        connection = await self.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    discord_id,
                    display_name
                FROM members
                WHERE guild_id = ? AND is_active = 1
                ORDER BY display_name COLLATE NOCASE;
                """,
                (guild_id,),
            )

            rows = await cursor.fetchall()
            await cursor.close()

            return [dict(row) for row in rows]
        finally:
            await connection.close()
