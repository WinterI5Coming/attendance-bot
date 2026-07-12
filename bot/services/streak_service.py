"""연속 출석 계산과 보너스 지급 서비스."""

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
    """연속 출석 보너스 적용 결과."""

    current_streak: int
    bonus_delta: int = 0
    threshold: int | None = None


class StreakService:
    """현재 연속 출석 수를 계산하고 milestone 보너스를 지급한다."""

    def __init__(self, *, score_repository: ScoreRepository) -> None:
        """연속 출석 보너스 점수 저장에 사용할 Repository를 저장한다."""

        self.score_repository = score_repository

    async def calculate_current_streak(
        self,
        *,
        guild_id: str,
        member_id: int,
        connection: aiosqlite.Connection | None = None,
    ) -> int:
        """대원의 현재 연속 출석 수를 계산한다."""

        owns_connection = connection is None
        if connection is None:
            connection = await self.score_repository.database.connect()

        try:
            cursor = await connection.execute(
                """
                SELECT
                    CASE
                        WHEN abs_adj.id IS NOT NULL THEN 'EXEMPT_ABSENT'
                        WHEN late_adj.resulting_status IS NOT NULL THEN late_adj.resulting_status
                        ELSE ar.status
                    END AS status
                FROM attendance_session_members AS asm
                JOIN attendance_sessions AS s ON s.id = asm.session_id
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
                if status in NEUTRAL_STATUSES or status == "EXEMPT_ABSENT":
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
        """이번 세션으로 3일 또는 7일 연속 출석을 달성하면 보너스를 지급한다."""

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
