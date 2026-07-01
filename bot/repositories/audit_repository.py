"""SQLite access for administrator audit logs."""

import aiosqlite

from bot.db.database import Database


class AuditRepository:
    """Run SQL for ``audit_logs`` rows."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database: Database object that opens configured SQLite connections.
        """

        self.database = database

    async def create_log(
        self,
        *,
        guild_id: str,
        actor_discord_id: str,
        action_type: str,
        target_type: str,
        target_id: str,
        before_json: str | None,
        after_json: str | None,
        reason: str,
        created_at: str,
        connection: aiosqlite.Connection,
    ) -> int:
        """Create one audit log row inside a caller-owned transaction.

        Args:
            guild_id: Discord guild ID.
            actor_discord_id: Actor Discord ID.
            action_type: Audit action type.
            target_type: Target category.
            target_id: Target identifier as text.
            before_json: JSON snapshot before the change.
            after_json: JSON snapshot after the change.
            reason: Required administrator reason.
            created_at: UTC ISO 8601 creation time.
            connection: Existing transaction connection.

        Returns:
            Created audit_logs.id.
        """

        cursor = await connection.execute(
            """
            INSERT INTO audit_logs (
                guild_id,
                actor_discord_id,
                action_type,
                target_type,
                target_id,
                before_json,
                after_json,
                reason,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                guild_id,
                actor_discord_id,
                action_type,
                target_type,
                target_id,
                before_json,
                after_json,
                reason,
                created_at,
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid
