"""Stage B 출석 조정 비즈니스 규칙을 담당한다."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
from typing import Any

import aiosqlite

from bot.policies.score_policy import get_attendance_score
from bot.repositories.adjustment_repository import AdjustmentRepository
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.excuse_repository import ExcuseRepository
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository


class AdjustmentStatus(Enum):
    """출석 조정 작업에서 예상되는 처리 결과."""

    APPLIED = "APPLIED"
    CANCELLED = "CANCELLED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    RECORD_NOT_FOUND = "RECORD_NOT_FOUND"
    EXCUSE_NOT_APPROVED = "EXCUSE_NOT_APPROVED"
    INVALID_STATUS = "INVALID_STATUS"
    INVALID_REASON = "INVALID_REASON"
    INVALID_REDUCTION = "INVALID_REDUCTION"
    DUPLICATE_ACTIVE_ADJUSTMENT = "DUPLICATE_ACTIVE_ADJUSTMENT"
    ACTIVE_ADJUSTMENT_NOT_FOUND = "ACTIVE_ADJUSTMENT_NOT_FOUND"


@dataclass(frozen=True)
class AdjustmentResult:
    """조정 적용 또는 취소 후 반환되는 결과."""

    status: AdjustmentStatus
    adjustment_id: int | None = None
    target_discord_id: str | None = None
    attendance_date: str | None = None
    original_status: str | None = None
    resulting_status: str | None = None
    original_late_seconds: int | None = None
    requested_reduction_seconds: int | None = None
    resulting_late_seconds: int | None = None
    score_delta: int = 0
    reversal_delta: int = 0
    excuse_request_id: int | None = None
    reason: str | None = None


class AdjustmentService:
    """지각 감면과 결석 면제를 적용하고 취소한다."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        member_repository: MemberRepository,
        session_repository: SessionRepository,
        attendance_repository: AttendanceRepository,
        excuse_repository: ExcuseRepository,
        score_repository: ScoreRepository,
        audit_repository: AuditRepository,
        adjustment_repository: AdjustmentRepository,
    ) -> None:
        """서비스 의존성을 초기화한다."""

        self.guild_repository = guild_repository
        self.member_repository = member_repository
        self.session_repository = session_repository
        self.attendance_repository = attendance_repository
        self.excuse_repository = excuse_repository
        self.score_repository = score_repository
        self.audit_repository = audit_repository
        self.adjustment_repository = adjustment_repository

    async def apply_late_reduction(
        self,
        *,
        guild_id: int | str,
        target_discord_id: int | str,
        attendance_date: str,
        reduction_minutes: int,
        full_reduction: bool,
        reason: str,
        actor_discord_id: int | str,
        has_permission: bool,
        now: datetime,
    ) -> AdjustmentResult:
        """승인된 사유 신청을 바탕으로 지각 감면을 적용한다."""

        self._require_aware(now)
        if not has_permission:
            return AdjustmentResult(status=AdjustmentStatus.PERMISSION_DENIED)
        cleaned_reason = reason.strip()
        if len(cleaned_reason) < 2 or len(cleaned_reason) > 500:
            return AdjustmentResult(status=AdjustmentStatus.INVALID_REASON)
        if not full_reduction and reduction_minutes <= 0:
            return AdjustmentResult(status=AdjustmentStatus.INVALID_REDUCTION)

        located = await self._locate_adjustable_record(
            guild_id=str(guild_id),
            target_discord_id=str(target_discord_id),
            attendance_date=attendance_date,
        )
        if isinstance(located, AdjustmentResult):
            return located
        settings, member, session, record, excuse_request = located

        if record["status"] not in {"LATE", "EXCUSED_LATE"} or record["checked_at"] is None:
            return AdjustmentResult(status=AdjustmentStatus.INVALID_STATUS)

        checked_at = datetime.fromisoformat(record["checked_at"])
        late_at = datetime.fromisoformat(session["late_at"])
        original_late_seconds = max(0, int((checked_at - late_at).total_seconds()))
        if original_late_seconds <= 0:
            return AdjustmentResult(status=AdjustmentStatus.INVALID_STATUS)

        requested_seconds = (
            original_late_seconds if full_reduction else reduction_minutes * 60
        )
        if requested_seconds <= 0:
            return AdjustmentResult(status=AdjustmentStatus.INVALID_REDUCTION)
        applied_seconds = min(requested_seconds, original_late_seconds)
        resulting_late_seconds = max(0, original_late_seconds - applied_seconds)
        resulting_status = "PRESENT" if resulting_late_seconds == 0 else record["status"]

        current_score = await self._current_record_score(int(record["id"]))
        target_score = get_attendance_score(resulting_status)
        score_delta = target_score - current_score

        return await self._create_adjustment(
            guild_id=str(guild_id),
            member=member,
            session=session,
            record=record,
            excuse_request=excuse_request,
            adjustment_type="LATE_REDUCTION",
            requested_reduction_seconds=applied_seconds,
            original_status=record["status"],
            resulting_status=resulting_status,
            original_late_seconds=original_late_seconds,
            resulting_late_seconds=resulting_late_seconds,
            score_delta=score_delta,
            event_type="LATE_REDUCTION_ADJUSTMENT",
            audit_action="LATE_REDUCTION_APPLIED",
            description="Late reduction attendance score adjustment",
            actor_discord_id=str(actor_discord_id),
            now=now,
            reason=cleaned_reason,
        )

    async def cancel_late_reduction(
        self,
        *,
        guild_id: int | str,
        target_discord_id: int | str,
        attendance_date: str,
        reason: str,
        actor_discord_id: int | str,
        has_permission: bool,
        now: datetime,
    ) -> AdjustmentResult:
        """멤버와 날짜에 연결된 활성 지각 감면을 취소한다."""

        return await self._cancel_by_member_date(
            guild_id=str(guild_id),
            target_discord_id=str(target_discord_id),
            attendance_date=attendance_date,
            adjustment_type="LATE_REDUCTION",
            reversal_event_type="LATE_REDUCTION_REVERSAL",
            audit_action="LATE_REDUCTION_CANCELLED",
            reason=reason,
            actor_discord_id=str(actor_discord_id),
            has_permission=has_permission,
            now=now,
        )

    async def apply_absence_exemption(
        self,
        *,
        guild_id: int | str,
        target_discord_id: int | str,
        attendance_date: str,
        reason: str,
        actor_discord_id: int | str,
        has_permission: bool,
        now: datetime,
    ) -> AdjustmentResult:
        """승인된 사유 신청을 사용해 결석 면제를 적용한다."""

        self._require_aware(now)
        if not has_permission:
            return AdjustmentResult(status=AdjustmentStatus.PERMISSION_DENIED)
        cleaned_reason = reason.strip()
        if len(cleaned_reason) < 2 or len(cleaned_reason) > 500:
            return AdjustmentResult(status=AdjustmentStatus.INVALID_REASON)

        located = await self._locate_adjustable_record(
            guild_id=str(guild_id),
            target_discord_id=str(target_discord_id),
            attendance_date=attendance_date,
        )
        if isinstance(located, AdjustmentResult):
            return located
        settings, member, session, record, excuse_request = located

        if record["status"] not in {"ABSENT", "EXCUSED_ABSENT"}:
            return AdjustmentResult(status=AdjustmentStatus.INVALID_STATUS)

        current_score = await self._current_record_score(int(record["id"]))
        score_delta = 0 - current_score

        return await self._create_adjustment(
            guild_id=str(guild_id),
            member=member,
            session=session,
            record=record,
            excuse_request=excuse_request,
            adjustment_type="ABSENCE_EXEMPTION",
            requested_reduction_seconds=None,
            original_status=record["status"],
            resulting_status="EXEMPT_ABSENT",
            original_late_seconds=None,
            resulting_late_seconds=None,
            score_delta=score_delta,
            event_type="ABSENCE_EXEMPTION_ADJUSTMENT",
            audit_action="ABSENCE_EXEMPTION_APPLIED",
            description="Absence exemption score adjustment",
            actor_discord_id=str(actor_discord_id),
            now=now,
            reason=cleaned_reason,
        )

    async def cancel_absence_exemption(
        self,
        *,
        guild_id: int | str,
        target_discord_id: int | str,
        attendance_date: str,
        reason: str,
        actor_discord_id: int | str,
        has_permission: bool,
        now: datetime,
    ) -> AdjustmentResult:
        """멤버와 날짜에 연결된 활성 결석 면제를 취소한다."""

        return await self._cancel_by_member_date(
            guild_id=str(guild_id),
            target_discord_id=str(target_discord_id),
            attendance_date=attendance_date,
            adjustment_type="ABSENCE_EXEMPTION",
            reversal_event_type="ABSENCE_EXEMPTION_REVERSAL",
            audit_action="ABSENCE_EXEMPTION_CANCELLED",
            reason=reason,
            actor_discord_id=str(actor_discord_id),
            has_permission=has_permission,
            now=now,
        )

    async def _create_adjustment(
        self,
        *,
        guild_id: str,
        member: dict[str, Any],
        session: dict[str, Any],
        record: dict[str, Any],
        excuse_request: dict[str, Any],
        adjustment_type: str,
        requested_reduction_seconds: int | None,
        original_status: str,
        resulting_status: str,
        original_late_seconds: int | None,
        resulting_late_seconds: int | None,
        score_delta: int,
        event_type: str,
        audit_action: str,
        description: str,
        actor_discord_id: str,
        now: datetime,
        reason: str,
    ) -> AdjustmentResult:
        """조정, 점수 변동, 감사 로그를 원자적으로 저장한다."""

        now_text = now.isoformat()
        connection = await self.adjustment_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            duplicate = await self.adjustment_repository.get_active_for_record(
                attendance_record_id=int(record["id"]),
                adjustment_type=adjustment_type,
                connection=connection,
            )
            if duplicate is not None:
                await connection.rollback()
                return AdjustmentResult(status=AdjustmentStatus.DUPLICATE_ACTIVE_ADJUSTMENT)

            adjustment_id = await self.adjustment_repository.create_adjustment(
                guild_id=guild_id,
                session_id=int(session["id"]),
                member_id=int(member["id"]),
                attendance_record_id=int(record["id"]),
                excuse_request_id=int(excuse_request["id"]),
                adjustment_type=adjustment_type,
                requested_reduction_seconds=requested_reduction_seconds,
                original_status=original_status,
                resulting_status=resulting_status,
                original_late_seconds=original_late_seconds,
                resulting_late_seconds=resulting_late_seconds,
                applied_by_discord_id=actor_discord_id,
                applied_at=now_text,
                reason=reason,
                connection=connection,
            )
            score_event_id = None
            if score_delta != 0:
                score_event_id = await self.score_repository.create_event(
                    guild_id=guild_id,
                    member_id=int(member["id"]),
                    event_type=event_type,
                    delta=score_delta,
                    reference_type="ATTENDANCE_ADJUSTMENT",
                    reference_id=adjustment_id,
                    dedup_key=f"attendance-adjustment:{adjustment_id}:apply",
                    description=description,
                    created_by_discord_id=actor_discord_id,
                    created_at=now_text,
                    connection=connection,
                )
                await self.adjustment_repository.set_score_event(
                    adjustment_id=adjustment_id,
                    score_event_id=score_event_id,
                    now=now_text,
                    connection=connection,
                )
            await self.audit_repository.create_log(
                guild_id=guild_id,
                actor_discord_id=actor_discord_id,
                action_type=audit_action,
                target_type="ATTENDANCE_ADJUSTMENT",
                target_id=str(adjustment_id),
                before_json=json.dumps(
                    {
                        "attendance_record_id": record["id"],
                        "original_status": original_status,
                    },
                    ensure_ascii=False,
                ),
                after_json=json.dumps(
                    {
                        "adjustment_id": adjustment_id,
                        "resulting_status": resulting_status,
                        "score_delta": score_delta,
                        "score_event_id": score_event_id,
                        "status": "ACTIVE",
                    },
                    ensure_ascii=False,
                ),
                reason=reason,
                created_at=now_text,
                connection=connection,
            )
            await connection.commit()
            return AdjustmentResult(
                status=AdjustmentStatus.APPLIED,
                adjustment_id=adjustment_id,
                target_discord_id=member["discord_id"],
                attendance_date=session["attendance_date"],
                original_status=original_status,
                resulting_status=resulting_status,
                original_late_seconds=original_late_seconds,
                requested_reduction_seconds=requested_reduction_seconds,
                resulting_late_seconds=resulting_late_seconds,
                score_delta=score_delta,
                excuse_request_id=int(excuse_request["id"]),
                reason=reason,
            )
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _cancel_by_member_date(
        self,
        *,
        guild_id: str,
        target_discord_id: str,
        attendance_date: str,
        adjustment_type: str,
        reversal_event_type: str,
        audit_action: str,
        reason: str,
        actor_discord_id: str,
        has_permission: bool,
        now: datetime,
    ) -> AdjustmentResult:
        """사용자와 날짜 기준으로 활성 조정을 찾아 취소한다."""

        self._require_aware(now)
        if not has_permission:
            return AdjustmentResult(status=AdjustmentStatus.PERMISSION_DENIED)
        cleaned_reason = reason.strip()
        if len(cleaned_reason) < 2 or len(cleaned_reason) > 500:
            return AdjustmentResult(status=AdjustmentStatus.INVALID_REASON)
        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id,
            discord_id=target_discord_id,
        )
        if member is None:
            return AdjustmentResult(status=AdjustmentStatus.TARGET_NOT_FOUND)

        now_text = now.isoformat()
        connection = await self.adjustment_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            adjustment = await self.adjustment_repository.get_active_for_member_date(
                guild_id=guild_id,
                member_id=int(member["id"]),
                attendance_date=attendance_date,
                adjustment_type=adjustment_type,
                connection=connection,
            )
            if adjustment is None:
                await connection.rollback()
                return AdjustmentResult(status=AdjustmentStatus.ACTIVE_ADJUSTMENT_NOT_FOUND)

            reversal_id = None
            reversal_delta = 0
            if adjustment["score_event_id"] is not None:
                original_event = await self.score_repository.get_by_id(
                    score_event_id=int(adjustment["score_event_id"]),
                    connection=connection,
                )
                assert original_event is not None
                reversal_delta = -int(original_event["delta"])
                if reversal_delta != 0:
                    reversal_id = await self.score_repository.create_reversal_event(
                        guild_id=guild_id,
                        member_id=int(member["id"]),
                        event_type=reversal_event_type,
                        delta=reversal_delta,
                        reference_type="ATTENDANCE_ADJUSTMENT",
                        reference_id=int(adjustment["id"]),
                        dedup_key=f"reverse:{original_event['id']}",
                        description="Attendance adjustment cancelled",
                        created_by_discord_id=actor_discord_id,
                        created_at=now_text,
                        reversed_event_id=int(original_event["id"]),
                        connection=connection,
                    )
            await self.adjustment_repository.cancel_adjustment(
                adjustment_id=int(adjustment["id"]),
                cancelled_by_discord_id=actor_discord_id,
                cancelled_at=now_text,
                cancellation_reason=cleaned_reason,
                reversal_score_event_id=reversal_id,
                connection=connection,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id,
                actor_discord_id=actor_discord_id,
                action_type=audit_action,
                target_type="ATTENDANCE_ADJUSTMENT",
                target_id=str(adjustment["id"]),
                before_json=json.dumps(adjustment, ensure_ascii=False),
                after_json=json.dumps(
                    {
                        "status": "CANCELLED",
                        "reversal_score_event_id": reversal_id,
                        "reversal_delta": reversal_delta,
                    },
                    ensure_ascii=False,
                ),
                reason=cleaned_reason,
                created_at=now_text,
                connection=connection,
            )
            await connection.commit()
            return AdjustmentResult(
                status=AdjustmentStatus.CANCELLED,
                adjustment_id=int(adjustment["id"]),
                target_discord_id=member["discord_id"],
                attendance_date=attendance_date,
                original_status=adjustment["original_status"],
                resulting_status=adjustment["resulting_status"],
                original_late_seconds=adjustment["original_late_seconds"],
                requested_reduction_seconds=adjustment["requested_reduction_seconds"],
                resulting_late_seconds=adjustment["resulting_late_seconds"],
                reversal_delta=reversal_delta,
                excuse_request_id=int(adjustment["excuse_request_id"]),
                reason=cleaned_reason,
            )
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _locate_adjustable_record(
        self,
        *,
        guild_id: str,
        target_discord_id: str,
        attendance_date: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | AdjustmentResult:
        """감면 또는 면제에 필요한 설정, 대원, 세션, 기록, 승인 사유를 찾는다."""

        settings = await self.guild_repository.get_by_guild_id(guild_id)
        if settings is None:
            return AdjustmentResult(status=AdjustmentStatus.NOT_CONFIGURED)
        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id,
            discord_id=target_discord_id,
        )
        if member is None:
            return AdjustmentResult(status=AdjustmentStatus.TARGET_NOT_FOUND)
        session = await self.session_repository.get_by_guild_and_date(
            guild_id=guild_id,
            attendance_date=attendance_date,
        )
        if session is None:
            return AdjustmentResult(status=AdjustmentStatus.SESSION_NOT_FOUND)
        record = await self.attendance_repository.get_by_session_and_member(
            session_id=int(session["id"]),
            member_id=int(member["id"]),
        )
        if record is None:
            return AdjustmentResult(status=AdjustmentStatus.RECORD_NOT_FOUND)
        excuse_request = await self.excuse_repository.get_effective_approved_request(
            guild_id=guild_id,
            member_id=int(member["id"]),
            target_date=attendance_date,
        )
        if excuse_request is None:
            return AdjustmentResult(status=AdjustmentStatus.EXCUSE_NOT_APPROVED)
        return settings, member, session, record, excuse_request

    async def _current_record_score(self, attendance_record_id: int) -> int:
        """출석 기록에 연결된 현재 점수 합계를 조회한다."""

        connection = await self.adjustment_repository.database.connect()
        try:
            return await self.adjustment_repository.get_attendance_score_for_record(
                attendance_record_id=attendance_record_id,
                connection=connection,
            )
        finally:
            await connection.close()

    def _require_aware(self, now: datetime) -> None:
        """timezone-aware datetime인지 검증한다."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")
