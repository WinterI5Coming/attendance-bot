"""Administrator settings and session-control business rules."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bot.config import ALLOWED_ATTENDANCE_DAYS, ALLOWED_EXCUSE_MODES
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.utils.time_utils import build_session_window, get_server_today, parse_hhmm


class SettingsUpdateStatus(Enum):
    """Expected settings update outcomes."""

    UPDATED = "UPDATED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_FIELD = "INVALID_FIELD"
    INVALID_VALUE = "INVALID_VALUE"
    INVALID_TIME_ORDER = "INVALID_TIME_ORDER"


class SessionControlStatus(Enum):
    """Expected outcomes for today's session cancel/resume workflows."""

    CANCELLED = "CANCELLED"
    RESUMED = "RESUMED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NO_SESSION = "NO_SESSION"
    NOT_ATTENDANCE_DAY = "NOT_ATTENDANCE_DAY"
    INVALID_REASON = "INVALID_REASON"
    INVALID_TIME = "INVALID_TIME"
    CLOSED = "CLOSED"
    ALREADY_CANCELLED = "ALREADY_CANCELLED"
    NOT_CANCELLED = "NOT_CANCELLED"
    CLOSE_ALREADY_PASSED = "CLOSE_ALREADY_PASSED"
    PERMISSION_DENIED = "PERMISSION_DENIED"


@dataclass(frozen=True)
class SettingsUpdateResult:
    """Result of a guild setting update."""

    status: SettingsUpdateStatus
    field: str | None = None
    value: str | None = None
    before_value: str | None = None
    after_value: str | None = None


@dataclass(frozen=True)
class SessionControlResult:
    """Result of cancelling or resuming today's attendance session."""

    status: SessionControlStatus
    session_id: int | None = None
    attendance_date: str | None = None
    score_event_count: int = 0
    reason: str | None = None


