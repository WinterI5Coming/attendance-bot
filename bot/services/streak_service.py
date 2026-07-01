"""Streak calculation and bonus application."""

from dataclasses import dataclass

import aiosqlite

from bot.policies.score_policy import STREAK_BONUS_3_DAYS, STREAK_BONUS_7_DAYS
from bot.repositories.score_repository import ScoreRepository


COUNTING_STATUSES = {"PRESENT", "LATE", "EXCUSED_LATE"}
NEUTRAL_STATUSES = {"EXCUSED_ABSENT"}
BONUSES = {
    3: STREAK_BONUS_3_DAYS,
    7: STREAK_BONUS_7_DAYS,
}


@dataclass(frozen=True)
class StreakBonusResult:
    """Result of applying a possible streak bonus."""

    current_streak: int
    bonus_delta: int = 0
    threshold: int | None = None


class StreakService:
    """Calculate current attendance streaks and award milestone bonuses."""

    def __init__(self, *, score_repository: ScoreRepository) -> None:
        self.score_repository = score_repository

    async def calculate_current_streak(
        self,
        *,
        guild_id: str,
        member_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> int:
        """Return the current streak for a guild member."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.score_repository.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT ar.status
                FROM attendance_session_members AS asm
                JOIN attendance_sessions AS s ON s.id = asm.session_id
                LEFT JOIN attendance_records AS ar
                    ON ar.session_id = asm.session_id
                    AND ar.member_id = asm.member_id
                WHERE s.guild_id = ?
                  AND asm.member_id = ?
                  AND s.status != 'CANCELLED'
                ORDER BY s.attendance_date DESC, s.id DESC;
                """,
                (guild_id, member_id),
            )
            rows = await cursor.fetchall()
            await cursor.close()

            streak = 0
            for row in rows:
                status = row["status"]
                if status in COUNTING_STATUSES:
                    streak += 1
                    continue
                if status in NEUTRAL_STATUSES:
                    continue
                break
            return streak
        finally:
            if owns_connection:
                await connection.close()

    async def apply_bonus_if_needed(
        self,
        *,
        guild_id: str,
        member_id: int,
        session_id: int,
        created_at: str,
        connection: aiosqlite.Connection,
    ) -> StreakBonusResult:
        """Award a 3-day or 7-day streak bonus when this session reaches it."""

        streak = await self.calculate_current_streak(
            guild_id=guild_id,
            member_id=member_id,
            connection=connection,
        )
        bonus = BONUSES.get(streak)
        if bonus is None:
            return StreakBonusResult(current_streak=streak)

        try:
            await self.score_repository.create_event(
                guild_id=guild_id,
                member_id=member_id,
                event_type="STREAK_BONUS",
                delta=bonus,
                reference_type="STREAK",
                reference_id=session_id,
                dedup_key=f"streak:{session_id}:{member_id}:{streak}",
                description=f"연속 출석 {streak}회 보너스",
                created_by_discord_id=None,
                created_at=created_at,
                connection=connection,
            )
        except aiosqlite.IntegrityError:
            return StreakBonusResult(current_streak=streak)

        return StreakBonusResult(
            current_streak=streak,
            bonus_delta=bonus,
            threshold=streak,
        )
