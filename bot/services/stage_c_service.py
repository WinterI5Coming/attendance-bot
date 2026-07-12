"""Stage C 시즌, 업적, 칭호, 간부 인사 비즈니스 규칙."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from bot.policies.rank_policy import get_rank
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.stage_c_repository import StageCRepository


@dataclass(frozen=True)
class SeasonRankingEntry:
    """시즌 랭킹 한 행에 표시할 멤버 통계."""

    rank_no: int
    discord_id: str
    display_name: str
    season_score: int
    attendance_rate: float
    on_time_rate: float
    present_count: int
    late_count: int
    absent_count: int
    personal_rank: str


@dataclass(frozen=True)
class SeasonRankingResult:
    """시즌 랭킹 조회 결과."""

    configured: bool
    season: dict[str, Any] | None = None
    entries: list[SeasonRankingEntry] | None = None


@dataclass(frozen=True)
class AchievementEvaluationResult:
    """업적 평가 실행 결과."""

    awarded_count: int
    role_grants: list[dict[str, str]]


@dataclass(frozen=True)
class OfficerCandidate:
    """간부 인사 미리보기에서 제안된 단일 대상자."""

    member_id: int
    discord_id: str
    display_name: str
    score: float
    action: str
    reason: str


@dataclass(frozen=True)
class OfficerReviewResult:
    """간부 인사 미리보기 생성 결과."""

    configured: bool
    review_id: int | None = None
    enabled: bool = False
    candidates: list[OfficerCandidate] | None = None
    summary: dict[str, Any] | None = None


class SeasonService:
    """시즌 생성, 상태 전환, 통계 스냅샷, 랭킹 조회를 담당한다."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        repository: StageCRepository,
    ) -> None:
        """서비스가 사용할 Repository 의존성을 저장한다."""

        self.guild_repository = guild_repository
        self.repository = repository

    async def create_season(
        self,
        *,
        guild_id: int | str,
        name: str,
        start_date: str,
        end_date: str,
        created_by_discord_id: int | str | None,
        now: datetime | None = None,
    ) -> int:
        """서버 설정을 스냅샷으로 저장하며 새 시즌을 생성한다."""

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            raise ValueError("Guild is not configured.")
        now_text = self._now_text(now)
        policy_snapshot = {
            "timezone": settings["timezone"],
            "attendance_days": settings["attendance_days"],
            "attendance_start": settings["attendance_start"],
            "late_deadline": settings["late_deadline"],
            "close_deadline": settings["close_deadline"],
            "excuse_mode": settings["excuse_mode"],
            "exempt_absence_counts_in_attendance_denominator": settings.get(
                "exempt_absence_counts_in_attendance_denominator",
                0,
            ),
        }
        return await self.repository.create_season(
            guild_id=guild_id_text,
            name=name,
            start_date=start_date,
            end_date=end_date,
            policy_snapshot_json=json.dumps(policy_snapshot, ensure_ascii=True),
            created_by_discord_id=(
                None if created_by_discord_id is None else str(created_by_discord_id)
            ),
            now=now_text,
        )

    async def list_seasons(self, *, guild_id: int | str) -> list[dict[str, Any]]:
        """서버의 시즌 목록을 조회한다."""

        return await self.repository.list_seasons(guild_id=str(guild_id))

    async def start_season(
        self,
        *,
        guild_id: int | str,
        season_id: int,
        now: datetime | None = None,
    ) -> bool:
        """예약된 시즌을 활성 상태로 시작한다."""

        return await self.repository.update_season_status(
            guild_id=str(guild_id),
            season_id=season_id,
            from_statuses=("SCHEDULED",),
            to_status="ACTIVE",
            now=self._now_text(now),
        )

    async def close_season(
        self,
        *,
        guild_id: int | str,
        season_id: int,
        now: datetime | None = None,
    ) -> bool:
        """시즌을 종료하고 통계 스냅샷을 확정한다."""

        now_text = self._now_text(now)
        updated = await self.repository.update_season_status(
            guild_id=str(guild_id),
            season_id=season_id,
            from_statuses=("ACTIVE", "SCHEDULED"),
            to_status="CLOSED",
            now=now_text,
        )
        if updated:
            await self.reconcile_season(
                guild_id=guild_id,
                season_id=season_id,
                finalized=True,
                now=now,
            )
        return updated

    async def cancel_season(
        self,
        *,
        guild_id: int | str,
        season_id: int,
        reason: str | None,
        now: datetime | None = None,
    ) -> bool:
        """예약 또는 활성 시즌을 취소 상태로 전환한다."""

        return await self.repository.update_season_status(
            guild_id=str(guild_id),
            season_id=season_id,
            from_statuses=("SCHEDULED", "ACTIVE"),
            to_status="CANCELLED",
            now=self._now_text(now),
            cancellation_reason=reason,
        )

    async def reconcile_season(
        self,
        *,
        guild_id: int | str,
        season_id: int,
        finalized: bool = False,
        now: datetime | None = None,
    ) -> int:
        """시즌 통계를 재계산하고 스냅샷 테이블을 갱신한다."""

        raw_rows = await self.repository.calculate_season_stats(
            guild_id=str(guild_id),
            season_id=season_id,
        )
        stats = [self._normalize_stat_row(row) for row in raw_rows]
        await self.repository.replace_season_stats(
            season_id=season_id,
            stats=stats,
            finalized=finalized,
            now=self._now_text(now),
        )
        return len(stats)

    async def get_ranking(
        self,
        *,
        guild_id: int | str,
        season_id: int | None = None,
        limit: int = 10,
    ) -> SeasonRankingResult:
        """활성 시즌 또는 지정 시즌의 랭킹을 조회한다."""

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return SeasonRankingResult(configured=False)

        season = (
            await self.repository.get_active_season(guild_id=guild_id_text)
            if season_id is None
            else await self.repository.get_season(
                guild_id=guild_id_text,
                season_id=season_id,
            )
        )
        if season is None:
            return SeasonRankingResult(configured=True, season=None, entries=[])

        if season["stats_dirty"]:
            await self.reconcile_season(
                guild_id=guild_id_text,
                season_id=int(season["id"]),
            )
            season = await self.repository.get_season(
                guild_id=guild_id_text,
                season_id=int(season["id"]),
            )

        rows = await self.repository.list_season_stats(
            season_id=int(season["id"]),
            limit=limit,
        )
        entries = [
            SeasonRankingEntry(
                rank_no=index,
                discord_id=row["discord_id"],
                display_name=row["display_name"],
                season_score=int(row["season_score"] or 0),
                attendance_rate=float(row["attendance_rate"] or 0),
                on_time_rate=float(row["on_time_rate"] or 0),
                present_count=int(row["present_count"] or 0),
                late_count=int(row["late_count"] or 0),
                absent_count=int(row["absent_count"] or 0),
                personal_rank=row["final_personal_rank"] or get_rank(
                    int(row["season_score"] or 0)
                ),
            )
            for index, row in enumerate(rows, start=1)
        ]
        return SeasonRankingResult(configured=True, season=season, entries=entries)

    def _normalize_stat_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """SQL 집계 결과를 시즌 스냅샷 저장 형식으로 보정한다."""

        denominator = int(row["attendance_denominator"] or 0)
        present = int(row["present_count"] or 0)
        late = int(row["late_count"] or 0)
        excused_late = int(row["excused_late_count"] or 0)
        success = present + late + excused_late
        attendance_rate = 0.0 if denominator == 0 else round(success / denominator * 100, 1)
        on_time_rate = 0.0 if denominator == 0 else round(present / denominator * 100, 1)
        season_score = int(row["season_score"] or 0)
        best_streak = success
        current_streak = success
        officer_score = round(
            attendance_rate * 0.45
            + on_time_rate * 0.30
            + min(max(season_score, 0), 100) * 0.15
            + min(int(row["voice_verified_count"] or 0) * 2, 10),
            1,
        )
        return {
            **row,
            "target_session_count": int(row["target_session_count"] or 0),
            "attendance_denominator": denominator,
            "present_count": present,
            "late_count": late,
            "absent_count": int(row["absent_count"] or 0),
            "exempt_absent_count": int(row["exempt_absent_count"] or 0),
            "attendance_rate": attendance_rate,
            "on_time_rate": on_time_rate,
            "voice_seconds": int(row["voice_seconds"] or 0),
            "voice_verified_count": int(row["voice_verified_count"] or 0),
            "voice_failed_count": int(row["voice_failed_count"] or 0),
            "current_streak": current_streak,
            "best_streak": best_streak,
            "season_score": season_score,
            "final_personal_rank": get_rank(season_score),
            "officer_evaluation_score": officer_score,
        }

    @staticmethod
    def _now_text(now: datetime | None) -> str:
        """timezone-aware datetime을 UTC ISO 문자열로 변환한다."""

        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        return now.astimezone(timezone.utc).isoformat()


