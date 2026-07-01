"""Business rules for officer evaluations and manual score adjustments."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
import uuid

from bot.policies.rank_policy import get_rank
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.evaluation_repository import EvaluationRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.score_repository import ScoreRepository


EVALUATION_MIN_SCORE = -5
EVALUATION_MAX_SCORE = 5
MANUAL_MIN_SCORE = -1000
MANUAL_MAX_SCORE = 1000
MIN_REASON_LENGTH = 2
MAX_REASON_LENGTH = 500


class EvaluationStatus(Enum):
    """Expected outcomes for evaluation operations."""

    CREATED = "CREATED"
    CANCELLED = "CANCELLED"
    NOT_FOUND = "NOT_FOUND"
    ALREADY_CANCELLED = "ALREADY_CANCELLED"
    INVALID_SCORE = "INVALID_SCORE"
    INVALID_REASON = "INVALID_REASON"
    TARGET_NOT_ACTIVE = "TARGET_NOT_ACTIVE"
    SELF_EVALUATION_NOT_ALLOWED = "SELF_EVALUATION_NOT_ALLOWED"
    PERMISSION_DENIED = "PERMISSION_DENIED"


class ManualScoreStatus(Enum):
    """Expected outcomes for manual score adjustment."""

    ADJUSTED = "ADJUSTED"
    INVALID_SCORE = "INVALID_SCORE"
    INVALID_REASON = "INVALID_REASON"
    TARGET_NOT_ACTIVE = "TARGET_NOT_ACTIVE"
    PERMISSION_DENIED = "PERMISSION_DENIED"


@dataclass(frozen=True)
class EvaluationResult:
    """Result of creating or cancelling an evaluation."""

    status: EvaluationStatus
    evaluation_id: int | None = None
    target_discord_id: str | None = None
    score: int = 0
    reversal_delta: int = 0
    previous_total: int = 0
    total_score: int = 0
    previous_rank: str | None = None
    current_rank: str | None = None
    rank_changed: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class ManualScoreResult:
    """Result of a manual score adjustment."""

    status: ManualScoreStatus
    target_discord_id: str | None = None
    delta: int = 0
    previous_total: int = 0
    total_score: int = 0
    previous_rank: str | None = None
    current_rank: str | None = None
    rank_changed: bool = False
    reason: str | None = None


class EvaluationService:
    """Create evaluations, cancel evaluations, and adjust score manually."""

    def __init__(
        self,
        *,
        member_repository: MemberRepository,
        score_repository: ScoreRepository,
        evaluation_repository: EvaluationRepository,
        audit_repository: AuditRepository,
    ) -> None:
        """Create the service."""

        self.member_repository = member_repository
        self.score_repository = score_repository
        self.evaluation_repository = evaluation_repository
        self.audit_repository = audit_repository

    async def create_evaluation(
        self,
        *,
        guild_id: int | str,
        target_discord_id: int | str,
        evaluator_discord_id: int | str,
        score: int,
        reason: str,
        has_permission: bool,
        now: datetime,
    ) -> EvaluationResult:
        """Create an officer evaluation and its score event atomically."""

        self._require_aware(now)
        if not has_permission:
            return EvaluationResult(status=EvaluationStatus.PERMISSION_DENIED)
        if score == 0 or score < EVALUATION_MIN_SCORE or score > EVALUATION_MAX_SCORE:
            return EvaluationResult(status=EvaluationStatus.INVALID_SCORE)

        cleaned_reason = reason.strip()
        if not self._valid_reason(cleaned_reason):
            return EvaluationResult(status=EvaluationStatus.INVALID_REASON)

        guild_id_text = str(guild_id)
        target_id_text = str(target_discord_id)
        evaluator_id_text = str(evaluator_discord_id)
        if target_id_text == evaluator_id_text:
            return EvaluationResult(status=EvaluationStatus.SELF_EVALUATION_NOT_ALLOWED)

        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=target_id_text,
        )
        if member is None or not member["is_active"]:
            return EvaluationResult(status=EvaluationStatus.TARGET_NOT_ACTIVE)

        now_text = now.isoformat()
        member_id = int(member["id"])
        connection = await self.score_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            previous_total = await self.score_repository.get_total_score(
                member_id=member_id,
                connection=connection,
            )
            previous_rank = get_rank(previous_total)

            evaluation_id = await self.evaluation_repository.create(
                guild_id=guild_id_text,
                member_id=member_id,
                evaluator_discord_id=evaluator_id_text,
                score=score,
                reason=cleaned_reason,
                created_at=now_text,
                connection=connection,
            )
            score_event_id = await self.score_repository.create_event(
                guild_id=guild_id_text,
                member_id=member_id,
                event_type="EVALUATION",
                delta=score,
                reference_type="EVALUATION",
                reference_id=evaluation_id,
                dedup_key=f"evaluation:{evaluation_id}",
                description="Officer evaluation",
                created_by_discord_id=evaluator_id_text,
                created_at=now_text,
                connection=connection,
            )
            await self.evaluation_repository.set_score_event_id(
                evaluation_id=evaluation_id,
                score_event_id=score_event_id,
                connection=connection,
            )
            after_json = json.dumps(
                {
                    "evaluation_id": evaluation_id,
                    "member_id": member_id,
                    "score": score,
                    "status": "ACTIVE",
                    "score_event_id": score_event_id,
                },
                ensure_ascii=False,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id_text,
                actor_discord_id=evaluator_id_text,
                action_type="EVALUATION_CREATED",
                target_type="EVALUATION",
                target_id=str(evaluation_id),
                before_json=None,
                after_json=after_json,
                reason=cleaned_reason,
                created_at=now_text,
                connection=connection,
            )
            total_score = await self.score_repository.get_total_score(
                member_id=member_id,
                connection=connection,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        current_rank = get_rank(total_score)
        return EvaluationResult(
            status=EvaluationStatus.CREATED,
            evaluation_id=evaluation_id,
            target_discord_id=target_id_text,
            score=score,
            previous_total=previous_total,
            total_score=total_score,
            previous_rank=previous_rank,
            current_rank=current_rank,
            rank_changed=previous_rank != current_rank,
            reason=cleaned_reason,
        )

    async def cancel_evaluation(
        self,
        *,
        guild_id: int | str,
        evaluation_id: int,
        actor_discord_id: int | str,
        cancellation_reason: str,
        has_permission: bool,
        now: datetime,
    ) -> EvaluationResult:
        """Cancel an ACTIVE evaluation with a reversal score event."""

        self._require_aware(now)
        if not has_permission:
            return EvaluationResult(status=EvaluationStatus.PERMISSION_DENIED)

        cleaned_reason = cancellation_reason.strip()
        if not self._valid_reason(cleaned_reason):
            return EvaluationResult(status=EvaluationStatus.INVALID_REASON)

        guild_id_text = str(guild_id)
        actor_id_text = str(actor_discord_id)
        now_text = now.isoformat()
        connection = await self.score_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            evaluation = await self.evaluation_repository.get_by_id(
                evaluation_id=evaluation_id,
                connection=connection,
            )
            if evaluation is None or evaluation["guild_id"] != guild_id_text:
                await connection.rollback()
                return EvaluationResult(status=EvaluationStatus.NOT_FOUND)
            if evaluation["status"] != "ACTIVE":
                await connection.rollback()
                return EvaluationResult(
                    status=EvaluationStatus.ALREADY_CANCELLED,
                    evaluation_id=evaluation_id,
                )

            member_id = int(evaluation["member_id"])
            previous_total = await self.score_repository.get_total_score(
                member_id=member_id,
                connection=connection,
            )
            previous_rank = get_rank(previous_total)
            original_event_id = int(evaluation["score_event_id"])
            reversal_delta = -int(evaluation["score"])
            reversal_event_id = await self.score_repository.create_reversal_event(
                guild_id=guild_id_text,
                member_id=member_id,
                event_type="EVALUATION_REVERSAL",
                delta=reversal_delta,
                reference_type="EVALUATION",
                reference_id=evaluation_id,
                dedup_key=f"reverse:{original_event_id}",
                description="Evaluation cancelled",
                created_by_discord_id=actor_id_text,
                created_at=now_text,
                reversed_event_id=original_event_id,
                connection=connection,
            )
            await self.evaluation_repository.mark_cancelled(
                evaluation_id=evaluation_id,
                cancelled_at=now_text,
                cancelled_by_discord_id=actor_id_text,
                cancellation_reason=cleaned_reason,
                reversal_score_event_id=reversal_event_id,
                connection=connection,
            )
            before_json = json.dumps(
                {
                    "status": "ACTIVE",
                    "score": evaluation["score"],
                    "score_event_id": original_event_id,
                },
                ensure_ascii=False,
            )
            after_json = json.dumps(
                {
                    "status": "CANCELLED",
                    "reversal_score_event_id": reversal_event_id,
                    "reversal_delta": reversal_delta,
                },
                ensure_ascii=False,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id_text,
                actor_discord_id=actor_id_text,
                action_type="EVALUATION_CANCELLED",
                target_type="EVALUATION",
                target_id=str(evaluation_id),
                before_json=before_json,
                after_json=after_json,
                reason=cleaned_reason,
                created_at=now_text,
                connection=connection,
            )
            total_score = await self.score_repository.get_total_score(
                member_id=member_id,
                connection=connection,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        current_rank = get_rank(total_score)
        return EvaluationResult(
            status=EvaluationStatus.CANCELLED,
            evaluation_id=evaluation_id,
            target_discord_id=evaluation["discord_id"],
            score=int(evaluation["score"]),
            reversal_delta=reversal_delta,
            previous_total=previous_total,
            total_score=total_score,
            previous_rank=previous_rank,
            current_rank=current_rank,
            rank_changed=previous_rank != current_rank,
            reason=cleaned_reason,
        )

    async def adjust_score(
        self,
        *,
        guild_id: int | str,
        target_discord_id: int | str,
        actor_discord_id: int | str,
        delta: int,
        reason: str,
        has_permission: bool,
        now: datetime,
    ) -> ManualScoreResult:
        """Create a manual score adjustment event and audit log."""

        self._require_aware(now)
        if not has_permission:
            return ManualScoreResult(status=ManualScoreStatus.PERMISSION_DENIED)
        if delta == 0 or delta < MANUAL_MIN_SCORE or delta > MANUAL_MAX_SCORE:
            return ManualScoreResult(status=ManualScoreStatus.INVALID_SCORE)

        cleaned_reason = reason.strip()
        if not self._valid_reason(cleaned_reason):
            return ManualScoreResult(status=ManualScoreStatus.INVALID_REASON)

        guild_id_text = str(guild_id)
        target_id_text = str(target_discord_id)
        actor_id_text = str(actor_discord_id)
        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=target_id_text,
        )
        if member is None or not member["is_active"]:
            return ManualScoreResult(status=ManualScoreStatus.TARGET_NOT_ACTIVE)

        now_text = now.isoformat()
        member_id = int(member["id"])
        connection = await self.score_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            previous_total = await self.score_repository.get_total_score(
                member_id=member_id,
                connection=connection,
            )
            previous_rank = get_rank(previous_total)
            event_id = await self.score_repository.create_event(
                guild_id=guild_id_text,
                member_id=member_id,
                event_type="MANUAL_ADJUSTMENT",
                delta=delta,
                reference_type="MANUAL",
                reference_id=None,
                dedup_key=f"manual:{uuid.uuid4()}",
                description="Manual score adjustment",
                created_by_discord_id=actor_id_text,
                created_at=now_text,
                connection=connection,
            )
            total_score = await self.score_repository.get_total_score(
                member_id=member_id,
                connection=connection,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id_text,
                actor_discord_id=actor_id_text,
                action_type="SCORE_ADJUSTED",
                target_type="MEMBER",
                target_id=str(member_id),
                before_json=json.dumps(
                    {"total_score": previous_total},
                    ensure_ascii=False,
                ),
                after_json=json.dumps(
                    {
                        "total_score": total_score,
                        "delta": delta,
                        "score_event_id": event_id,
                    },
                    ensure_ascii=False,
                ),
                reason=cleaned_reason,
                created_at=now_text,
                connection=connection,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        current_rank = get_rank(total_score)
        return ManualScoreResult(
            status=ManualScoreStatus.ADJUSTED,
            target_discord_id=target_id_text,
            delta=delta,
            previous_total=previous_total,
            total_score=total_score,
            previous_rank=previous_rank,
            current_rank=current_rank,
            rank_changed=previous_rank != current_rank,
            reason=cleaned_reason,
        )

    def _valid_reason(self, reason: str) -> bool:
        return MIN_REASON_LENGTH <= len(reason) <= MAX_REASON_LENGTH

    def _require_aware(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")
