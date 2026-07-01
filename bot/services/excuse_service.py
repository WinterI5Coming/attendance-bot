"""Business rules for excuse requests and approvals."""

from dataclasses import dataclass
from datetime import datetime
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
from bot.utils.time_utils import (
    build_session_window,
    get_server_today,
    get_weekday_code,
    parse_attendance_days,
    parse_hhmm,
)


logger = logging.getLogger(__name__)


class ExcuseStatus(Enum):
    """Expected outcomes for excuse commands."""

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


@dataclass(frozen=True)
class ExcuseResult:
    """Result returned by excuse service methods."""

    status: ExcuseStatus
    request: dict[str, Any] | None = None
    attendance_record: dict[str, Any] | None = None
    score_delta: int = 0


class ExcuseService:
    """Validate and mutate excuse requests."""

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
        """Create the service."""

        self.guild_repository = guild_repository
        self.member_repository = member_repository
        self.session_repository = session_repository
        self.attendance_repository = attendance_repository
        self.score_repository = score_repository
        self.excuse_repository = excuse_repository
        self.audit_repository = audit_repository

    async def create_request(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
        target_date: str,
        expected_time: str | None,
        reason: str,
        now: datetime,
    ) -> ExcuseResult:
        """Create an excuse request for an active member."""

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

        if expected_time:
            try:
                parse_hhmm(expected_time)
            except ValueError:
                return ExcuseResult(status=ExcuseStatus.INVALID_TIME)

        cleaned_reason = reason.strip()
        if len(cleaned_reason) < 2 or len(cleaned_reason) > 500:
            return ExcuseResult(status=ExcuseStatus.INVALID_REASON)

        window = build_session_window(
            attendance_date=parsed_date,
            attendance_start=settings["attendance_start"],
            late_deadline=settings["late_deadline"],
            close_deadline=settings["close_deadline"],
            timezone_name=settings["timezone"],
        )
        if now >= window.start_at:
            return ExcuseResult(status=ExcuseStatus.TOO_LATE_TO_REQUEST)

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

        status = (
            "AUTO_APPROVED"
            if settings["excuse_mode"] == "auto"
            else "PENDING"
        )
        try:
            request = await self.excuse_repository.create(
                guild_id=guild_id_text,
                member_id=int(member["id"]),
                target_date=target_date,
                reason=cleaned_reason,
                expected_time=expected_time,
                status=status,
                requested_at=now.isoformat(),
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
        )

    async def cancel_request(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
        excuse_request_id: int,
        now: datetime,
    ) -> ExcuseResult:
        """Cancel the caller's active excuse request."""

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

        if request["status"] not in {"PENDING", "APPROVED", "AUTO_APPROVED"}:
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
        """Approve a pending excuse request and reconcile existing attendance."""

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
        """Reject a pending excuse request."""

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
        """List excuse requests visible to a user."""

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
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _require_aware(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")
