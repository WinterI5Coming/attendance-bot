"""관리자 설정과 세션 제어 비즈니스 규칙을 담당한다."""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bot.config import ALLOWED_ATTENDANCE_DAYS, ALLOWED_EXCUSE_MODES
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.services.voice_verification_service import (
    WAIVE_REASON_SESSION_CANCELLED,
    VoiceVerificationService,
)
from bot.utils.time_utils import (
    get_server_today,
    parse_hhmm,
    require_aware,
)


class SettingsUpdateStatus(Enum):
    """설정 변경 작업에서 예상되는 처리 결과."""

    UPDATED = "UPDATED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_FIELD = "INVALID_FIELD"
    INVALID_VALUE = "INVALID_VALUE"
    INVALID_TIME_ORDER = "INVALID_TIME_ORDER"


class SessionControlStatus(Enum):
    """오늘 세션 취소/재개 흐름에서 예상되는 처리 결과."""

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
    """서버 설정 변경 결과."""

    status: SettingsUpdateStatus
    field: str | None = None
    value: str | None = None
    before_value: str | None = None
    after_value: str | None = None


@dataclass(frozen=True)
class VoiceVerificationUpdateResult:
    """`/설정 음성검증` 일괄 변경 결과."""

    status: SettingsUpdateStatus
    field: str | None = None
    settings: dict | None = None


@dataclass(frozen=True)
class SessionControlResult:
    """오늘 출석 세션 취소 또는 재개 결과."""

    status: SessionControlStatus
    session_id: int | None = None
    attendance_date: str | None = None
    score_event_count: int = 0
    reason: str | None = None


