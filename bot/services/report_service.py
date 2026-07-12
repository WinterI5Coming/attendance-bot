"""개인 출석 리포트 비즈니스 규칙을 담당한다."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bot.policies.rank_policy import get_rank
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.report_repository import ReportRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.evaluation_repository import EvaluationRepository
from bot.services.streak_service import StreakService


@dataclass(frozen=True)
class PersonalReportResult:
    """활성 멤버 한 명에 대해 계산된 리포트."""

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
    recent_evaluations: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class RankingEntry:
    """서버 랭킹의 한 행."""

    rank_no: int
    discord_id: str
    display_name: str
    total_score: int
    rank: str
    current_streak: int


@dataclass(frozen=True)
class RankingResult:
    """서버 활성 멤버 랭킹 결과."""

    configured: bool
    entries: list[RankingEntry] | None = None


@dataclass(frozen=True)
class PublicReportResult:
    """대상 멤버의 공개 가능한 리포트."""

    found: bool
    target_discord_id: str | None = None
    display_name: str | None = None
    total_score: int = 0
    rank: str | None = None
    total_sessions: int = 0
    attendance_rate: float = 0.0
    current_streak: int = 0
    present_count: int = 0
    late_count: int = 0
    absent_count: int = 0
    excused_late_count: int = 0
    excused_absent_count: int = 0
    recent_events: list[dict[str, Any]] | None = None
    recent_evaluations: list[dict[str, Any]] | None = None
    timezone_name: str | None = None


@dataclass(frozen=True)
class WeeklyMemberRow:
    """주간 리포트의 멤버별 한 행."""

    discord_id: str
    display_name: str
    total_sessions: int
    attendance_rate: float
    weekly_score: int
    present_count: int
    late_count: int
    absent_count: int
    excused_late_count: int
    excused_absent_count: int


@dataclass(frozen=True)
class WeeklyReportResult:
    """서버 주간 출석과 점수 요약."""

    configured: bool
    start_at: str | None = None
    end_at: str | None = None
    total_targets: int = 0
    attendance_rate: float = 0.0
    present_count: int = 0
    late_count: int = 0
    absent_count: int = 0
    excused_late_count: int = 0
    excused_absent_count: int = 0
    top_member: WeeklyMemberRow | None = None
    member_rows: list[WeeklyMemberRow] | None = None
    timezone_name: str | None = None


class ReportService:
    """저장소와 정책을 조합해 출석 리포트를 만든다."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        member_repository: MemberRepository,
        report_repository: ReportRepository,
        score_repository: ScoreRepository,
        streak_service: StreakService | None = None,
        evaluation_repository: EvaluationRepository | None = None,
    ) -> None:
        """서비스 의존성을 초기화한다."""

        self.guild_repository = guild_repository
        self.member_repository = member_repository
        self.report_repository = report_repository
        self.score_repository = score_repository
        self.streak_service = streak_service
        self.evaluation_repository = evaluation_repository

    async def get_my_report(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
    ) -> PersonalReportResult:
        """현재 활성 멤버의 개인 리포트를 반환한다.

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
        recent_evaluations = []
        if self.evaluation_repository is not None:
            recent_evaluations = await self.evaluation_repository.list_recent_active_for_member(
                member_id=member_id,
                limit=3,
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
            recent_evaluations=recent_evaluations,
            timezone_name=None if settings is None else settings["timezone"],
        )

    async def get_ranking(self, *, guild_id: int | str, limit: int = 10) -> RankingResult:
        """서버의 활성 멤버 랭킹을 반환한다."""

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

    async def get_public_report(
        self,
        *,
        guild_id: int | str,
        target_discord_id: int | str,
    ) -> PublicReportResult:
        """이력이 있는 멤버의 공개 가능한 리포트를 반환한다."""

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=str(target_discord_id),
        )
        if member is None:
            return PublicReportResult(found=False)

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
        recent_evaluations = []
        if self.evaluation_repository is not None:
            recent_evaluations = await self.evaluation_repository.list_recent_active_for_member(
                member_id=member_id,
                limit=3,
            )
        return PublicReportResult(
            found=True,
            target_discord_id=str(target_discord_id),
            display_name=member["display_name"],
            total_score=total_score,
            rank=get_rank(total_score),
            total_sessions=summary["total_sessions"],
            attendance_rate=attendance_rate,
            current_streak=current_streak,
            present_count=summary["present"],
            late_count=summary["late"],
            absent_count=summary["absent"],
            excused_late_count=summary["excused_late"],
            excused_absent_count=summary["excused_absent"],
            recent_events=await self.score_repository.list_recent_events(
                member_id=member_id,
                limit=5,
            ),
            recent_evaluations=recent_evaluations,
            timezone_name=None if settings is None else settings["timezone"],
        )

    async def get_weekly_report(
        self,
        *,
        guild_id: int | str,
        now: datetime,
        previous_week: bool = False,
    ) -> WeeklyReportResult:
        """현재 또는 이전 서버 로컬 월요일-일요일 리포트를 반환한다."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return WeeklyReportResult(configured=False)

        local_timezone = ZoneInfo(settings["timezone"])
        local_today = now.astimezone(local_timezone).date()
        monday = local_today - timedelta(days=local_today.weekday())
        if previous_week:
            monday -= timedelta(days=7)
        next_monday = monday + timedelta(days=7)
        start_at = datetime.combine(monday, time.min, tzinfo=local_timezone).astimezone(
            timezone.utc
        )
        end_at = datetime.combine(
            next_monday,
            time.min,
            tzinfo=local_timezone,
        ).astimezone(timezone.utc)

        summary = await self.report_repository.get_weekly_summary(
            guild_id=guild_id_text,
            start_at=start_at.isoformat(),
            end_at=end_at.isoformat(),
        )
        rows = await self.report_repository.get_weekly_member_rows(
            guild_id=guild_id_text,
            start_at=start_at.isoformat(),
            end_at=end_at.isoformat(),
        )
        member_rows = [
            self._build_weekly_member_row(row)
            for row in rows
        ]
        success_count = (
            summary["present"]
            + summary["late"]
            + summary["excused_late"]
        )
        attendance_rate = (
            0.0
            if summary["total_targets"] == 0
            else round(success_count / summary["total_targets"] * 100, 1)
        )
        return WeeklyReportResult(
            configured=True,
            start_at=start_at.isoformat(),
            end_at=end_at.isoformat(),
            total_targets=summary["total_targets"],
            attendance_rate=attendance_rate,
            present_count=summary["present"],
            late_count=summary["late"],
            absent_count=summary["absent"],
            excused_late_count=summary["excused_late"],
            excused_absent_count=summary["excused_absent"],
            top_member=member_rows[0] if member_rows else None,
            member_rows=member_rows,
            timezone_name=settings["timezone"],
        )

    def _build_weekly_member_row(self, row: dict[str, Any]) -> WeeklyMemberRow:
        """Repository의 주간 집계 행을 리포트 dataclass로 변환한다."""

        total_sessions = int(row["total_sessions"] or 0)
        success_count = int(row["present"] or 0) + int(row["late"] or 0) + int(
            row["excused_late"] or 0
        )
        rate = 0.0 if total_sessions == 0 else round(success_count / total_sessions * 100, 1)
        return WeeklyMemberRow(
            discord_id=row["discord_id"],
            display_name=row["display_name"],
            total_sessions=total_sessions,
            attendance_rate=rate,
            weekly_score=int(row["weekly_score"] or 0),
            present_count=int(row["present"] or 0),
            late_count=int(row["late"] or 0),
            absent_count=int(row["absent"] or 0),
            excused_late_count=int(row["excused_late"] or 0),
            excused_absent_count=int(row["excused_absent"] or 0),
        )
