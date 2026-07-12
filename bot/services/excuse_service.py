"""사유 신청과 승인 흐름의 비즈니스 규칙을 담당한다."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import logging
from typing import Any

import aiosqlite

from bot.policies.score_policy import get_attendance_score
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.excuse_repository import ExcuseRepository
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.services.excuse_policy import EXCUSE_TYPE_LABELS, ExcusePolicyService
from bot.utils.time_utils import (
    build_session_window,
    get_server_today,
    get_weekday_code,
    parse_attendance_days,
    parse_hhmm,
)


logger = logging.getLogger(__name__)


class ExcuseStatus(Enum):
    """사유 명령에서 예상되는 처리 결과."""

    CREATED_PENDING = "CREATED_PENDING"
    CREATED_AUTO_APPROVED = "CREATED_AUTO_APPROVED"
    DUPLICATE_ACTIVE_REQUEST = "DUPLICATE_ACTIVE_REQUEST"
    INVALID_DATE = "INVALID_DATE"
    PAST_DATE = "PAST_DATE"
    NOT_ATTENDANCE_DAY = "NOT_ATTENDANCE_DAY"
    TOO_LATE_TO_REQUEST = "TOO_LATE_TO_REQUEST"
    INVALID_TIME = "INVALID_TIME"
    INVALID_REASON = "INVALID_REASON"
    NOT_REGISTERED = "NOT_REGISTERED"
    NOT_SESSION_MEMBER = "NOT_SESSION_MEMBER"
    NOT_FOUND = "NOT_FOUND"
    NOT_OWNER = "NOT_OWNER"
    INVALID_STATUS = "INVALID_STATUS"
    CANCELLED = "CANCELLED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ALREADY_DECIDED = "ALREADY_DECIDED"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    ADMIN_OVERRIDE_CREATED = "ADMIN_OVERRIDE_CREATED"
    POLICY_UPDATED = "POLICY_UPDATED"


@dataclass(frozen=True)
class ExcuseResult:
    """사유 서비스 메서드가 반환하는 결과."""

    status: ExcuseStatus
    request: dict[str, Any] | None = None
    attendance_record: dict[str, Any] | None = None
    score_delta: int = 0
    deadline_at: datetime | None = None


class ExcuseService:
    """사유 신청을 검증하고 상태를 변경한다."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        member_repository: MemberRepository,
        session_repository: SessionRepository,
        attendance_repository: AttendanceRepository,
        score_repository: ScoreRepository,
        excuse_repository: ExcuseRepository,
        audit_repository: AuditRepository,
    ) -> None:
        """서비스 의존성을 초기화한다."""

        self.guild_repository = guild_repository
        self.member_repository = member_repository
        self.session_repository = session_repository
        self.attendance_repository = attendance_repository
        self.score_repository = score_repository
        self.excuse_repository = excuse_repository
        self.audit_repository = audit_repository
        self.policy_service = ExcusePolicyService()

    async def create_request(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
        target_date: str,
        expected_time: str | None,
        reason: str,
        now: datetime,
        excuse_type: str = "ABSENCE",
    ) -> ExcuseResult:
        """활성 멤버의 사유 신청을 생성한다."""

        self._require_aware(now)
        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return ExcuseResult(status=ExcuseStatus.NOT_CONFIGURED)

        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=str(discord_id),
        )
        if member is None or not member["is_active"]:
            return ExcuseResult(status=ExcuseStatus.NOT_REGISTERED)

        parsed_date = self._parse_date(target_date)
        if parsed_date is None:
            return ExcuseResult(status=ExcuseStatus.INVALID_DATE)

        local_today = get_server_today(now, settings["timezone"])
        if parsed_date < local_today:
            return ExcuseResult(status=ExcuseStatus.PAST_DATE)

        if get_weekday_code(parsed_date) not in parse_attendance_days(
            settings["attendance_days"]
        ):
            return ExcuseResult(status=ExcuseStatus.NOT_ATTENDANCE_DAY)

        normalized_type = excuse_type.strip().upper()
        if normalized_type not in EXCUSE_TYPE_LABELS:
            return ExcuseResult(status=ExcuseStatus.INVALID_STATUS)

        if expected_time:
            try:
                parse_hhmm(expected_time)
            except ValueError:
                return ExcuseResult(status=ExcuseStatus.INVALID_TIME)

        cleaned_reason = reason.strip()
        if len(cleaned_reason) < 2 or len(cleaned_reason) > 500:
            return ExcuseResult(status=ExcuseStatus.INVALID_REASON)

        policy = self.policy_service.from_settings(settings)
        can_submit, deadline_at = self.policy_service.can_submit(
            now=now,
            target_date=parsed_date,
            policy=policy,
        )
        if not can_submit and not policy.allow_late_request:
            return ExcuseResult(
                status=ExcuseStatus.TOO_LATE_TO_REQUEST,
                deadline_at=deadline_at,
            )

        existing_session = await self.session_repository.get_by_guild_and_date(
            guild_id=guild_id_text,
            attendance_date=target_date,
        )
        if existing_session is not None:
            if existing_session["status"] == "CANCELLED":
                return ExcuseResult(status=ExcuseStatus.INVALID_STATUS)
            if not await self.session_repository.is_session_member(
                session_id=int(existing_session["id"]),
                member_id=int(member["id"]),
            ):
                return ExcuseResult(status=ExcuseStatus.NOT_SESSION_MEMBER)

        duplicate = await self.excuse_repository.get_active_by_member_and_date(
            guild_id=guild_id_text,
            member_id=int(member["id"]),
            target_date=target_date,
        )
        if duplicate is not None:
            return ExcuseResult(status=ExcuseStatus.DUPLICATE_ACTIVE_REQUEST)

        status = "PENDING" if policy.require_admin_approval else "AUTO_APPROVED"
        try:
            request = await self.excuse_repository.create(
                guild_id=guild_id_text,
                member_id=int(member["id"]),
                target_date=target_date,
                excuse_type=normalized_type,
                reason=cleaned_reason,
                expected_time=expected_time,
                status=status,
                requested_at=now.isoformat(),
                deadline_at=deadline_at.astimezone(timezone.utc).isoformat(),
                attendance_session_id=(
                    None if existing_session is None else int(existing_session["id"])
                ),
            )
        except aiosqlite.IntegrityError:
            return ExcuseResult(status=ExcuseStatus.DUPLICATE_ACTIVE_REQUEST)

        logger.info(
            "Excuse request created: guild_id=%s member_id=%s request_id=%s status=%s",
            guild_id_text,
            member["id"],
            request["id"],
            status,
        )
        return ExcuseResult(
            status=(
                ExcuseStatus.CREATED_AUTO_APPROVED
                if status == "AUTO_APPROVED"
                else ExcuseStatus.CREATED_PENDING
            ),
            request=request,
            deadline_at=deadline_at,
        )

    async def create_admin_override(
        self,
        *,
        guild_id: int | str,
        target_discord_id: int | str,
        actor_discord_id: int | str,
        target_date: str,
        excuse_type: str,
        reason: str,
        admin_note: str,
        now: datetime,
    ) -> ExcuseResult:
        """Create an admin-only late exception as an approved request."""

        self._require_aware(now)
        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return ExcuseResult(status=ExcuseStatus.NOT_CONFIGURED)

        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=str(target_discord_id),
        )
        if member is None or not member["is_active"]:
            return ExcuseResult(status=ExcuseStatus.NOT_REGISTERED)

        parsed_date = self._parse_date(target_date)
        if parsed_date is None:
            return ExcuseResult(status=ExcuseStatus.INVALID_DATE)
        normalized_type = excuse_type.strip().upper()
        if normalized_type not in EXCUSE_TYPE_LABELS:
            return ExcuseResult(status=ExcuseStatus.INVALID_STATUS)

        policy = self.policy_service.from_settings(settings)
        _, deadline_at = self.policy_service.can_submit(
            now=now,
            target_date=parsed_date,
            policy=policy,
        )
        request = await self.excuse_repository.create(
            guild_id=guild_id_text,
            member_id=int(member["id"]),
            target_date=target_date,
            excuse_type=normalized_type,
            reason=reason.strip(),
            expected_time=None,
            status="APPROVED",
            requested_at=now.isoformat(),
            deadline_at=deadline_at.astimezone(timezone.utc).isoformat(),
            is_admin_override=True,
            approval_type="ADMIN_OVERRIDE",
            decided_by_discord_id=str(actor_discord_id),
            decided_at=now.isoformat(),
            processed_by=str(actor_discord_id),
            processed_at=now.isoformat(),
            admin_note=admin_note.strip(),
        )

        connection = await self.excuse_repository.database.connect()
        try:
            await self.audit_repository.create_log(
                guild_id=guild_id_text,
                actor_discord_id=str(actor_discord_id),
                action_type="EXCUSE_ADMIN_OVERRIDE",
                target_type="EXCUSE_REQUEST",
                target_id=str(request["id"]),
                before_json=None,
                after_json=json.dumps(
                    {
                        "status": "APPROVED",
                        "excuse_type": normalized_type,
                        "deadline_at": deadline_at.isoformat(),
                    }
                ),
                reason=admin_note.strip() or "관리자 예외 등록",
                created_at=now.isoformat(),
                connection=connection,
            )
            await connection.commit()
        finally:
            await connection.close()

        updated = await self.excuse_repository.get_by_id(
            excuse_request_id=int(request["id"])
        )
        return ExcuseResult(
            status=ExcuseStatus.ADMIN_OVERRIDE_CREATED,
            request=updated,
            deadline_at=deadline_at,
        )

    async def update_policy(
        self,
        *,
        guild_id: int | str,
        actor_discord_id: int | str,
        deadline_time: str,
        deadline_days_before: int,
        now: datetime,
    ) -> ExcuseResult:
        """Update excuse deadline policy for a guild."""

        self._require_aware(now)
        try:
            parse_hhmm(deadline_time)
        except ValueError:
            return ExcuseResult(status=ExcuseStatus.INVALID_TIME)
        if deadline_days_before < 0:
            return ExcuseResult(status=ExcuseStatus.INVALID_DATE)

        guild_id_text = str(guild_id)
        before = await self.guild_repository.get_by_guild_id(guild_id_text)
        if before is None:
            return ExcuseResult(status=ExcuseStatus.NOT_CONFIGURED)

        connection = await self.excuse_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            await self.excuse_repository.update_policy(
                guild_id=guild_id_text,
                deadline_time=deadline_time,
                deadline_days_before=deadline_days_before,
                actor_discord_id=str(actor_discord_id),
                now=now.isoformat(),
                connection=connection,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id_text,
                actor_discord_id=str(actor_discord_id),
                action_type="EXCUSE_POLICY_UPDATED",
                target_type="GUILD_SETTINGS",
                target_id=guild_id_text,
                before_json=json.dumps(
                    {
                        "excuse_deadline_time": before["excuse_deadline_time"],
                        "excuse_deadline_days_before": before[
                            "excuse_deadline_days_before"
                        ],
                    }
                ),
                after_json=json.dumps(
                    {
                        "excuse_deadline_time": deadline_time,
                        "excuse_deadline_days_before": deadline_days_before,
                    }
                ),
                reason="사유 신청 정책 변경",
                created_at=now.isoformat(),
                connection=connection,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        updated = await self.guild_repository.get_by_guild_id(guild_id_text)
        return ExcuseResult(status=ExcuseStatus.POLICY_UPDATED, request=updated)

    async def cancel_request(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
        excuse_request_id: int,
        now: datetime,
    ) -> ExcuseResult:
        """호출자의 활성 사유 신청을 취소한다."""

        self._require_aware(now)
        request = await self.excuse_repository.get_by_id(
            excuse_request_id=excuse_request_id
        )
        if request is None or request["guild_id"] != str(guild_id):
            return ExcuseResult(status=ExcuseStatus.NOT_FOUND)

        member = await self.member_repository.get_by_discord_id(
            guild_id=str(guild_id),
            discord_id=str(discord_id),
        )
        if member is None or int(member["id"]) != int(request["member_id"]):
            return ExcuseResult(status=ExcuseStatus.NOT_OWNER)

        if request["status"] != "PENDING":
            return ExcuseResult(status=ExcuseStatus.INVALID_STATUS)

        applied = await self._is_request_applied(
            excuse_request_id=excuse_request_id
        )
        if applied:
            return ExcuseResult(status=ExcuseStatus.ALREADY_APPLIED, request=request)

        connection = await self.excuse_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            await self.excuse_repository.cancel_active(
                excuse_request_id=excuse_request_id,
                cancelled_at=now.isoformat(),
                connection=connection,
            )
            await self.audit_repository.create_log(
                guild_id=str(guild_id),
                actor_discord_id=str(discord_id),
                action_type="EXCUSE_CANCELLED",
                target_type="EXCUSE_REQUEST",
                target_id=str(excuse_request_id),
                before_json=json.dumps({"status": request["status"]}),
                after_json=json.dumps({"status": "CANCELLED"}),
                reason="신청자 취소",
                created_at=now.isoformat(),
                connection=connection,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        updated = await self.excuse_repository.get_by_id(
            excuse_request_id=excuse_request_id
        )
        return ExcuseResult(status=ExcuseStatus.CANCELLED, request=updated)

    async def approve_request(
        self,
        *,
        guild_id: int | str,
        excuse_request_id: int,
        actor_discord_id: int | str,
        now: datetime,
    ) -> ExcuseResult:
        """대기 중인 사유 신청을 승인하고 기존 출석 기록을 보정한다."""

        return await self._decide_request(
            guild_id=str(guild_id),
            excuse_request_id=excuse_request_id,
            actor_discord_id=str(actor_discord_id),
            now=now,
            approve=True,
            rejection_reason=None,
        )

    async def reject_request(
        self,
        *,
        guild_id: int | str,
        excuse_request_id: int,
        actor_discord_id: int | str,
        rejection_reason: str,
        now: datetime,
    ) -> ExcuseResult:
        """대기 중인 사유 신청을 거절한다."""

        reason = rejection_reason.strip()
        if len(reason) < 2 or len(reason) > 500:
            return ExcuseResult(status=ExcuseStatus.INVALID_REASON)
        return await self._decide_request(
            guild_id=str(guild_id),
            excuse_request_id=excuse_request_id,
            actor_discord_id=str(actor_discord_id),
            now=now,
            approve=False,
            rejection_reason=reason,
        )

    async def list_requests(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
        status: str | None,
        include_all: bool,
        can_view_all: bool,
    ) -> ExcuseResult:
        """사용자에게 보이는 사유 신청 목록을 조회한다."""

        member = await self.member_repository.get_by_discord_id(
            guild_id=str(guild_id),
            discord_id=str(discord_id),
        )
        if include_all and not can_view_all:
            return ExcuseResult(status=ExcuseStatus.NOT_OWNER)
        if include_all:
            rows = await self.excuse_repository.list_by_guild(
                guild_id=str(guild_id),
                status=status,
            )
        else:
            if member is None:
                return ExcuseResult(status=ExcuseStatus.NOT_REGISTERED)
            rows = await self.excuse_repository.list_by_member(
                guild_id=str(guild_id),
                member_id=int(member["id"]),
                status=status,
            )
        return ExcuseResult(status=ExcuseStatus.APPROVED, request={"rows": rows})

    async def _decide_request(
        self,
        *,
        guild_id: str,
        excuse_request_id: int,
        actor_discord_id: str,
        now: datetime,
        approve: bool,
        rejection_reason: str | None,
    ) -> ExcuseResult:
        """사유 신청 승인 또는 거절을 하나의 트랜잭션으로 처리한다."""

        self._require_aware(now)
        connection = await self.excuse_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            request = await self.excuse_repository.get_by_id(
                excuse_request_id=excuse_request_id,
                connection=connection,
            )
            if request is None or request["guild_id"] != guild_id:
                await connection.rollback()
                return ExcuseResult(status=ExcuseStatus.NOT_FOUND)
            if request["status"] != "PENDING":
                await connection.rollback()
                return ExcuseResult(status=ExcuseStatus.ALREADY_DECIDED, request=request)

            if approve:
                await self.excuse_repository.approve_pending(
                    excuse_request_id=excuse_request_id,
                    actor_discord_id=actor_discord_id,
                    decided_at=now.isoformat(),
                    connection=connection,
                )
                result = await self._reconcile_attendance_for_approval(
                    request=request,
                    actor_discord_id=actor_discord_id,
                    now=now,
                    connection=connection,
                )
                await self.audit_repository.create_log(
                    guild_id=guild_id,
                    actor_discord_id=actor_discord_id,
                    action_type="EXCUSE_APPROVED",
                    target_type="EXCUSE_REQUEST",
                    target_id=str(excuse_request_id),
                    before_json=json.dumps({"status": "PENDING"}),
                    after_json=json.dumps({"status": "APPROVED"}),
                    reason="사유 승인",
                    created_at=now.isoformat(),
                    connection=connection,
                )
                await connection.commit()
                updated = await self.excuse_repository.get_by_id(
                    excuse_request_id=excuse_request_id
                )
                return ExcuseResult(
                    status=ExcuseStatus.APPROVED,
                    request=updated,
                    attendance_record=result["record"],
                    score_delta=result["delta"],
                )

            await self.excuse_repository.reject_pending(
                excuse_request_id=excuse_request_id,
                actor_discord_id=actor_discord_id,
                decided_at=now.isoformat(),
                rejection_reason=rejection_reason or "",
                connection=connection,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id,
                actor_discord_id=actor_discord_id,
                action_type="EXCUSE_REJECTED",
                target_type="EXCUSE_REQUEST",
                target_id=str(excuse_request_id),
                before_json=json.dumps({"status": "PENDING"}),
                after_json=json.dumps({"status": "REJECTED"}),
                reason="사유 거절",
                created_at=now.isoformat(),
                connection=connection,
            )
            await connection.commit()
            updated = await self.excuse_repository.get_by_id(
                excuse_request_id=excuse_request_id
            )
            return ExcuseResult(status=ExcuseStatus.REJECTED, request=updated)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _reconcile_attendance_for_approval(
        self,
        *,
        request: dict[str, Any],
        actor_discord_id: str,
        now: datetime,
        connection: aiosqlite.Connection,
    ) -> dict[str, Any]:
        """승인된 사유를 기존 출석 기록과 점수에 반영한다."""

        session = await self.session_repository.get_by_guild_and_date(
            guild_id=request["guild_id"],
            attendance_date=request["target_date"],
            connection=connection,
        )
        if session is None:
            return {"record": None, "delta": 0}
        record = await self.attendance_repository.get_by_session_and_member(
            session_id=int(session["id"]),
            member_id=int(request["member_id"]),
            connection=connection,
        )
        if record is None:
            return {"record": None, "delta": 0}

        old_status = record["status"]
        if old_status == "PRESENT":
            await self.attendance_repository.set_excuse_request(
                attendance_record_id=int(record["id"]),
                excuse_request_id=int(request["id"]),
                connection=connection,
            )
            return {"record": record, "delta": 0}
        if old_status == "LATE":
            new_status = "EXCUSED_LATE"
        elif old_status == "ABSENT":
            new_status = "EXCUSED_ABSENT"
        else:
            return {"record": record, "delta": 0}

        delta = get_attendance_score(new_status) - get_attendance_score(old_status)
        await self.attendance_repository.update_status_for_excuse(
            attendance_record_id=int(record["id"]),
            new_status=new_status,
            excuse_request_id=int(request["id"]),
            now=now.isoformat(),
            connection=connection,
        )
        if delta != 0:
            await self.score_repository.create_event(
                guild_id=request["guild_id"],
                member_id=int(request["member_id"]),
                event_type="ATTENDANCE_EXCUSE_CORRECTION",
                delta=delta,
                reference_type="ATTENDANCE",
                reference_id=int(record["id"]),
                dedup_key=f"excuse-correction:{request['id']}:{record['id']}:approve",
                description="사유 승인에 따른 출석 점수 보정",
                created_by_discord_id=actor_discord_id,
                created_at=now.isoformat(),
                connection=connection,
            )
        await self.audit_repository.create_log(
            guild_id=request["guild_id"],
            actor_discord_id=actor_discord_id,
            action_type="EXCUSE_ATTENDANCE_RECONCILED",
            target_type="ATTENDANCE",
            target_id=str(record["id"]),
            before_json=json.dumps(
                {"status": old_status, "excuse_request_id": record["excuse_request_id"]}
            ),
            after_json=json.dumps(
                {"status": new_status, "excuse_request_id": request["id"]}
            ),
            reason="사유 승인 출석 반영",
            created_at=now.isoformat(),
            connection=connection,
        )
        record["status"] = new_status
        return {"record": record, "delta": delta}

    async def _is_request_applied(self, *, excuse_request_id: int) -> bool:
        """사유 신청이 이미 출석 기록에 연결되었는지 확인한다."""

        connection = await self.excuse_repository.database.connect()
        try:
            rows = await connection.execute_fetchall(
                """
                SELECT 1
                FROM attendance_records
                WHERE excuse_request_id = ?
                LIMIT 1;
                """,
                (excuse_request_id,),
            )
            return bool(rows)
        finally:
            await connection.close()

    def _parse_date(self, value: str):
        """YYYY-MM-DD 문자열을 date 객체로 변환하고 실패하면 None을 반환한다."""

        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _require_aware(self, value: datetime) -> None:
        """timezone-aware datetime인지 검증한다."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")