class AdminService:
    """Phase 3 관리자 설정 변경과 세션 제어를 처리한다."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        session_repository: SessionRepository,
        score_repository: ScoreRepository,
        audit_repository: AuditRepository,
        voice_verification_service: VoiceVerificationService | None = None,
    ) -> None:
        """관리자 서비스가 사용할 Repository 의존성을 저장한다."""

        self.guild_repository = guild_repository
        self.session_repository = session_repository
        self.score_repository = score_repository
        self.audit_repository = audit_repository
        self.voice_verification_service = voice_verification_service

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
        """서버 설정 한 항목을 검증, 갱신하고 감사 로그를 남긴다."""

        require_aware(now)
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

    async def update_voice_verification(
        self,
        *,
        guild_id: int | str,
        enabled: bool,
        voice_channel_id: str | None,
        voice_category_id: str | None,
        required_minutes: int | None,
        verification_end_time: str | None,
        early_leave_penalty: int | None,
        no_participation_penalty: int | None,
        actor_discord_id: int | str,
        has_permission: bool,
        now: datetime,
    ) -> VoiceVerificationUpdateResult:
        """음성 검증 관련 설정을 검증한 뒤 하나의 트랜잭션으로 저장한다.

        ``None``으로 넘어온 항목은 바꾸지 않는다. 채널/카테고리는 지정 시 기존
        목록을 대체한다. 부분 적용을 막기 위해 모든 항목을 함께 커밋한다.
        """

        require_aware(now)
        if not has_permission:
            return VoiceVerificationUpdateResult(status=SettingsUpdateStatus.PERMISSION_DENIED)
        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return VoiceVerificationUpdateResult(status=SettingsUpdateStatus.NOT_CONFIGURED)

        raw_updates: list[tuple[str, str]] = [
            ("voice_verification_enabled", "1" if enabled else "0"),
        ]
        if voice_channel_id is not None:
            raw_updates.append(("voice_channel_ids", voice_channel_id))
        if voice_category_id is not None:
            raw_updates.append(("voice_category_ids", voice_category_id))
        if required_minutes is not None:
            raw_updates.append(("voice_required_minutes", str(required_minutes)))
        if verification_end_time is not None:
            raw_updates.append(("voice_verification_end_time", verification_end_time))
        if early_leave_penalty is not None:
            raw_updates.append(("voice_early_leave_penalty", str(early_leave_penalty)))
        if no_participation_penalty is not None:
            raw_updates.append(("voice_no_participation_penalty", str(no_participation_penalty)))

        fields: dict[str, str] = {}
        for field, value in raw_updates:
            normalized = self._normalize_setting(field, value, settings)
            if normalized.status is not SettingsUpdateStatus.UPDATED:
                return VoiceVerificationUpdateResult(status=normalized.status, field=field)
            assert normalized.field is not None and normalized.value is not None
            fields[normalized.field] = normalized.value

        now_text = now.isoformat()
        connection = await self.guild_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            await self.guild_repository.update_settings(
                guild_id=guild_id_text,
                fields=fields,
                now=now_text,
                connection=connection,
            )
            await self.audit_repository.create_log(
                guild_id=guild_id_text,
                actor_discord_id=str(actor_discord_id),
                action_type="GUILD_SETTINGS_UPDATED",
                target_type="SETTING",
                target_id="voice_verification",
                before_json=json.dumps(
                    {field: settings.get(field) for field in fields},
                    ensure_ascii=False,
                ),
                after_json=json.dumps(fields, ensure_ascii=False),
                reason="voice verification settings update",
                created_at=now_text,
                connection=connection,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        updated = await self.guild_repository.get_by_guild_id(guild_id_text)
        return VoiceVerificationUpdateResult(
            status=SettingsUpdateStatus.UPDATED,
            settings=updated,
        )

    async def cancel_today_session(
        self,
        *,
        guild_id: int | str,
        reason: str,
        actor_discord_id: int | str,
        has_permission: bool,
        now: datetime,
    ) -> SessionControlResult:
        """오늘의 SCHEDULED 또는 OPEN 세션을 취소하고 출석 점수를 되돌린다."""

        require_aware(now)
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
            waived_verifications = 0
            if self.voice_verification_service is not None:
                # 취소된 세션의 음성 검증은 감점 없이 면제한다.
                waived_verifications = (
                    await self.voice_verification_service.waive_session_verifications(
                        session_id=int(session["id"]),
                        waived_reason=WAIVE_REASON_SESSION_CANCELLED,
                        now=now,
                        connection=connection,
                    )
                )
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
                        "waived_verifications": waived_verifications,
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
        """오늘의 CANCELLED 세션을 재개하고 취소된 점수를 복원한다."""

        require_aware(now)
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
            restored_verifications = 0
            if self.voice_verification_service is not None:
                restored_verifications = (
                    await self.voice_verification_service.restore_session_verifications(
                        session_id=int(session["id"]),
                        waived_reason=WAIVE_REASON_SESSION_CANCELLED,
                        now=now,
                        connection=connection,
                    )
                )
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
                    {
                        "status": new_status,
                        "restored_events": restored,
                        "restored_verifications": restored_verifications,
                    },
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
        """서버 설정과 오늘 날짜의 출석 세션을 함께 조회한다."""

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
        """설정 필드별 입력 값을 DB 저장 형식으로 정규화한다."""

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
            "voice_verification_enabled": "voice_verification_enabled",
            "voice_channel_ids": "voice_channel_ids",
            "voice_category_ids": "voice_category_ids",
            "voice_required_minutes": "voice_required_minutes",
            "voice_verification_end_time": "voice_verification_end_time",
            "voice_early_leave_penalty": "voice_early_leave_penalty",
            "voice_no_participation_penalty": "voice_no_participation_penalty",
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
        elif db_field == "voice_verification_enabled":
            normalized_bool = cleaned_value.lower()
            if normalized_bool in {"1", "true", "yes", "on"}:
                cleaned_value = "1"
            elif normalized_bool in {"0", "false", "no", "off"}:
                cleaned_value = "0"
            else:
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
        elif db_field in {"voice_channel_ids", "voice_category_ids"}:
            if cleaned_value:
                ids = [
                    item.strip()
                    for item in cleaned_value.split(",")
                    if item.strip()
                ]
                if not ids or any(not item.isdigit() for item in ids):
                    return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
                cleaned_value = ",".join(dict.fromkeys(ids))
        elif db_field == "voice_required_minutes":
            if not cleaned_value.isdigit() or not 1 <= int(cleaned_value) <= 24 * 60:
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
            cleaned_value = str(int(cleaned_value))
        elif db_field == "voice_verification_end_time":
            try:
                parse_hhmm(cleaned_value)
            except ValueError:
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
        elif db_field in {"voice_early_leave_penalty", "voice_no_participation_penalty"}:
            try:
                penalty = int(cleaned_value)
            except ValueError:
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
            # 감점은 0 또는 음수만 허용한다. 양수를 넣으면 실패가 가점이 된다.
            if penalty > 0 or penalty < -100:
                return SettingsUpdateResult(status=SettingsUpdateStatus.INVALID_VALUE)
            cleaned_value = str(penalty)
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

