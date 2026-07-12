"""출석 기록의 음성 채널 참여 검증을 담당한다."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import logging
from typing import Any

from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.repositories.stage_a_repository import StageARepository
from bot.utils.time_utils import get_server_today


logger = logging.getLogger(__name__)


VERIFIABLE_ATTENDANCE_STATUSES = {
    "PRESENT",
    "LATE",
    "EXCUSED_LATE",
}


class VerificationManageStatus(Enum):
    """관리자 검증 작업에서 예상되는 처리 결과."""

    UPDATED = "UPDATED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_STATUS = "INVALID_STATUS"
    PERMISSION_DENIED = "PERMISSION_DENIED"


@dataclass(frozen=True)
class VerificationSummary:
    """표시용 정보가 보강된 검증 행 하나."""

    verification_id: int
    member_id: int
    discord_id: str
    display_name: str
    attendance_status: str
    status: str
    required_seconds: int
    accumulated_seconds: int
    failure_reason: str | None


@dataclass(frozen=True)
class VerificationFinalizeResult:
    """대기 중인 검증을 마무리한 뒤 반환되는 요약."""

    processed: int = 0
    verified: int = 0
    failed: int = 0
    penalties: int = 0


class VoiceVerificationService:
    """음성 로그, 출석 검증, 실패 감점을 조율한다."""

    def __init__(
        self,
        *,
        guild_repository: GuildRepository,
        member_repository: MemberRepository,
        session_repository: SessionRepository,
        attendance_repository: AttendanceRepository,
        score_repository: ScoreRepository,
        stage_a_repository: StageARepository,
    ) -> None:
        """서비스 의존성을 초기화한다."""

        self.guild_repository = guild_repository
        self.member_repository = member_repository
        self.session_repository = session_repository
        self.attendance_repository = attendance_repository
        self.score_repository = score_repository
        self.stage_a_repository = stage_a_repository

    async def create_for_attendance_record(
        self,
        *,
        guild_id: str,
        session: dict[str, Any],
        attendance_record: dict[str, Any],
        checked_at: str,
        current_voice_channel_id: str | None,
        connection,
    ) -> int | None:
        """기능이 켜져 있으면 사용자 체크인에 대한 대기 검증을 생성한다.

        The attendance record and the original attendance score are not altered.
        Voice verification lives in its own table so existing attendance
        semantics stay intact, and any later failure is represented by a
        separate penalty score event.
        """

        if attendance_record["status"] not in VERIFIABLE_ATTENDANCE_STATUSES:
            return None
        settings = await self.guild_repository.get_by_guild_id(guild_id)
        if settings is None or not settings.get("voice_verification_enabled"):
            return None
        if not self._has_voice_targets(settings):
            return None
        required_seconds = session.get("required_voice_seconds")
        verification_end_at = session.get("verification_end_at")
        if required_seconds is None or verification_end_at is None:
            return None

        verification_id = await self.stage_a_repository.create_verification(
            attendance_record_id=int(attendance_record["id"]),
            session_id=int(session["id"]),
            member_id=int(attendance_record["member_id"]),
            required_seconds=int(required_seconds),
            verification_end_at=verification_end_at,
            now=checked_at,
            connection=connection,
        )

        if current_voice_channel_id and self.is_configured_voice_channel(
            settings=settings,
            channel_id=current_voice_channel_id,
            category_id=None,
        ):
            # 멤버가 /출석 실행 시점에 이미 검증 대상 음성 채널에 있었다면
            # 출석 시각(checked_at) 이후의 시간만 인정한다. Discord 이벤트만으로는
            # 이 출석 기록 이전의 참여를 증명할 수 없기 때문에 로그 시작점을
            # 명령 실행 시각으로 맞춘다.
            await self.stage_a_repository.open_voice_log(
                guild_id=guild_id,
                session_id=int(session["id"]),
                member_id=int(attendance_record["member_id"]),
                voice_channel_id=current_voice_channel_id,
                joined_at=checked_at,
                connection=connection,
            )

        return verification_id

    async def handle_voice_update(
        self,
        *,
        guild_id: int | str,
        discord_id: int | str,
        before_channel_id: int | str | None,
        before_category_id: int | str | None,
        after_channel_id: int | str | None,
        after_category_id: int | str | None,
        now: datetime,
    ) -> None:
        """입장, 퇴장, 설정된 채널 간 이동을 기록한다.

        Args:
            guild_id: Discord guild ID.
            discord_id: Discord user ID.
            before_channel_id: Voice channel before the event, if any.
            before_category_id: Parent category before the event, if any.
            after_channel_id: Voice channel after the event, if any.
            after_category_id: Parent category after the event, if any.
            now: Current timezone-aware UTC time.
        """

        self._require_aware(now)
        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None or not settings.get("voice_verification_enabled"):
            return

        before_is_target = self.is_configured_voice_channel(
            settings=settings,
            channel_id=None if before_channel_id is None else str(before_channel_id),
            category_id=None if before_category_id is None else str(before_category_id),
        )
        after_is_target = self.is_configured_voice_channel(
            settings=settings,
            channel_id=None if after_channel_id is None else str(after_channel_id),
            category_id=None if after_category_id is None else str(after_category_id),
        )
        if before_is_target == after_is_target and before_channel_id == after_channel_id:
            return

        member = await self.member_repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=str(discord_id),
        )
        if member is None or not member["is_active"]:
            return

        attendance_date = get_server_today(now, settings["timezone"]).isoformat()
        session = await self.session_repository.get_by_guild_and_date(
            guild_id=guild_id_text,
            attendance_date=attendance_date,
        )
        if session is None:
            return

        verification = await self.stage_a_repository.get_pending_verification(
            session_id=int(session["id"]),
            member_id=int(member["id"]),
        )
        if verification is None:
            return

        now_text = now.isoformat()
        connection = await self.stage_a_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            if before_is_target:
                await self._close_current_voice_log(
                    session_id=int(session["id"]),
                    member_id=int(member["id"]),
                    now=now,
                    close_reason="MOVED" if after_is_target else "LEFT",
                    connection=connection,
                )
            if after_is_target and after_channel_id is not None:
                await self.stage_a_repository.open_voice_log(
                    guild_id=guild_id_text,
                    session_id=int(session["id"]),
                    member_id=int(member["id"]),
                    voice_channel_id=str(after_channel_id),
                    joined_at=now_text,
                    connection=connection,
                )
            await self._refresh_or_verify(
                verification=verification,
                now=now,
                connection=connection,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def finalize_due_verifications(
        self,
        *,
        now: datetime,
    ) -> VerificationFinalizeResult:
        """종료 시간이 지난 대기 검증을 마무리한다."""

        self._require_aware(now)
        now_text = now.isoformat()
        connection = await self.stage_a_repository.database.connect()
        processed = verified = failed = penalties = 0
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            pending = await self.stage_a_repository.list_pending_verifications(
                now=now_text,
                connection=connection,
            )
            for verification in pending:
                processed += 1
                await self._close_current_voice_log(
                    session_id=int(verification["session_id"]),
                    member_id=int(verification["member_id"]),
                    now=now,
                    close_reason="VERIFICATION_ENDED",
                    connection=connection,
                )
                accumulated = await self._calculate_accumulated_seconds(
                    verification=verification,
                    now=now,
                    connection=connection,
                )
                if accumulated >= int(verification["required_seconds"]):
                    await self.stage_a_repository.mark_verified(
                        verification_id=int(verification["id"]),
                        accumulated_seconds=accumulated,
                        now=now_text,
                        connection=connection,
                    )
                    verified += 1
                else:
                    failure_reason = (
                        "NO_VOICE_JOIN"
                        if accumulated == 0
                        else "INSUFFICIENT_DURATION"
                    )
                    await self.stage_a_repository.mark_failed(
                        verification_id=int(verification["id"]),
                        accumulated_seconds=accumulated,
                        failure_reason=failure_reason,
                        now=now_text,
                        connection=connection,
                    )
                    if await self._create_failure_penalty(
                        verification=verification,
                        failure_reason=failure_reason,
                        now=now_text,
                        connection=connection,
                    ):
                        penalties += 1
                    failed += 1
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        return VerificationFinalizeResult(
            processed=processed,
            verified=verified,
            failed=failed,
            penalties=penalties,
        )

    async def list_session_verifications(
        self,
        *,
        session_id: int,
    ) -> list[VerificationSummary]:
        """출석 세션 하나에 대한 검증 행을 반환한다."""

        connection = await self.stage_a_repository.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    av.id AS verification_id,
                    av.member_id,
                    m.discord_id,
                    m.display_name,
                    ar.status AS attendance_status,
                    av.status,
                    av.required_seconds,
                    av.accumulated_seconds,
                    av.failure_reason
                FROM attendance_verifications AS av
                JOIN members AS m ON m.id = av.member_id
                JOIN attendance_records AS ar ON ar.id = av.attendance_record_id
                WHERE av.session_id = ?
                ORDER BY m.display_name COLLATE NOCASE;
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [
                VerificationSummary(
                    verification_id=int(row["verification_id"]),
                    member_id=int(row["member_id"]),
                    discord_id=row["discord_id"],
                    display_name=row["display_name"],
                    attendance_status=row["attendance_status"],
                    status=row["status"],
                    required_seconds=int(row["required_seconds"]),
                    accumulated_seconds=int(row["accumulated_seconds"]),
                    failure_reason=row["failure_reason"],
                )
                for row in rows
            ]
        finally:
            await connection.close()

    def is_configured_voice_channel(
        self,
        *,
        settings: dict[str, Any],
        channel_id: str | None,
        category_id: str | None,
    ) -> bool:
        """음성 채널 또는 카테고리가 검증 대상으로 설정되어 있는지 확인한다."""

        channel_ids = self._parse_id_list(settings.get("voice_channel_ids"))
        category_ids = self._parse_id_list(settings.get("voice_category_ids"))
        return (
            channel_id is not None
            and channel_id in channel_ids
        ) or (
            category_id is not None
            and category_id in category_ids
        )

    async def _refresh_or_verify(
        self,
        *,
        verification: dict[str, Any],
        now: datetime,
        connection,
    ) -> None:
        """현재 누적 음성 시간을 다시 계산하고 기준 충족 시 검증 완료 처리한다."""

        accumulated = await self._calculate_accumulated_seconds(
            verification=verification,
            now=now,
            connection=connection,
        )
        if accumulated >= int(verification["required_seconds"]):
            await self.stage_a_repository.mark_verified(
                verification_id=int(verification["id"]),
                accumulated_seconds=accumulated,
                now=now.isoformat(),
                connection=connection,
            )
        else:
            await self.stage_a_repository.update_accumulated_seconds(
                verification_id=int(verification["id"]),
                accumulated_seconds=accumulated,
                now=now.isoformat(),
                connection=connection,
            )

    async def _close_current_voice_log(
        self,
        *,
        session_id: int,
        member_id: int,
        now: datetime,
        close_reason: str,
        connection,
    ) -> None:
        """현재 열려 있는 음성 체류 로그를 종료 처리한다."""

        open_log = await self.stage_a_repository.get_open_voice_log(
            session_id=session_id,
            member_id=member_id,
            connection=connection,
        )
        if open_log is None:
            return
        joined_at = datetime.fromisoformat(open_log["joined_at"])
        duration = max(0, int((now - joined_at).total_seconds()))
        await self.stage_a_repository.close_voice_log(
            voice_log_id=int(open_log["id"]),
            left_at=now.isoformat(),
            duration_seconds=duration,
            close_reason=close_reason,
            connection=connection,
        )

    async def _calculate_accumulated_seconds(
        self,
        *,
        verification: dict[str, Any],
        now: datetime,
        connection,
    ) -> int:
        """출석 체크 이후 검증 종료 시각까지의 유효 음성 체류 시간을 계산한다."""

        record_rows = await connection.execute_fetchall(
            """
            SELECT checked_at
            FROM attendance_records
            WHERE id = ?;
            """,
            (verification["attendance_record_id"],),
        )
        if not record_rows or record_rows[0]["checked_at"] is None:
            return 0
        checked_at = datetime.fromisoformat(record_rows[0]["checked_at"])
        verification_end = datetime.fromisoformat(verification["verification_end_at"])
        total = 0
        logs = await self.stage_a_repository.list_voice_logs_for_verification(
            session_id=int(verification["session_id"]),
            member_id=int(verification["member_id"]),
            connection=connection,
        )
        for log in logs:
            joined_at = datetime.fromisoformat(log["joined_at"])
            left_at = (
                now
                if log["left_at"] is None
                else datetime.fromisoformat(log["left_at"])
            )
            effective_start = max(joined_at, checked_at)
            effective_end = min(left_at, now, verification_end)
            if effective_end > effective_start:
                total += int((effective_end - effective_start).total_seconds())
        return max(0, total)

    async def _create_failure_penalty(
        self,
        *,
        verification: dict[str, Any],
        failure_reason: str,
        now: str,
        connection,
    ) -> bool:
        """음성 검증 실패에 따른 감점 이벤트를 중복 없이 생성한다."""

        session = await self.session_repository.get_by_id(
            session_id=int(verification["session_id"]),
            connection=connection,
        )
        if session is None:
            return False
        if failure_reason == "NO_VOICE_JOIN":
            delta = int(session["no_participation_penalty"] or -2)
            event_type = "NO_PARTICIPATION_PENALTY"
            description = "No voice participation"
        else:
            delta = int(session["early_leave_penalty"] or -1)
            event_type = "EARLY_LEAVE_PENALTY"
            description = "Insufficient voice duration"
        try:
            await self.score_repository.create_event(
                guild_id=session["guild_id"],
                member_id=int(verification["member_id"]),
                event_type=event_type,
                delta=delta,
                reference_type="VOICE_VERIFICATION",
                reference_id=int(verification["id"]),
                dedup_key=f"voice-verification:{verification['id']}:failure",
                description=description,
                created_by_discord_id=None,
                created_at=now,
                connection=connection,
            )
        except Exception as exc:
            if exc.__class__.__name__ == "IntegrityError":
                return False
            raise
        return True

    def _has_voice_targets(self, settings: dict[str, Any]) -> bool:
        """서버 설정에 음성 검증 대상 채널 또는 카테고리가 있는지 확인한다."""

        return bool(
            self._parse_id_list(settings.get("voice_channel_ids"))
            or self._parse_id_list(settings.get("voice_category_ids"))
        )

    def _parse_id_list(self, value: str | None) -> set[str]:
        """쉼표로 구분된 Discord ID 문자열을 집합으로 변환한다."""

        if not value:
            return set()
        return {
            item.strip()
            for item in value.split(",")
            if item.strip()
        }

    def _require_aware(self, now: datetime) -> None:
        """timezone-aware datetime인지 검증한다."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")