class AchievementService:
    """업적 평가, 업적 조회, 칭호 조회와 장착을 담당한다."""

    DEFAULT_DEFINITIONS = [
        {
            "code": "FIRST_PRESENT",
            "name": "First Attendance",
            "description": "Awarded after the first effective attendance.",
            "condition_type": "FIRST_PRESENT",
            "threshold": 1,
            "reward_score": 1,
            "title_name": "First Check-in",
            "once_per_season": False,
        },
        {
            "code": "STREAK_3",
            "name": "Three Streak",
            "description": "Awarded for a 3-session success streak in a season.",
            "condition_type": "STREAK",
            "threshold": 3,
            "reward_score": 2,
            "title_name": "Steady",
            "once_per_season": True,
        },
        {
            "code": "ON_TIME_10",
            "name": "On-time 10",
            "description": "Awarded for 10 on-time attendances in a season.",
            "condition_type": "ON_TIME_COUNT",
            "threshold": 10,
            "reward_score": 3,
            "title_name": "Punctual",
            "once_per_season": True,
        },
        {
            "code": "VOICE_50_HOURS",
            "name": "Voice 50 Hours",
            "description": "Awarded for 50 verified voice hours in a season.",
            "condition_type": "VOICE_HOURS",
            "threshold": 50,
            "reward_score": 5,
            "title_name": "Voice Anchor",
            "once_per_season": True,
        },
        {
            "code": "PERFECT_SEASON",
            "name": "Perfect Season",
            "description": "Awarded for 100% attendance and on-time rate in a season.",
            "condition_type": "PERFECT_SEASON",
            "threshold": 1,
            "reward_score": 10,
            "title_name": "Perfect",
            "once_per_season": True,
        },
    ]

    def __init__(
        self,
        *,
        member_repository: MemberRepository,
        repository: StageCRepository,
        season_service: SeasonService,
    ) -> None:
        """서비스가 사용할 Repository와 시즌 서비스를 저장한다."""

        self.member_repository = member_repository
        self.repository = repository
        self.season_service = season_service

    async def ensure_defaults(
        self,
        *,
        guild_id: int | str,
        now: datetime | None = None,
    ) -> None:
        """서버의 기본 업적 정의를 생성하거나 최신 값으로 갱신한다."""

        await self.repository.upsert_default_achievements(
            guild_id=str(guild_id),
            definitions=self.DEFAULT_DEFINITIONS,
            now=SeasonService._now_text(now),
        )

    async def evaluate_season(
        self,
        *,
        guild_id: int | str,
        season_id: int,
        created_by_discord_id: int | str | None = None,
        now: datetime | None = None,
    ) -> AchievementEvaluationResult:
        """시즌 통계를 기준으로 업적과 보상을 중복 없이 지급한다."""

        guild_id_text = str(guild_id)
        now_text = SeasonService._now_text(now)
        await self.ensure_defaults(guild_id=guild_id_text, now=now)
        await self.season_service.reconcile_season(
            guild_id=guild_id_text,
            season_id=season_id,
            now=now,
        )
        definitions = await self.repository.list_achievement_definitions(
            guild_id=guild_id_text,
        )
        mappings = {
            row["code"]: str(row["role_id"])
            for row in await self.repository.list_achievement_role_mappings(
                guild_id=guild_id_text,
            )
        }
        stats = await self.repository.list_season_stats(
            season_id=season_id,
            limit=1000,
        )
        awarded = 0
        role_grants: list[dict[str, str]] = []
        for row in stats:
            for definition in definitions:
                if not self._is_earned(definition, row):
                    continue
                season_scope = season_id if definition["once_per_season"] else None
                created = await self.repository.award_achievement(
                    guild_id=guild_id_text,
                    member_id=int(row["member_id"]),
                    definition=definition,
                    season_id=season_scope,
                    created_by_discord_id=(
                        None
                        if created_by_discord_id is None
                        else str(created_by_discord_id)
                    ),
                    now=now_text,
                )
                if created:
                    awarded += 1
                    role_id = mappings.get(definition["code"])
                    if role_id is not None:
                        role_grants.append(
                            {
                                "discord_id": str(row["discord_id"]),
                                "role_id": role_id,
                                "achievement_code": definition["code"],
                            }
                        )
        return AchievementEvaluationResult(
            awarded_count=awarded,
            role_grants=role_grants,
        )

    async def list_definitions(
        self,
        *,
        guild_id: int | str,
    ) -> list[dict[str, Any]]:
        """서버의 활성 업적 정의 목록을 조회한다."""

        return await self.repository.list_achievement_definitions(guild_id=str(guild_id))

    async def list_member_achievements(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
    ) -> list[dict[str, Any]]:
        """Discord 사용자 ID로 멤버를 찾아 획득 업적 목록을 조회한다."""

        member = await self.member_repository.get_by_discord_id(
            guild_id=str(guild_id),
            discord_id=str(discord_id),
        )
        if member is None:
            return []
        return await self.repository.list_member_achievements(
            guild_id=str(guild_id),
            member_id=int(member["id"]),
        )

    async def list_member_titles(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
    ) -> list[dict[str, Any]]:
        """Discord 사용자 ID로 멤버를 찾아 보유 칭호 목록을 조회한다."""

        member = await self.member_repository.get_by_discord_id(
            guild_id=str(guild_id),
            discord_id=str(discord_id),
        )
        if member is None:
            return []
        return await self.repository.list_member_titles(
            guild_id=str(guild_id),
            member_id=int(member["id"]),
        )

    async def set_role_mapping(
        self,
        *,
        guild_id: int | str,
        achievement_code: str,
        role_id: int | str,
        now: datetime | None = None,
    ) -> bool:
        """업적 코드와 Discord 역할 보상 매핑을 저장한다."""

        await self.ensure_defaults(guild_id=guild_id, now=now)
        return await self.repository.set_achievement_role_mapping(
            guild_id=str(guild_id),
            achievement_code=achievement_code,
            role_id=str(role_id),
            now=SeasonService._now_text(now),
        )

    async def list_role_mappings(
        self,
        *,
        guild_id: int | str,
    ) -> list[dict[str, Any]]:
        """서버의 업적-역할 매핑 목록을 조회한다."""

        return await self.repository.list_achievement_role_mappings(
            guild_id=str(guild_id),
        )

    async def equip_title(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
        title_name: str,
        now: datetime | None = None,
    ) -> bool:
        """사용자가 보유한 칭호를 대표 칭호로 장착한다."""

        member = await self.member_repository.get_by_discord_id(
            guild_id=str(guild_id),
            discord_id=str(discord_id),
        )
        if member is None:
            return False
        return await self.repository.equip_title(
            guild_id=str(guild_id),
            member_id=int(member["id"]),
            title_name=title_name,
            now=SeasonService._now_text(now),
        )

    async def unequip_title(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
        now: datetime | None = None,
    ) -> None:
        """사용자의 대표 칭호 장착 상태를 해제한다."""

        member = await self.member_repository.get_by_discord_id(
            guild_id=str(guild_id),
            discord_id=str(discord_id),
        )
        if member is None:
            return
        await self.repository.unequip_title(
            guild_id=str(guild_id),
            member_id=int(member["id"]),
            now=SeasonService._now_text(now),
        )

    def _is_earned(self, definition: dict[str, Any], row: dict[str, Any]) -> bool:
        """업적 정의와 시즌 통계를 비교해 달성 여부를 판정한다."""

        threshold = int(definition["threshold"])
        condition = definition["condition_type"]
        if condition == "FIRST_PRESENT":
            return int(row["present_count"] or 0) >= 1
        if condition == "ATTENDANCE_COUNT":
            return int(row["present_count"] or 0) + int(row["late_count"] or 0) >= threshold
        if condition == "STREAK":
            return int(row["best_streak"] or 0) >= threshold
        if condition == "ON_TIME_COUNT":
            return int(row["present_count"] or 0) >= threshold
        if condition == "VOICE_HOURS":
            return int(row["voice_seconds"] or 0) >= threshold * 3600
        if condition == "PERFECT_SEASON":
            return (
                int(row["attendance_denominator"] or 0) > 0
                and float(row["attendance_rate"] or 0) >= 100
                and float(row["on_time_rate"] or 0) >= 100
            )
        return False


