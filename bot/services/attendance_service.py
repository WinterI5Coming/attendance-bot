"""Attendance classification and check-in business rules."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import logging
from typing import Any
import uuid

import aiosqlite

from bot.policies.rank_policy import get_rank
from bot.policies.score_policy import get_attendance_score
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.excuse_repository import ExcuseRepository
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.services.session_service import SessionPrepareStatus, SessionService
from bot.services.streak_service import StreakService
from bot.utils.time_utils import get_server_today


logger = logging.getLogger(__name__)


class AttendanceTimeResult(Enum):
    """Result of comparing a time against an attendance session window.

    Values:
        NOT_OPEN: The current time is before the session start.
        PRESENT: The current time is within the on-time attendance window.
        LATE: The current time is within the late attendance window.
        CLOSED: The current time is at or after the close deadline.

    This enum is intentionally separate from database attendance record
    statuses. It contains window states such as NOT_OPEN and CLOSED that are
    useful for command behavior but are never stored as member attendance
    outcomes.
    """

    NOT_OPEN = "NOT_OPEN"
    PRESENT = "PRESENT"
    LATE = "LATE"
    CLOSED = "CLOSED"


def _require_aware_datetime(value: datetime, name: str) -> None:
    """Validate that a datetime includes usable timezone information.

    Args:
        value: Datetime to validate.
        name: Parameter name used in error messages.

    Raises:
        ValueError: If the value is naive or has an invalid timezone offset.
    """

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime.")


def classify_attendance(
    now: datetime,
    start_at: datetime,
    late_at: datetime,
    close_at: datetime,
) -> AttendanceTimeResult:
    """Classify the current time against an attendance session window.

    Args:
        now: Time to classify. It must be timezone-aware and is supplied by
            the caller so this pure function does not read the system clock.
        start_at: Time when on-time attendance opens. Must be timezone-aware.
        late_at: Time when late attendance begins. Must be timezone-aware.
        close_at: Time when attendance closes. Must be timezone-aware.

    Returns:
        AttendanceTimeResult.NOT_OPEN when now is before start_at,
        AttendanceTimeResult.PRESENT for start_at <= now < late_at,
        AttendanceTimeResult.LATE for late_at <= now < close_at, and
        AttendanceTimeResult.CLOSED for now >= close_at.

    Raises:
        ValueError: If any datetime is naive, or if the session window does not
            satisfy start_at < late_at < close_at.
    """

    _require_aware_datetime(now, "now")
    _require_aware_datetime(start_at, "start_at")
    _require_aware_datetime(late_at, "late_at")
    _require_aware_datetime(close_at, "close_at")

    if not start_at < late_at < close_at:
        raise ValueError("Attendance window must satisfy start_at < late_at < close_at.")

    # Python compares aware datetimes by absolute instant, so callers may pass
    # different timezone objects without normalizing them first.
    if now < start_at:
        return AttendanceTimeResult.NOT_OPEN

    if now < late_at:
        return AttendanceTimeResult.PRESENT

    if now < close_at:
        return AttendanceTimeResult.LATE

    return AttendanceTimeResult.CLOSED


class AttendanceCheckInStatus(Enum):
    """Possible outcomes of a user check-in attempt."""

    PRESENT = "PRESENT"
    LATE = "LATE"
    EXCUSED_LATE = "EXCUSED_LATE"
    ALREADY_CHECKED = "ALREADY_CHECKED"
    NOT_OPEN = "NOT_OPEN"
    CLOSED = "CLOSED"
    NOT_REGISTERED = "NOT_REGISTERED"
    NOT_SESSION_MEMBER = "NOT_SESSION_MEMBER"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_ATTENDANCE_DAY = "NOT_ATTENDANCE_DAY"
    NO_ACTIVE_MEMBERS = "NO_ACTIVE_MEMBERS"
    CANCELLED = "CANCELLED"


class AttendanceCorrectionStatus(Enum):
    """Possible outcomes for an administrator attendance correction."""

    UPDATED = "UPDATED"
    CREATED = "CREATED"
    SAME_STATUS = "SAME_STATUS"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    INVALID_DATE = "INVALID_DATE"
    FUTURE_DATE = "FUTURE_DATE"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    NOT_SESSION_MEMBER = "NOT_SESSION_MEMBER"
    INVALID_REASON = "INVALID_REASON"


@dataclass(frozen=True)
class AttendanceCheckInResult:
    """Result returned after attempting a user check-in.

    Attributes:
        status: Operational result for the command.
        attendance_status: Stored attendance status when a record exists.
        score_delta: Score delta created by this check-in.
        total_score: Current member total from ``score_events``.
        checked_at: UTC ISO 8601 check-in timestamp when available.
        start_at: UTC ISO 8601 session start.
        late_at: UTC ISO 8601 late threshold.
        close_at: UTC ISO 8601 close threshold.
        timezone_name: Guild timezone used for display.
        cancel_reason: Optional cancellation reason.
    """

    status: AttendanceCheckInStatus
    attendance_status: str | None = None
    score_delta: int | None = None
    total_score: int | None = None
    current_streak: int | None = None
    streak_bonus_delta: int = 0
    previous_rank: str | None = None
    current_rank: str | None = None
    rank_changed: bool = False
    checked_at: str | None = None
    start_at: str | None = None
    late_at: str | None = None
    close_at: str | None = None
    timezone_name: str | None = None
    cancel_reason: str | None = None


@dataclass(frozen=True)
class AttendanceCorrectionResult:
    """Result of an administrator attendance correction."""

    status: AttendanceCorrectionStatus
    target_discord_id: str | None = None
    attendance_date: str | None = None
    previous_status: str | None = None
    new_status: str | None = None
    score_delta: int = 0
    reason: str | None = None


@dataclass(frozen=True)
class AttendanceStatusMember:
    """One member row in today's attendance status report."""

    member_id: int
    discord_id: str
    display_name: str
    attendance_record_id: int | None
    attendance_status: str | None
    checked_at: str | None