class AdminService:
    """Handle Phase 3 administrator operations."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        session_repository: SessionRepository,
        score_repository: ScoreRepository,
        audit_repository: AuditRepository,
    ) -> None:
        self.guild_repository = guild_repository
        self.session_repository = session_repository
        self.score_repository = score_repository
        self.audit_repository = audit_repository

    async def update_setting(
        self,
        *,
        guild_id: int | str,
        field: str,
        value: str,
        actor_discord_id: int | str,
        has_permission: bool,
        now: datetime,
    ) -> SettingsUpdateResult:
        """Validate and update one guild setting with an audit log."""

        self._require_aware(now)
        if not has_permission:
            return SettingsUpdateResult(status=SettingsUpdateStatus.PERMISSION_DENIED)

        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return SettingsUpdateResult(status=SettingsUpdateStatus.NOT_CONFIGURED)

        normalized = self._normalize_setting(field, value, settings)
        if normalized.status is not SettingsUpdateStatus.UPDATED:
            return normalized

        assert normalized.field is not None
        assert normalized.value is not None
        now_text = now.isoformat()
        connection = await self.guild_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            await self.guild_repository.update_settings(
                guild_id=guild_id_text,
                fields={normalized.field: normalized.value},
                now=now_text,
                connection=connection,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id_text,
                actor_discord_id=str(actor_discord_id),
                action_type="GUILD_SETTINGS_UPDATED",
                target_type="SETTING",
                target_id=normalized.field,
                before_json=json.dumps(
                    {normalized.field: normalized.before_value},
                    ensure_ascii=False,
                ),
                after_json=json.dumps(
                    {normalized.field: normalized.value},
                    ensure_ascii=False,
                ),
                reason=f"setting update: {normalized.field}",
                created_at=now_text,
                connection=connection,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        return normalized

    async def cancel_today_session(
        self,
        *,
        guild_id: int | str,
        reason: str,
        actor_discord_id: int | str,
        has_permission: bool,
        now: datetime,
    ) -> SessionControlResult:
        """Cancel today's SCHEDULED or OPEN session and reverse attendance points."""

        self._require_aware(now)
        if not has_permission:
            return SessionControlResult(status=SessionControlStatus.PERMISSION_DENIED)
        cleaned_reason = reason.strip()
        if len(cleaned_reason) < 2 or len(cleaned_reason) > 500:
            return SessionControlResult(status=SessionControlStatus.INVALID_REASON)

        located = await self._locate_today_session(guild_id, now)
        if isinstance(located, SessionControlResult):
            return located
        settings, session = located
        if session["status"] == "CLOSED":
            return SessionControlResult(status=SessionControlStatus.CLOSED)
        if session["status"] == "CANCELLED":
            return SessionControlResult(status=SessionControlStatus.ALREADY_CANCELLED)

        guild_id_text = str(guild_id)
        now_text = now.isoformat()
        connection = await self.session_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            score_events = await self.session_repository.list_attendance_score_events(
                session_id=int(session["id"]),
                connection=connection,
            )
            created_reversals = 0
            for event in score_events:
                if int(event["delta"]) == 0:
                    continue
                await self.score_repository.create_reversal_event(
                    guild_id=guild_id_text,
                    member_id=int(event["member_id"]),
                    event_type="SESSION_CANCEL_REVERSAL",
                    delta=-int(event["delta"]),
                    reference_type="SESSION",
                    reference_id=int(session["id"]),
                    dedup_key=f"cancel_session:{session['id']}:{event['id']}",
                    description="Session cancelled",
                    created_by_discord_id=str(actor_discord_id),
                    created_at=now_text,
                    reversed_event_id=int(event["id"]),
                    connection=connection,
                )
                created_reversals += 1
            await self.session_repository.cancel_session(
                session_id=int(session["id"]),
                reason=cleaned_reason,
                now=now_text,
                connection=connection,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id_text,
                actor_discord_id=str(actor_discord_id),
                action_type="ATTENDANCE_SESSION_CANCELLED",
                target_type="ATTENDANCE",
                target_id=str(session["id"]),
                before_json=json.dumps({"status": session["status"]}, ensure_ascii=False),
                after_json=json.dumps(
                    {
                        "status": "CANCELLED",
                        "score_reversals": created_reversals,
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

        return SessionControlResult(
            status=SessionControlStatus.CANCELLED,
            session_id=int(session["id"]),
            attendance_date=session["attendance_date"],
            score_event_count=created_reversals,
            reason=cleaned_reason,
        )

    async def resume_today_session(
        self,
        *,
        guild_id: int | str,
        actor_discord_id: int | str,
        has_permission: bool,
        now: datetime,
    ) -> SessionControlResult:
        """Resume today's CANCELLED session and restore cancelled points."""

        self._require_aware(now)
        if not has_permission:
            return SessionControlResult(status=SessionControlStatus.PERMISSION_DENIED)

        located = await self._locate_today_session(guild_id, now)
        if isinstance(located, SessionControlResult):
            return located
        settings, session = located
        if session["status"] != "CANCELLED":
            return SessionControlResult(status=SessionControlStatus.NOT_CANCELLED)

        close_at = datetime.fromisoformat(session["close_at"])
        start_at = datetime.fromisoformat(session["start_at"])
        if now >= close_at:
            return SessionControlResult(status=SessionControlStatus.CLOSE_ALREADY_PASSED)

        new_status = "OPEN" if now >= start_at else "SCHEDULED"
        now_text = now.isoformat()
        guild_id_text = str(guild_id)
        connection = await self.session_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            cancellation_events = await self.session_repository.list_session_reversal_events(
                session_id=int(session["id"]),
                prefix="cancel_session",
                connection=connection,
            )
            restored = 0
            for event in cancellation_events:
                if int(event["delta"]) == 0:
                    continue
                await self.score_repository.create_reversal_event(
                    guild_id=guild_id_text,
                    member_id=int(event["member_id"]),
                    event_type="SESSION_RESUME_RESTORE",
                    delta=-int(event["delta"]),
                    reference_type="SESSION",
                    reference_id=int(session["id"]),
                    dedup_key=f"resume_session:{session['id']}:{event['id']}",
                    description="Session resumed",
                    created_by_discord_id=str(actor_discord_id),
                    created_at=now_text,
                    reversed_event_id=int(event["id"]),
                    connection=connection,
                )
                restored += 1
            await self.session_repository.resume_cancelled_session(
                session_id=int(session["id"]),
                status=new_status,
                opened_at=now_text if new_status == "OPEN" else None,
                now=now_text,
                connection=connection,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id_text,
                actor_discord_id=str(actor_discord_id),
                action_type="ATTENDANCE_SESSION_RESUMED",
                target_type="ATTENDANCE",
                target_id=str(session["id"]),
                before_json=json.dumps({"status": "CANCELLED"}, ensure_ascii=False),
                after_json=json.dumps(
                    {"status": new_status, "restored_events": restored},
                    ensure_ascii=False,
                ),
                reason="session resumed",
                created_at=now_text,
                connection=connection,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        return SessionControlResult(
            status=SessionControlStatus.RESUMED,
            session_id=int(session["id"]),
            attendance_date=session["attendance_date"],
            score_event_count=restored,
        )

    async def _locate_today_session(
        self,
        guild_id: int | str,
        now: datetime,
    ) -> tuple[dict, dict] | SessionControlResult:
        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return SessionControlResult(status=SessionControlStatus.NOT_CONFIGURED)
        local_date = get_server_today(now, settings["timezone"]).isoformat()
        session = await self.session_repository.get_by_guild_and_date(
            guild_id=guild_id_text,
            attendance_date=local_date,
        )
        if session is None:
            return SessionControlResult(status=SessionControlStatus.NO_SESSION)
        return settings, session

    def _normalize_setting(
        self,
        field: str,
        value: str,
        settings: dict,
    ) -> SettingsUpdateResult:
        normalized_field = field.strip().lower()
        cleaned_value = value.strip()
        aliases = {
            "timezone": "timezone",
            "attendance_days": "attendance_days",
            "attendance_start": "attendance_start",
            "late_deadline": "late_deadline",
            "close_deadline": "close_deadline",
            "excuse_mode": "excuse_mode",
            "officer_role_id": "officer_role_id",
            "attendance_channel_id": "attendance_channel_id",
            "announcement_channel_id": "announcement_channel_id",
        }
        if normalized_field not in aliases:
            return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_FIELD)
        db_field = aliases[normalized_field]

        if db_field == "timezone":
            try:
                ZoneInfo(cleaned_value)
            except ZoneInfoNotFoundError:
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
        elif db_field == "attendance_days":
            days = [day.strip().upper() for day in cleaned_value.split(",") if day.strip()]
            if not days or any(day not in ALLOWED_ATTENDANCE_DAYS for day in days):
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
            cleaned_value = ",".join(dict.fromkeys(days))
        elif db_field in {"attendance_start", "late_deadline", "close_deadline"}:
            try:
                parse_hhmm(cleaned_value)
                start = cleaned_value if db_field == "attendance_start" else settings["attendance_start"]
                late = cleaned_value if db_field == "late_deadline" else settings["late_deadline"]
                close = cleaned_value if db_field == "close_deadline" else settings["close_deadline"]
                if not parse_hhmm(start) < parse_hhmm(late) < parse_hhmm(close):
                    return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_TIME_ORDER)
            except ValueError:
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
        elif db_field == "excuse_mode":
            if cleaned_value not in ALLOWED_EXCUSE_MODES:
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
        elif db_field.endswith("_id"):
            if cleaned_value and not cleaned_value.isdigit():
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)

        return SettingsUpdateResult(
            status=SettingsUpdateStatus.UPDATED,
            field=db_field,
            value=cleaned_value,
            before_value=settings[db_field],
            after_value=cleaned_value,
        )

    def _require_aware(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")
