"""Business rules for preparing today's attendance session."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import logging
from typing import Any

import aiosqlite

from bot.policies.score_policy import get_attendance_score
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
)


logger = logging.getLogger(__name__)


class SessionPrepareStatus(Enum):
    """Possible outcomes for preparing today's attendance session."""

    READY = "READY"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_ATTENDANCE_DAY = "NOT_ATTENDANCE_DAY"
    NO_ACTIVE_MEMBERS = "NO_ACTIVE_MEMBERS"
    ALREADY_CLOSED = "ALREADY_CLOSED"
    CANCELLED = "CANCELLED"


class SessionCloseStatus(Enum):
    """Possible outcomes for closing an attendance session."""

    CLOSED = "CLOSED"
    ALREADY_CLOSED = "ALREADY_CLOSED"
    CANCELLED = "CANCELLED"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class SessionPrepareResult:
    """Result of preparing a guild's attendance session for today.

    Attributes:
        status: Operational outcome. Expected states are represented here
            instead of exceptions.
        session: ``attendance_sessions`` row when one is available.
        cancel_reason: Cancellation reason for CANCELLED sessions.
        timezone_name: Guild timezone used for date and display conversion.
        attendance_date: Guild-local attendance date in YYYY-MM-DD format.
    """

    status: SessionPrepareStatus
    session: dict[str, Any] | None = None
    cancel_reason: str | None = None
    timezone_name: str | None = None
    attendance_date: str | None = None


@dataclass(frozen=True)
class SessionCloseResult:
    """Result of attempting to close one session.

    Attributes:
        status: Close outcome.
        session_id: attendance_sessions.id.
        guild_id: Discord guild ID when known.
        newly_absent_count: Count of ABSENT rows created in this close call.
        already_recorded_count: Members that already had records.
        closed_at: UTC ISO 8601 close timestamp if this call closed the session.
    """

    status: SessionCloseStatus
    session_id: int
    guild_id: str | None = None
    newly_absent_count: int = 0
    already_recorded_count: int = 0
    closed_at: str | None = None


@dataclass(frozen=True)
class RecoveryResult:
    """Summary of processing overdue sessions after restart or scheduler ticks."""

    processed_sessions: int = 0
    newly_absent_count: int = 0
    already_closed_count: int = 0
    cancelled_count: int = 0
    failed_sessions: int = 0


