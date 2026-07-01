"""Business rules for personal attendance reports."""

from dataclasses import dataclass
from typing import Any

from bot.policies.rank_policy import get_rank
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.report_repository import ReportRepository
from bot.repositories.score_repository import ScoreRepository
from bot.services.streak_service import StreakService


@dataclass(frozen=True)
class PersonalReportResult:
    """Computed report for one active member."""

    found: bool
    display_name: str | None = None
    total_score: int = 0
    rank: str | None = None
    total_sessions: int = 0
    present_count: int = 0
    late_count: int = 0
    absent_count: int = 0
    excused_late_count: int = 0
    excused_absent_count: int = 0
    attendance_rate: float = 0.0
    current_streak: int = 0
    recent_events: list[dict[str, Any]] | None = None
    timezone_name: str | None = None


@dataclass(frozen=True)
class RankingEntry:
    """One row in the guild ranking."""

    rank_no: int
    discord_id: str
    display_name: str
    total_score: int
    rank: str
    current_streak: int


@dataclass(frozen=True)
class RankingResult:
    """Ranking result for active guild members."""

    configured: bool
    entries: list[RankingEntry] | None = None


class ReportService:
    """Build personal attendance reports from repositories and policies."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        member_repository: MemberRepository,
        report_repository: ReportRepository,
        score_repository: ScoreRepository,
        streak_service: StreakService | None = None,
    ) -> None:
        """Create the service."""

        self.guild_repository = guild_repository
        self.member_repository = member_repository
        self.report_repository = report_repository
        self.score_repository = score_repository
        self.streak_service = streak_service

    async def get_my_report(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
    ) -> PersonalReportResult:
        """Return a personal report for the currently active member.

        Args:
            guild_id: Discord guild ID.
            discord_id: Discord user ID.

        Returns:
            Personal report. ``found`` is False for unregistered/inactive users.
        """

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=str(discord_id),
        )
        if member is None or not member["is_active"]:
            return PersonalReportResult(found=False)

        member_id = int(member["id"])
        summary = await self.report_repository.get_attendance_summary(
            member_id=member_id,
        )
        total_score = await self.score_repository.get_total_score(member_id=member_id)
        current_streak = 0
        if self.streak_service is not None:
            current_streak = await self.streak_service.calculate_current_streak(
                guild_id=guild_id_text,
                member_id=member_id,
            )
        success_count = (
            summary["present"]
            + summary["late"]
            + summary["excused_late"]
        )
        attendance_rate = (
            0.0
            if summary["total_sessions"] == 0
            else round(success_count / summary["total_sessions"] * 100, 1)
        )
        recent_events = await self.score_repository.list_recent_events(
            member_id=member_id,
            limit=5,
        )

        return PersonalReportResult(
            found=True,
            display_name=member["display_name"],
            total_score=total_score,
            rank=get_rank(total_score),
            total_sessions=summary["total_sessions"],
            present_count=summary["present"],
            late_count=summary["late"],
            absent_count=summary["absent"],
            excused_late_count=summary["excused_late"],
            excused_absent_count=summary["excused_absent"],
            attendance_rate=attendance_rate,
            current_streak=current_streak,
            recent_events=recent_events,
            timezone_name=None if settings is None else settings["timezone"],
        )

    async def get_ranking(self, *, guild_id: int | str, limit: int = 10) -> RankingResult:
        """Return active member ranking for a guild."""

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return RankingResult(configured=False)

        members = await self.member_repository.list_active_with_ids(guild_id=guild_id_text)
        rows = []
        for member in members:
            member_id = int(member["id"])
            total_score = await self.score_repository.get_total_score(
                member_id=member_id
            )
            current_streak = 0
            if self.streak_service is not None:
                current_streak = await self.streak_service.calculate_current_streak(
                    guild_id=guild_id_text,
                    member_id=member_id,
                )
            rows.append(
                {
                    "discord_id": member["discord_id"],
                    "display_name": member["display_name"],
                    "total_score": total_score,
                    "current_streak": current_streak,
                }
            )

        rows.sort(
            key=lambda row: (
                -row["total_score"],
                -row["current_streak"],
                row["display_name"].casefold(),
            )
        )
        entries = [
            RankingEntry(
                rank_no=index,
                discord_id=row["discord_id"],
                display_name=row["display_name"],
                total_score=row["total_score"],
                rank=get_rank(row["total_score"]),
                current_streak=row["current_streak"],
            )
            for index, row in enumerate(rows[:limit], start=1)
        ]
        return RankingResult(configured=True, entries=entries)