class OfficerReviewService:
    """간부 인사 미리보기 생성과 역할 변경 감사 로그를 담당한다."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        repository: StageCRepository,
        season_service: SeasonService,
    ) -> None:
        """서비스가 사용할 Repository와 시즌 서비스를 저장한다."""

        self.guild_repository = guild_repository
        self.repository = repository
        self.season_service = season_service

    async def get_settings(
        self,
        *,
        guild_id: int | str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """서버의 간부 평가 설정을 조회한다."""

        return await self.repository.get_officer_settings(
            guild_id=str(guild_id),
            now=SeasonService._now_text(now),
        )

    async def update_settings(
        self,
        *,
        guild_id: int | str,
        values: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """허용된 간부 평가 설정 항목만 갱신한다."""

        allowed = {
            "enabled",
            "evaluation_window_days",
            "minimum_sessions",
            "promotion_threshold",
            "retention_threshold",
            "replacement_score_gap",
            "officer_capacity",
            "promotion_cooldown_days",
            "demotion_cooldown_days",
            "member_role_id",
            "officer_role_id",
            "auto_review_enabled",
            "auto_apply_roles_enabled",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown officer setting: {sorted(unknown)[0]}")
        return await self.repository.update_officer_settings(
            guild_id=str(guild_id),
            values=values,
            now=SeasonService._now_text(now),
        )

    async def create_preview(
        self,
        *,
        guild_id: int | str,
        season_id: int | None,
        current_officer_discord_ids: set[str],
        protected_discord_ids: set[str],
        created_by_discord_id: int | str | None,
        now: datetime | None = None,
    ) -> OfficerReviewResult:
        """시즌 통계와 현재 역할 상태를 비교해 인사 미리보기를 생성한다."""

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return OfficerReviewResult(configured=False)
        officer_settings = await self.get_settings(guild_id=guild_id_text, now=now)
        if not officer_settings["enabled"]:
            return OfficerReviewResult(configured=True, enabled=False, candidates=[])

        season = (
            await self.repository.get_active_season(guild_id=guild_id_text)
            if season_id is None
            else await self.repository.get_season(
                guild_id=guild_id_text,
                season_id=season_id,
            )
        )
        if season is None:
            return OfficerReviewResult(configured=True, enabled=True, candidates=[])

        await self.season_service.reconcile_season(
            guild_id=guild_id_text,
            season_id=int(season["id"]),
            now=now,
        )
        stats = await self.repository.list_season_stats(
            season_id=int(season["id"]),
            limit=1000,
        )
        candidates = self._build_candidates(
            stats=stats,
            officer_settings=officer_settings,
            current_officer_discord_ids=current_officer_discord_ids,
            protected_discord_ids=protected_discord_ids,
        )
        summary = {
            "season_id": season["id"],
            "season_name": season["name"],
            "current_officer_count": len(current_officer_discord_ids),
            "candidate_count": len(candidates),
            "protected_count": len(protected_discord_ids),
            "auto_apply_roles_enabled": bool(officer_settings["auto_apply_roles_enabled"]),
        }
        result_payload = [candidate.__dict__ for candidate in candidates]
        digest = hashlib.sha256(
            json.dumps(
                {
                    "guild_id": guild_id_text,
                    "season_id": season["id"],
                    "settings": dict(officer_settings),
                    "officers": sorted(current_officer_discord_ids),
                    "protected": sorted(protected_discord_ids),
                    "result": result_payload,
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        review_id = await self.repository.create_officer_review(
            guild_id=guild_id_text,
            season_id=int(season["id"]),
            input_digest=digest,
            created_by_discord_id=(
                None if created_by_discord_id is None else str(created_by_discord_id)
            ),
            summary_json=json.dumps(summary, ensure_ascii=True),
            result_json=json.dumps(result_payload, ensure_ascii=True),
            now=SeasonService._now_text(now),
        )
        return OfficerReviewResult(
            configured=True,
            review_id=review_id,
            enabled=True,
            candidates=candidates,
            summary=summary,
        )

    async def mark_review_executed(
        self,
        *,
        guild_id: int | str,
        review_id: int,
        status: str,
        executed_by_discord_id: int | str,
        now: datetime | None = None,
    ) -> None:
        """간부 인사 리뷰 실행 결과 상태를 저장한다."""

        await self.repository.mark_officer_review_executed(
            guild_id=str(guild_id),
            review_id=review_id,
            status=status,
            executed_by_discord_id=str(executed_by_discord_id),
            now=SeasonService._now_text(now),
        )

    async def get_review(
        self,
        *,
        guild_id: int | str,
        review_id: int,
    ) -> dict[str, Any] | None:
        """저장된 간부 인사 리뷰를 조회한다."""

        return await self.repository.get_officer_review(
            guild_id=str(guild_id),
            review_id=review_id,
        )

    async def log_role_change(
        self,
        *,
        guild_id: int | str,
        review_id: int | None,
        member_id: int | None,
        discord_id: int | str,
        action_type: str,
        from_role_id: int | str | None,
        to_role_id: int | str | None,
        status: str,
        reason: str,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Discord 역할 변경 시도 결과를 감사 로그로 저장한다."""

        await self.repository.create_role_change_log(
            guild_id=str(guild_id),
            review_id=review_id,
            member_id=member_id,
            discord_id=str(discord_id),
            action_type=action_type,
            from_role_id=None if from_role_id is None else str(from_role_id),
            to_role_id=None if to_role_id is None else str(to_role_id),
            status=status,
            reason=reason,
            error_message=error_message,
            now=SeasonService._now_text(now),
        )

    async def list_role_change_logs(
        self,
        *,
        guild_id: int | str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """최근 간부 역할 변경 이력을 조회한다."""

        return await self.repository.list_role_change_logs(
            guild_id=str(guild_id),
            limit=limit,
        )

    def _build_candidates(
        self,
        *,
        stats: list[dict[str, Any]],
        officer_settings: dict[str, Any],
        current_officer_discord_ids: set[str],
        protected_discord_ids: set[str],
    ) -> list[OfficerCandidate]:
        """평가 점수와 정원 기준으로 승격/강등 후보를 계산한다."""

        minimum_sessions = int(officer_settings["minimum_sessions"])
        promotion_threshold = float(officer_settings["promotion_threshold"])
        retention_threshold = float(officer_settings["retention_threshold"])
        capacity = int(officer_settings["officer_capacity"])

        rows = [
            row
            for row in stats
            if int(row["attendance_denominator"] or 0) >= minimum_sessions
        ]
        rows.sort(
            key=lambda row: (
                -float(row["officer_evaluation_score"] or 0),
                -int(row["season_score"] or 0),
                row["display_name"].casefold(),
            )
        )

        keep_officer_ids = set(current_officer_discord_ids)
        candidates: list[OfficerCandidate] = []
        for row in rows:
            discord_id = str(row["discord_id"])
            if discord_id in protected_discord_ids:
                candidates.append(
                    self._candidate_from_row(row, "KEEP_OFFICER", "Protected member.")
                )
                continue
            score = float(row["officer_evaluation_score"] or 0)
            is_officer = discord_id in current_officer_discord_ids
            if is_officer and score < retention_threshold:
                keep_officer_ids.discard(discord_id)
                candidates.append(
                    self._candidate_from_row(
                        row,
                        "DEMOTE",
                        f"Officer score {score:.1f} is below retention threshold.",
                    )
                )

        open_slots = max(capacity - len(keep_officer_ids), 0)
        for row in rows:
            if open_slots <= 0:
                break
            discord_id = str(row["discord_id"])
            if (
                discord_id in current_officer_discord_ids
                or discord_id in protected_discord_ids
            ):
                continue
            score = float(row["officer_evaluation_score"] or 0)
            if score < promotion_threshold:
                continue
            candidates.append(
                self._candidate_from_row(
                    row,
                    "PROMOTE",
                    f"Member score {score:.1f} meets promotion threshold.",
                )
            )
            open_slots -= 1

        return candidates

    def _candidate_from_row(
        self,
        row: dict[str, Any],
        action: str,
        reason: str,
    ) -> OfficerCandidate:
        """시즌 통계 행을 간부 후보 dataclass로 변환한다."""

        return OfficerCandidate(
            member_id=int(row["member_id"]),
            discord_id=str(row["discord_id"]),
            display_name=row["display_name"],
            score=float(row["officer_evaluation_score"] or 0),
            action=action,
            reason=reason,
        )