@dataclass(frozen=True)
class AttendanceStatusResult:
    """Current session attendance status grouped by stored outcome.

    Attributes:
        status: Session preparation status.
        session: ``attendance_sessions`` row when one exists.
        timezone_name: Guild timezone used for display.
        attendance_date: Guild-local date string.
        cancel_reason: Optional cancellation reason.
        present: Members with PRESENT records.
        late: Members with LATE records.
        unchecked: Snapshot members with no attendance record.
        absent: Members with ABSENT records, if future workflows created them.
        excused_late: Members with EXCUSED_LATE records.
        excused_absent: Members with EXCUSED_ABSENT records.
    """

    status: SessionPrepareStatus
    session: dict[str, Any] | None = None
    timezone_name: str | None = None
    attendance_date: str | None = None
    cancel_reason: str | None = None
    present: list[AttendanceStatusMember] = field(default_factory=list)
    late: list[AttendanceStatusMember] = field(default_factory=list)
    unchecked: list[AttendanceStatusMember] = field(default_factory=list)
    absent: list[AttendanceStatusMember] = field(default_factory=list)
    excused_late: list[AttendanceStatusMember] = field(default_factory=list)
    excused_absent: list[AttendanceStatusMember] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        """Return the number of members in the session snapshot."""

        return (
            len(self.present)
            + len(self.late)
            + len(self.unchecked)
            + len(self.absent)
            + len(self.excused_late)
            + len(self.excused_absent)
        )

    @property
    def checked_count(self) -> int:
        """Return the number of members with any attendance record."""

        return self.total_count - len(self.unchecked)


