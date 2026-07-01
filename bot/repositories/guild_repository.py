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
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?);
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