class SessionService:
    """Prepare attendance sessions using guild settings and active members."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        member_repository: MemberRepository,
        session_repository: SessionRepository,
        attendance_repository: AttendanceRepository | None = None,
        score_repository: ScoreRepository | None = None,
        excuse_repository: ExcuseRepository | None = None,
    ) -> None:
        """Create the service.

        Args:
            guild_repository: Repository for ``guild_settings``.
            member_repository: Repository for active member lookups.
            session_repository: Repository for sessions and snapshots.
            attendance_repository: Repository for attendance records, required
                by close/recovery operations.
            score_repository: Repository for score events, required by
                close/recovery operations.
        """

        self.guild_repository = guild_repository
        self.member_repository = member_repository
        self.session_repository = session_repository
        self.attendance_repository = attendance_repository
        self.score_repository = score_repository
        self.excuse_repository = excuse_repository

    async def prepare_today_session(
        self,
        *,
        guild_id: int | str,
        now: datetime,
    ) -> SessionPrepareResult:
        """Fetch or create today's attendance session for a guild.

        Args:
            guild_id: Discord guild ID.
            now: Current absolute time, supplied by caller for deterministic
                tests. It must be timezone-aware.

        Returns:
            Session preparation result. READY includes a usable session unless
            an unexpected database error is raised.

        Raises:
            ValueError: If ``now`` is naive or guild time settings are invalid.
            aiosqlite.Error: For unexpected database failures.
        """

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)

        if settings is None:
            return SessionPrepareResult(
                status=SessionPrepareStatus.NOT_CONFIGURED,
            )

        timezone_name = settings["timezone"]
        local_date = get_server_today(now, timezone_name)
        attendance_date = local_date.isoformat()

        existing = await self.session_repository.get_by_guild_and_date(
            guild_id=guild_id_text,
            attendance_date=attendance_date,
        )

        if existing is not None:
            return await self._prepare_existing_session(
                session=existing,
                now=now,
                timezone_name=timezone_name,
                attendance_date=attendance_date,
            )

        attendance_days = parse_attendance_days(settings["attendance_days"])
        if get_weekday_code(local_date) not in attendance_days:
            return SessionPrepareResult(
                status=SessionPrepareStatus.NOT_ATTENDANCE_DAY,
                timezone_name=timezone_name,
                attendance_date=attendance_date,
            )

        window = build_session_window(
            attendance_date=local_date,
            attendance_start=settings["attendance_start"],
            late_deadline=settings["late_deadline"],
            close_deadline=settings["close_deadline"],
            timezone_name=timezone_name,
        )

        if now >= window.close_at:
            return SessionPrepareResult(
                status=SessionPrepareStatus.ALREADY_CLOSED,
                timezone_name=timezone_name,
                attendance_date=attendance_date,
            )

        active_members = await self.member_repository.list_active_with_ids(
            guild_id=guild_id_text,
        )

        if not active_members:
            return SessionPrepareResult(
                status=SessionPrepareStatus.NO_ACTIVE_MEMBERS,
                timezone_name=timezone_name,
                attendance_date=attendance_date,
            )

        status = "OPEN" if now >= window.start_at else "SCHEDULED"
        now_text = now.isoformat()

        try:
            session = await self.session_repository.create_with_members(
                guild_id=guild_id_text,
                attendance_date=attendance_date,
                start_at=window.start_at.isoformat(),
                late_at=window.late_at.isoformat(),
                close_at=window.close_at.isoformat(),
                status=status,
                opened_at=now_text if status == "OPEN" else None,
                member_ids=[
                    int(member["id"])
                    for member in active_members
                ],
                now=now_text,
            )
        except aiosqlite.IntegrityError:
            # Concurrent /출석 calls can both try to create today's session.
            # UNIQUE(guild_id, attendance_date) lets the first request win;
            # the loser rolls back and returns the session that already exists.
            logger.info(
                "Concurrent attendance session creation recovered: guild_id=%s date=%s",
                guild_id_text,
                attendance_date,
            )
            session = await self.session_repository.get_by_guild_and_date(
                guild_id=guild_id_text,
                attendance_date=attendance_date,
            )
            if session is None:
                raise

        return SessionPrepareResult(
            status=SessionPrepareStatus.READY,
            session=session,
            timezone_name=timezone_name,
            attendance_date=attendance_date,
        )

    async def close_session(
        self,
        *,
        session_id: int,
        now: datetime,
    ) -> SessionCloseResult:
        """Close a session and create ABSENT records for unchecked members.

        Args:
            session_id: attendance_sessions.id.
            now: Current timezone-aware UTC time supplied by caller.

        Returns:
            Close result. Expected repeated/cancelled/closed states are not
            raised as exceptions.

        Raises:
            RuntimeError: If repositories required for closing were not wired.
            ValueError: If ``now`` is naive.
            aiosqlite.Error: For unexpected DB failures.
        """

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")
        if self.attendance_repository is None or self.score_repository is None:
            raise RuntimeError("SessionService close dependencies are not configured.")

        now_text = now.isoformat()
        connection = await self.session_repository.database.connect()

        try:
            await connection.execute("BEGIN IMMEDIATE;")
            session = await self.session_repository.get_by_id(
                session_id=session_id,
                connection=connection,
            )

            if session is None:
                await connection.rollback()
                return SessionCloseResult(
                    status=SessionCloseStatus.NOT_FOUND,
                    session_id=session_id,
                )

            if session["status"] == "CANCELLED":
                await connection.rollback()
                return SessionCloseResult(
                    status=SessionCloseStatus.CANCELLED,
                    session_id=session_id,
                    guild_id=session["guild_id"],
                )

            if session["status"] == "CLOSED":
                await connection.rollback()
                return SessionCloseResult(
                    status=SessionCloseStatus.ALREADY_CLOSED,
                    session_id=session_id,
                    guild_id=session["guild_id"],
                    closed_at=session["closed_at"],
                )

            unchecked_members = await self.session_repository.list_unchecked_members(
                session_id=session_id,
                connection=connection,
            )
            all_members = await connection.execute_fetchall(
                """
                SELECT COUNT(*) AS count
                FROM attendance_session_members
                WHERE session_id = ?;
                """,
                (session_id,),
            )
            already_recorded_count = int(all_members[0]["count"]) - len(unchecked_members)
            # The whole close runs in one transaction so a session cannot be
            # half closed: every ABSENT record, its -3 ledger event, and the
            # CLOSED status are committed or rolled back together.
            for member in unchecked_members:
                attendance_status = "ABSENT"
                excuse_request_id = None
                if self.excuse_repository is not None:
                    excuse_request = await self.excuse_repository.get_effective_approved_request(
                        guild_id=session["guild_id"],
                        member_id=int(member["member_id"]),
                        target_date=session["attendance_date"],
                        connection=connection,
                    )
                    if excuse_request is not None:
                        attendance_status = "EXCUSED_ABSENT"
                        excuse_request_id = int(excuse_request["id"])

                record = await self.attendance_repository.create_auto_absent_record(
                    session_id=session_id,
                    member_id=int(member["member_id"]),
                    status=attendance_status,
                    excuse_request_id=excuse_request_id,
                    now=now_text,
                    connection=connection,
                )
                score_delta = get_attendance_score(attendance_status)
                await self.score_repository.create_attendance_event(
                    guild_id=session["guild_id"],
                    member_id=int(member["member_id"]),
                    attendance_record_id=int(record["id"]),
                    attendance_status=attendance_status,
                    delta=score_delta,
                    description=(
                        "사유 결석" if attendance_status == "EXCUSED_ABSENT" else "결석"
                    ),
                    created_at=now_text,
                    connection=connection,
                )

            await self.session_repository.close_session(
                session_id=session_id,
                now=now_text,
                connection=connection,
            )
            await connection.commit()

            logger.info(
                "Attendance session closed: guild_id=%s session_id=%s absent=%s",
                session["guild_id"],
                session_id,
                len(unchecked_members),
            )
            return SessionCloseResult(
                status=SessionCloseStatus.CLOSED,
                session_id=session_id,
                guild_id=session["guild_id"],
                newly_absent_count=len(unchecked_members),
                already_recorded_count=already_recorded_count,
                closed_at=now_text,
            )
        except Exception:
            await connection.rollback()
            logger.exception("Attendance session close failed: session_id=%s", session_id)
            raise
        finally:
            await connection.close()

    async def process_overdue_sessions(
        self,
        *,
        now: datetime,
    ) -> RecoveryResult:
        """Close all overdue sessions using the same close logic.

        Args:
            now: Current timezone-aware UTC time.

        Returns:
            Summary of processed overdue sessions.

        Notes:
            Restart recovery deliberately reuses ``close_session`` so retry,
            duplicate prevention, and transaction behavior stay identical to
            the regular scheduler close path.
        """

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")

        sessions = await self.session_repository.list_overdue_sessions(
            now=now.isoformat(),
        )
        processed = 0
        absent = 0
        already = 0
        cancelled = 0
        failed = 0

        logger.info("Overdue attendance recovery started: sessions=%s", len(sessions))

        for session in sessions:
            try:
                result = await self.close_session(
                    session_id=int(session["id"]),
                    now=now,
                )
            except Exception:
                failed += 1
                logger.exception(
                    "Overdue attendance recovery failed: session_id=%s",
                    session["id"],
                )
                continue

            processed += 1
            absent += result.newly_absent_count
            if result.status is SessionCloseStatus.ALREADY_CLOSED:
                already += 1
            elif result.status is SessionCloseStatus.CANCELLED:
                cancelled += 1

        logger.info(
            "Overdue attendance recovery completed: processed=%s absent=%s failed=%s",
            processed,
            absent,
            failed,
        )
        return RecoveryResult(
            processed_sessions=processed,
            newly_absent_count=absent,
            already_closed_count=already,
            cancelled_count=cancelled,
            failed_sessions=failed,
        )

    async def _prepare_existing_session(
        self,
        *,
        session: dict[str, Any],
        now: datetime,
        timezone_name: str,
        attendance_date: str,
    ) -> SessionPrepareResult:
        """Apply current-time rules to an existing session.

        Args:
            session: Existing ``attendance_sessions`` row.
            now: Current absolute time, supplied by caller.
            timezone_name: Guild timezone for display/date context.
            attendance_date: Guild-local attendance date.

        Returns:
            Session preparation result.
        """

        close_at = datetime.fromisoformat(session["close_at"])

        if session["status"] == "CANCELLED":
            return SessionPrepareResult(
                status=SessionPrepareStatus.CANCELLED,
                session=session,
                cancel_reason=session["cancel_reason"],
                timezone_name=timezone_name,
                attendance_date=attendance_date,
            )

        if session["status"] == "CLOSED":
            return SessionPrepareResult(
                status=SessionPrepareStatus.ALREADY_CLOSED,
                session=session,
                timezone_name=timezone_name,
                attendance_date=attendance_date,
            )

        if now >= close_at:
            return SessionPrepareResult(
                status=SessionPrepareStatus.ALREADY_CLOSED,
                session=session,
                timezone_name=timezone_name,
                attendance_date=attendance_date,
            )

        if session["status"] == "SCHEDULED":
            start_at = datetime.fromisoformat(session["start_at"])
            if now >= start_at:
                opened_session = await self.session_repository.open_scheduled_session(
                    session_id=int(session["id"]),
                    now=now.isoformat(),
                )
                if opened_session is not None:
                    session = opened_session

        return SessionPrepareResult(
            status=SessionPrepareStatus.READY,
            session=session,
            timezone_name=timezone_name,
            attendance_date=attendance_date,
        )