class AttendanceService:
    """Handle user check-ins and attendance status lookups."""

    def __init__(
        self,
        *,
        member_repository: MemberRepository,
        session_repository: SessionRepository,
        attendance_repository: AttendanceRepository,
        score_repository: ScoreRepository,
        session_service: SessionService,
        guild_repository: GuildRepository | None = None,
        audit_repository: AuditRepository | None = None,
        excuse_repository: ExcuseRepository | None = None,
        streak_service: StreakService | None = None,
    ) -> None:
        """Create the service.

        Args:
            member_repository: Repository for member identity and active state.
            session_repository: Repository for session snapshots.
            attendance_repository: Repository for attendance records.
            score_repository: Repository for score ledger events.
            session_service: Service that prepares today's session.
            guild_repository: Optional repository for correction date context.
            audit_repository: Optional repository for correction audit logs.
        """

        self.member_repository = member_repository
        self.session_repository = session_repository
        self.attendance_repository = attendance_repository
        self.score_repository = score_repository
        self.session_service = session_service
        self.guild_repository = guild_repository
        self.audit_repository = audit_repository
        self.excuse_repository = excuse_repository
        self.streak_service = streak_service

    async def check_in(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
        now: datetime,
    ) -> AttendanceCheckInResult:
        """Check a registered member into today's attendance session.

        Args:
            guild_id: Discord guild ID.
            discord_id: Discord user ID.
            now: Current absolute time supplied by the caller. It must be
                timezone-aware.

        Returns:
            Check-in result. Expected user-facing states are returned as enum
            values instead of exceptions.

        Raises:
            ValueError: If ``now`` is naive or the configured session times are
                invalid.
            aiosqlite.Error: For unexpected database failures.
        """

        _require_aware_datetime(now, "now")

        guild_id_text = str(guild_id)
        discord_id_text = str(discord_id)

        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=discord_id_text,
        )

        if member is None or not member["is_active"]:
            return AttendanceCheckInResult(
                status=AttendanceCheckInStatus.NOT_REGISTERED,
            )

        prepared = await self.session_service.prepare_today_session(
            guild_id=guild_id_text,
            now=now,
        )

        if prepared.status is not SessionPrepareStatus.READY:
            return self._from_session_prepare_result(prepared)

        assert prepared.session is not None
        session = prepared.session

        if not await self.session_repository.is_session_member(
            session_id=int(session["id"]),
            member_id=int(member["id"]),
        ):
            return AttendanceCheckInResult(
                status=AttendanceCheckInStatus.NOT_SESSION_MEMBER,
                start_at=session["start_at"],
                late_at=session["late_at"],
                close_at=session["close_at"],
                timezone_name=prepared.timezone_name,
            )

        existing_record = await self.attendance_repository.get_by_session_and_member(
            session_id=int(session["id"]),
            member_id=int(member["id"]),
        )

        if existing_record is not None:
            total_score = await self.score_repository.get_total_score(
                member_id=int(member["id"]),
            )
            return AttendanceCheckInResult(
                status=AttendanceCheckInStatus.ALREADY_CHECKED,
                attendance_status=existing_record["status"],
                total_score=total_score,
                checked_at=existing_record["checked_at"],
                start_at=session["start_at"],
                late_at=session["late_at"],
                close_at=session["close_at"],
                timezone_name=prepared.timezone_name,
            )

        start_at = datetime.fromisoformat(session["start_at"])
        late_at = datetime.fromisoformat(session["late_at"])
        close_at = datetime.fromisoformat(session["close_at"])
        time_result = classify_attendance(
            now=now,
            start_at=start_at,
            late_at=late_at,
            close_at=close_at,
        )

        if time_result is AttendanceTimeResult.NOT_OPEN:
            return AttendanceCheckInResult(
                status=AttendanceCheckInStatus.NOT_OPEN,
                start_at=session["start_at"],
                late_at=session["late_at"],
                close_at=session["close_at"],
                timezone_name=prepared.timezone_name,
            )

        if time_result is AttendanceTimeResult.CLOSED:
            return AttendanceCheckInResult(
                status=AttendanceCheckInStatus.CLOSED,
                start_at=session["start_at"],
                late_at=session["late_at"],
                close_at=session["close_at"],
                timezone_name=prepared.timezone_name,
            )

        attendance_status = time_result.value
        excuse_request_id: int | None = None
        if self.excuse_repository is not None:
            excuse_request = await self.excuse_repository.get_effective_approved_request(
                guild_id=guild_id_text,
                member_id=int(member["id"]),
                target_date=session["attendance_date"],
            )
            if excuse_request is not None:
                excuse_request_id = int(excuse_request["id"])
                if attendance_status == "LATE":
                    attendance_status = "EXCUSED_LATE"

        return await self._create_record_and_score(
            guild_id=guild_id_text,
            session=session,
            member_id=int(member["id"]),
            attendance_status=attendance_status,
            excuse_request_id=excuse_request_id,
            checked_at=now.isoformat(),
            timezone_name=prepared.timezone_name,
        )

    async def get_today_status(
        self,
        *,
        guild_id: int | str,
        now: datetime,
    ) -> AttendanceStatusResult:
        """Return today's attendance snapshot grouped by outcome.

        Args:
            guild_id: Discord guild ID.
            now: Current absolute time supplied by the caller. It must be
                timezone-aware.

        Returns:
            Grouped attendance status result. If a closed/cancelled existing
            session is present, its historical status is still returned.

        Raises:
            ValueError: If ``now`` is naive or configured times are invalid.
            aiosqlite.Error: For unexpected database failures.
        """

        _require_aware_datetime(now, "now")

        prepared = await self.session_service.prepare_today_session(
            guild_id=str(guild_id),
            now=now,
        )

        if prepared.session is None:
            return AttendanceStatusResult(
                status=prepared.status,
                timezone_name=prepared.timezone_name,
                attendance_date=prepared.attendance_date,
                cancel_reason=prepared.cancel_reason,
            )

        rows = await self.session_repository.list_members_with_attendance(
            session_id=int(prepared.session["id"]),
        )

        groups: dict[str | None, list[AttendanceStatusMember]] = {
            "PRESENT": [],
            "LATE": [],
            None: [],
            "ABSENT": [],
            "EXCUSED_LATE": [],
            "EXCUSED_ABSENT": [],
        }

        for row in rows:
            member = AttendanceStatusMember(
                member_id=int(row["member_id"]),
                discord_id=row["discord_id"],
                display_name=row["display_name"],
                attendance_record_id=row["attendance_record_id"],
                attendance_status=row["attendance_status"],
                checked_at=row["checked_at"],
            )
            groups.setdefault(row["attendance_status"], []).append(member)

        return AttendanceStatusResult(
            status=prepared.status,
            session=prepared.session,
            timezone_name=prepared.timezone_name,
            attendance_date=prepared.attendance_date,
            cancel_reason=prepared.cancel_reason,
            present=groups["PRESENT"],
            late=groups["LATE"],
            unchecked=groups[None],
            absent=groups["ABSENT"],
            excused_late=groups["EXCUSED_LATE"],
            excused_absent=groups["EXCUSED_ABSENT"],
        )

    async def correct_attendance(
        self,
        *,
        guild_id: int | str,
        target_discord_id: int | str,
        attendance_date: str,
        new_status: str,
        reason: str,
        actor_discord_id: int | str,
        now: datetime,
    ) -> AttendanceCorrectionResult:
        """Create or update an attendance record as an administrator correction.

        Args:
            guild_id: Discord guild ID.
            target_discord_id: Target user's Discord ID.
            attendance_date: Guild-local YYYY-MM-DD date.
            new_status: PRESENT, LATE, or ABSENT.
            reason: Correction reason, 2 to 500 chars after trimming.
            actor_discord_id: Administrator/officer Discord ID.
            now: Current timezone-aware UTC time.

        Returns:
            Correction result. Expected validation failures are returned as
            statuses rather than exceptions.

        Raises:
            RuntimeError: If correction dependencies were not wired.
            aiosqlite.Error: For unexpected database failures.
        """

        _require_aware_datetime(now, "now")

        if self.guild_repository is None or self.audit_repository is None:
            raise RuntimeError("Attendance correction dependencies are not configured.")

        cleaned_reason = reason.strip()
        if len(cleaned_reason) < 2 or len(cleaned_reason) > 500:
            return AttendanceCorrectionResult(
                status=AttendanceCorrectionStatus.INVALID_REASON,
                attendance_date=attendance_date,
                new_status=new_status,
                reason=cleaned_reason,
            )

        if new_status not in {"PRESENT", "LATE", "ABSENT"}:
            return AttendanceCorrectionResult(
                status=AttendanceCorrectionStatus.INVALID_DATE,
                attendance_date=attendance_date,
                new_status=new_status,
                reason=cleaned_reason,
            )

        try:
            parsed_date = datetime.strptime(attendance_date, "%Y-%m-%d").date()
        except ValueError:
            return AttendanceCorrectionResult(
                status=AttendanceCorrectionStatus.INVALID_DATE,
                attendance_date=attendance_date,
                new_status=new_status,
                reason=cleaned_reason,
            )

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return AttendanceCorrectionResult(
                status=AttendanceCorrectionStatus.NOT_CONFIGURED,
                attendance_date=attendance_date,
            )

        if parsed_date > get_server_today(now, settings["timezone"]):
            return AttendanceCorrectionResult(
                status=AttendanceCorrectionStatus.FUTURE_DATE,
                attendance_date=attendance_date,
                new_status=new_status,
                reason=cleaned_reason,
            )

        session = await self.session_repository.get_by_guild_and_date(
            guild_id=guild_id_text,
            attendance_date=attendance_date,
        )
        if session is None:
            return AttendanceCorrectionResult(
                status=AttendanceCorrectionStatus.SESSION_NOT_FOUND,
                attendance_date=attendance_date,
                new_status=new_status,
                reason=cleaned_reason,
            )

        target_member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=str(target_discord_id),
        )
        if target_member is None:
            return AttendanceCorrectionResult(
                status=AttendanceCorrectionStatus.TARGET_NOT_FOUND,
                attendance_date=attendance_date,
                target_discord_id=str(target_discord_id),
            )

        if not await self.session_repository.is_session_member(
            session_id=int(session["id"]),
            member_id=int(target_member["id"]),
        ):
            return AttendanceCorrectionResult(
                status=AttendanceCorrectionStatus.NOT_SESSION_MEMBER,
                attendance_date=attendance_date,
                target_discord_id=str(target_discord_id),
            )

        return await self._apply_attendance_correction(
            guild_id=guild_id_text,
            session=session,
            member_id=int(target_member["id"]),
            target_discord_id=str(target_discord_id),
            attendance_date=attendance_date,
            new_status=new_status,
            reason=cleaned_reason,
            actor_discord_id=str(actor_discord_id),
            now=now,
        )

    async def _create_record_and_score(
        self,
        *,
        guild_id: str,
        session: dict[str, Any],
        member_id: int,
        attendance_status: str,
        excuse_request_id: int | None,
        checked_at: str,
        timezone_name: str | None,
    ) -> AttendanceCheckInResult:
        """Create attendance and score rows in one transaction.

        Args:
            guild_id: Discord guild ID stored as text.
            session: Prepared ``attendance_sessions`` row.
            member_id: ``members.id`` checking in.
            attendance_status: PRESENT or LATE.
            checked_at: UTC ISO 8601 timestamp.
            timezone_name: Guild timezone used for display.

        Returns:
            Check-in result for the created or concurrently existing record.

        Raises:
            aiosqlite.Error: For unexpected database failures.
        """

        connection = await self.attendance_repository.database.connect()

        try:
            await connection.execute("BEGIN IMMEDIATE;")
            existing_record = await self.attendance_repository.get_by_session_and_member(
                session_id=int(session["id"]),
                member_id=member_id,
                connection=connection,
            )

            if existing_record is not None:
                total_score = await self.score_repository.get_total_score(
                    member_id=member_id,
                    connection=connection,
                )
                await connection.rollback()
                return AttendanceCheckInResult(
                    status=AttendanceCheckInStatus.ALREADY_CHECKED,
                    attendance_status=existing_record["status"],
                    total_score=total_score,
                    checked_at=existing_record["checked_at"],
                    start_at=session["start_at"],
                    late_at=session["late_at"],
                    close_at=session["close_at"],
                    timezone_name=timezone_name,
                )

            previous_total = await self.score_repository.get_total_score(
                member_id=member_id,
                connection=connection,
            )
            previous_rank = get_rank(previous_total)

            record = await self.attendance_repository.create_user_record(
                session_id=int(session["id"]),
                member_id=member_id,
                status=attendance_status,
                checked_at=checked_at,
                connection=connection,
            )
            if excuse_request_id is not None:
                await self.attendance_repository.set_excuse_request(
                    attendance_record_id=int(record["id"]),
                    excuse_request_id=excuse_request_id,
                    connection=connection,
                )
                record = await self.attendance_repository.get_by_session_and_member(
                    session_id=int(session["id"]),
                    member_id=member_id,
                    connection=connection,
                )
                assert record is not None
            score_delta = get_attendance_score(attendance_status)
            descriptions = {
                "PRESENT": "정상 출석",
                "LATE": "지각",
                "EXCUSED_LATE": "사유 지각",
            }
            description = descriptions[attendance_status]

            # Attendance and score must be atomic: the command should never
            # leave a checked-in member without points, or points without the
            # attendance record that explains them.
            await self.score_repository.create_attendance_event(
                guild_id=guild_id,
                member_id=member_id,
                attendance_record_id=int(record["id"]),
                attendance_status=attendance_status,
                delta=score_delta,
                description=description,
                created_at=checked_at,
                connection=connection,
            )
            streak_result = None
            if self.streak_service is not None and attendance_status in {
                "PRESENT",
                "LATE",
                "EXCUSED_LATE",
            }:
                streak_result = await self.streak_service.apply_bonus_if_needed(
                    guild_id=guild_id,
                    member_id=member_id,
                    session_id=int(session["id"]),
                    created_at=checked_at,
                    connection=connection,
                )
            total_score = await self.score_repository.get_total_score(
                member_id=member_id,
                connection=connection,
            )
            current_rank = get_rank(total_score)
            await connection.commit()

            return AttendanceCheckInResult(
                status=AttendanceCheckInStatus[attendance_status],
                attendance_status=attendance_status,
                score_delta=score_delta,
                total_score=total_score,
                current_streak=(
                    None if streak_result is None else streak_result.current_streak
                ),
                streak_bonus_delta=(
                    0 if streak_result is None else streak_result.bonus_delta
                ),
                previous_rank=previous_rank,
                current_rank=current_rank,
                rank_changed=previous_rank != current_rank,
                checked_at=checked_at,
                start_at=session["start_at"],
                late_at=session["late_at"],
                close_at=session["close_at"],
                timezone_name=timezone_name,
            )
        except aiosqlite.IntegrityError:
            await connection.rollback()
            existing_record = await self.attendance_repository.get_by_session_and_member(
                session_id=int(session["id"]),
                member_id=member_id,
            )
            if existing_record is None:
                raise

            total_score = await self.score_repository.get_total_score(
                member_id=member_id,
            )
            return AttendanceCheckInResult(
                status=AttendanceCheckInStatus.ALREADY_CHECKED,
                attendance_status=existing_record["status"],
                total_score=total_score,
                checked_at=existing_record["checked_at"],
                start_at=session["start_at"],
                late_at=session["late_at"],
                close_at=session["close_at"],
                timezone_name=timezone_name,
            )
        except Exception:
            await connection.rollback()
            logger.exception(
                "Attendance check-in transaction failed: guild_id=%s session_id=%s member_id=%s",
                guild_id,
                session["id"],
                member_id,
            )
            raise
        finally:
            await connection.close()

    async def _apply_attendance_correction(
        self,
        *,
        guild_id: str,
        session: dict[str, Any],
        member_id: int,
        target_discord_id: str,
        attendance_date: str,
        new_status: str,
        reason: str,
        actor_discord_id: str,
        now: datetime,
    ) -> AttendanceCorrectionResult:
        """Apply correction rows in one transaction."""

        assert self.audit_repository is not None
        now_text = now.isoformat()
        connection = await self.attendance_repository.database.connect()

        try:
            await connection.execute("BEGIN IMMEDIATE;")
            existing = await self.attendance_repository.get_by_session_and_member(
                session_id=int(session["id"]),
                member_id=member_id,
                connection=connection,
            )

            if existing is not None and existing["status"] == new_status:
                await connection.rollback()
                return AttendanceCorrectionResult(
                    status=AttendanceCorrectionStatus.SAME_STATUS,
                    target_discord_id=target_discord_id,
                    attendance_date=attendance_date,
                    previous_status=existing["status"],
                    new_status=new_status,
                    reason=reason,
                )

            if existing is None:
                checked_at = now_text if new_status in {"PRESENT", "LATE"} else None
                record = await self.attendance_repository.create_admin_record(
                    session_id=int(session["id"]),
                    member_id=member_id,
                    status=new_status,
                    checked_at=checked_at,
                    note=reason,
                    now=now_text,
                    connection=connection,
                )
                score_delta = get_attendance_score(new_status)
                before_json = None
                description = f"출석 기록 생성: {new_status}"
                result_status = AttendanceCorrectionStatus.CREATED
            else:
                checked_at = existing["checked_at"]
                if new_status == "ABSENT":
                    checked_at = None
                elif existing["status"] == "ABSENT" and new_status in {"PRESENT", "LATE"}:
                    checked_at = now_text

                before_json = json.dumps(
                    {
                        "status": existing["status"],
                        "source": existing["source"],
                        "checked_at": existing["checked_at"],
                    },
                    ensure_ascii=False,
                )
                await self.attendance_repository.update_admin_record(
                    attendance_record_id=int(existing["id"]),
                    status=new_status,
                    checked_at=checked_at,
                    note=reason,
                    now=now_text,
                    connection=connection,
                )
                record = await self.attendance_repository.get_by_session_and_member(
                    session_id=int(session["id"]),
                    member_id=member_id,
                    connection=connection,
                )
                assert record is not None
                score_delta = get_attendance_score(new_status) - get_attendance_score(
                    existing["status"]
                )
                description = f"출석 정정: {existing['status']} → {new_status}"
                result_status = AttendanceCorrectionStatus.UPDATED

            after_json = json.dumps(
                {
                    "status": new_status,
                    "source": "ADMIN",
                    "checked_at": record["checked_at"],
                },
                ensure_ascii=False,
            )

            if score_delta != 0:
                await self.score_repository.create_correction_event(
                    guild_id=guild_id,
                    member_id=member_id,
                    attendance_record_id=int(record["id"]),
                    delta=score_delta,
                    dedup_key=f"correction:{record['id']}:{uuid.uuid4()}",
                    description=description,
                    created_by_discord_id=actor_discord_id,
                    created_at=now_text,
                    connection=connection,
                )

            # Attendance correction, score compensation, and audit evidence
            # must commit together so administrators never create invisible or
            # partially compensated changes.
            await self.audit_repository.create_log(
                guild_id=guild_id,
                actor_discord_id=actor_discord_id,
                action_type="ATTENDANCE_CORRECTED",
                target_type="ATTENDANCE",
                target_id=str(record["id"]),
                before_json=before_json,
                after_json=after_json,
                reason=reason,
                created_at=now_text,
                connection=connection,
            )
            await connection.commit()

            logger.info(
                "Attendance corrected: guild_id=%s actor_id=%s target_id=%s record_id=%s delta=%s",
                guild_id,
                actor_discord_id,
                target_discord_id,
                record["id"],
                score_delta,
            )
            return AttendanceCorrectionResult(
                status=result_status,
                target_discord_id=target_discord_id,
                attendance_date=attendance_date,
                previous_status=None if existing is None else existing["status"],
                new_status=new_status,
                score_delta=score_delta,
                reason=reason,
            )
        except Exception:
            await connection.rollback()
            logger.exception(
                "Attendance correction transaction failed: guild_id=%s target_id=%s",
                guild_id,
                target_discord_id,
            )
            raise
        finally:
            await connection.close()

    def _from_session_prepare_result(
        self,
        prepared: Any,
    ) -> AttendanceCheckInResult:
        """Translate session preparation status into check-in status.

        Args:
            prepared: ``SessionPrepareResult`` returned by SessionService.

        Returns:
            Check-in result preserving timing and cancellation context.
        """

        mapping = {
            SessionPrepareStatus.NOT_CONFIGURED: AttendanceCheckInStatus.NOT_CONFIGURED,
            SessionPrepareStatus.NOT_ATTENDANCE_DAY: (
                AttendanceCheckInStatus.NOT_ATTENDANCE_DAY
            ),
            SessionPrepareStatus.NO_ACTIVE_MEMBERS: AttendanceCheckInStatus.NO_ACTIVE_MEMBERS,
            SessionPrepareStatus.ALREADY_CLOSED: AttendanceCheckInStatus.CLOSED,
            SessionPrepareStatus.CANCELLED: AttendanceCheckInStatus.CANCELLED,
        }
        session = prepared.session

        return AttendanceCheckInResult(
            status=mapping[prepared.status],
            start_at=None if session is None else session["start_at"],
            late_at=None if session is None else session["late_at"],
            close_at=None if session is None else session["close_at"],
            timezone_name=prepared.timezone_name,
            cancel_reason=prepared.cancel_reason,
        )
