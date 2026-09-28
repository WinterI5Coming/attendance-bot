"""출석 기록의 음성 채널 참여 검증을 담당한다."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.repositories.stage_a_repository import StageARepository
from bot.utils.time_utils import (
    get_server_today,
    require_aware,
)

logger = logging.getLogger(__name__)

# 스케줄러가 마지막으로 살아 있던 시각을 저장하는 runtime_state 키.
# 재시작 후 놓친 음성 퇴장을 이 시각 기준으로 닫아 과다 집계를 막는다.
HEARTBEAT_KEY = "scheduler_heartbeat_at"

WAIVE_REASON_SESSION_CANCELLED = "SESSION_CANCELLED"
WAIVE_REASON_ATTENDANCE_CORRECTED = "ATTENDANCE_CORRECTED"
WAIVE_REASON_EXCUSE_APPROVED = "EXCUSE_APPROVED"
WAIVE_REASON_VERIFICATION_DISABLED = "VERIFICATION_DISABLED"


VERIFIABLE_ATTENDANCE_STATUSES = {
    "PRESENT",
    "LATE",
    "EXCUSED_LATE",
}


@dataclass(frozen=True)
class VerificationFinalizeResult:
    """대기 중인 검증을 마무리한 뒤 반환되는 요약."""

    processed: int = 0
    verified: int = 0
    failed: int = 0
    penalties: int = 0


@dataclass(frozen=True)
class VoiceReconcileResult:
    """재시작 후 실제 음성 채널 상태와 열린 로그를 맞춘 결과."""

    pending: int = 0
    closed_logs: int = 0
    opened_logs: int = 0


@dataclass(frozen=True)
class VerificationOverview:
    """`/출석 검증현황`에 표시할 오늘의 음성 검증 요약."""

    configured: bool
    enabled: bool = False
    has_targets: bool = False
    voice_channel_ids: list[str] = field(default_factory=list)
    voice_category_ids: list[str] = field(default_factory=list)
    timezone_name: str | None = None
    session: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)


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
        current_voice_category_id: str | None = None,
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
            category_id=current_voice_category_id,
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

        require_aware(now)
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
        if before_channel_id == after_channel_id:
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

        if not before_is_target and not after_is_target:
            # 검증 대상이 아닌 채널 사이의 이동이지만, 대상 채널이 설정에서
            # 빠졌거나 퇴장 이벤트를 놓친 경우 열린 로그가 남아 있을 수 있다.
            open_log = await self.stage_a_repository.get_open_voice_log(
                session_id=int(session["id"]),
                member_id=int(member["id"]),
            )
            if open_log is None:
                return

        now_text = now.isoformat()
        connection = await self.stage_a_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            open_log = await self.stage_a_repository.get_open_voice_log(
                session_id=int(session["id"]),
                member_id=int(member["id"]),
                connection=connection,
            )
            if open_log is not None:
                if open_log["voice_channel_id"] == str(before_channel_id):
                    # 정상적인 퇴장/이동: 지금 시각까지 체류를 인정한다.
                    # (대상 목록에서 빠진 채널을 나가는 경우도 실제 퇴장이다.)
                    await self._close_voice_log(
                        open_log=open_log,
                        left_at=now,
                        close_reason="MOVED" if after_is_target else "LEFT",
                        connection=connection,
                    )
                else:
                    # 이전 퇴장 이벤트를 놓친 상태다. 봇이 마지막으로 살아 있던
                    # 시각까지만 인정해 다운타임 동안의 체류를 과다 집계하지 않는다.
                    cutoff = await self._reconcile_cutoff(now=now, connection=connection)
                    await self._close_voice_log(
                        open_log=open_log,
                        left_at=cutoff,
                        close_reason="BOT_RECOVERY",
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

    async def record_heartbeat(self, *, now: datetime) -> None:
        """스케줄러가 살아 있음을 기록한다. 재시작 복구의 기준 시각으로 쓰인다."""

        require_aware(now)
        await self.stage_a_repository.set_runtime_value(
            key=HEARTBEAT_KEY,
            value=now.isoformat(),
            now=now.isoformat(),
        )

    async def reconcile_guild_voice_presence(
        self,
        *,
        guild_id: int | str,
        present: dict[str, tuple[str, str | None]],
        now: datetime,
    ) -> VoiceReconcileResult:
        """재시작 직후 실제 음성 채널 재실 상태와 열린 로그를 맞춘다.

        Args:
            guild_id: Discord guild ID.
            present: 현재 음성 채널에 있는 사용자 매핑
                ``{discord_id: (channel_id, category_id)}``.
            now: Current timezone-aware UTC time.

        다운타임 동안 나간 대원의 열린 로그는 마지막 하트비트 시각까지만 인정하고
        닫는다. 다운타임 동안 들어온 대원은 입장 시각을 알 수 없으므로 지금부터
        인정한다. 검증 대기 중인 대원만 대상으로 한다.
        """

        require_aware(now)
        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None or not settings.get("voice_verification_enabled"):
            return VoiceReconcileResult()

        attendance_date = get_server_today(now, settings["timezone"]).isoformat()
        session = await self.session_repository.get_by_guild_and_date(
            guild_id=guild_id_text,
            attendance_date=attendance_date,
        )
        if session is None:
            return VoiceReconcileResult()

        pending = await self.stage_a_repository.list_pending_verifications_for_session(
            session_id=int(session["id"]),
        )
        if not pending:
            return VoiceReconcileResult()

        closed = opened = 0
        now_text = now.isoformat()
        connection = await self.stage_a_repository.database.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            cutoff = await self._reconcile_cutoff(now=now, connection=connection)
            for verification in pending:
                location = present.get(str(verification["discord_id"]))
                in_target = location is not None and self.is_configured_voice_channel(
                    settings=settings,
                    channel_id=location[0],
                    category_id=location[1],
                )
                open_log = await self.stage_a_repository.get_open_voice_log(
                    session_id=int(session["id"]),
                    member_id=int(verification["member_id"]),
                    connection=connection,
                )
                if open_log is not None and (
                    not in_target or open_log["voice_channel_id"] != str(location[0])
                ):
                    await self._close_voice_log(
                        open_log=open_log,
                        left_at=cutoff,
                        close_reason="BOT_RECOVERY",
                        connection=connection,
                    )
                    closed += 1
                    open_log = None
                if in_target and open_log is None:
                    await self.stage_a_repository.open_voice_log(
                        guild_id=guild_id_text,
                        session_id=int(session["id"]),
                        member_id=int(verification["member_id"]),
                        voice_channel_id=str(location[0]),
                        joined_at=now_text,
                        connection=connection,
                    )
                    opened += 1
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

        logger.info(
            "Voice presence reconciled: guild_id=%s pending=%s closed=%s opened=%s",
            guild_id_text,
            len(pending),
            closed,
            opened,
        )
        return VoiceReconcileResult(
            pending=len(pending),
            closed_logs=closed,
            opened_logs=opened,
        )

    async def waive_verification_for_record(
        self,
        *,
        attendance_record_id: int,
        waived_reason: str,
        actor_discord_id: str | None,
        now: datetime,
        connection,
    ) -> bool:
        """출석 기록 하나의 검증을 면제하고, 이미 부과된 감점이 있으면 되돌린다.

        출석 정정으로 결석이 되었거나 조퇴 사유가 승인된 경우처럼 음성 참여
        요구가 더 이상 의미 없을 때 호출한다. 호출자의 트랜잭션 안에서 실행된다.
        """

        require_aware(now)
        verification = await self.stage_a_repository.get_verification_by_record_id(
            attendance_record_id=attendance_record_id,
            connection=connection,
        )
        if verification is None or verification["status"] not in {"PENDING", "FAILED"}:
            return False
        now_text = now.isoformat()
        await self._close_current_voice_log(
            session_id=int(verification["session_id"]),
            member_id=int(verification["member_id"]),
            now=now,
            close_reason="VERIFICATION_ENDED",
            connection=connection,
        )
        await self.stage_a_repository.waive_verification(
            verification_id=int(verification["id"]),
            waived_reason=waived_reason,
            now=now_text,
            connection=connection,
        )
        if verification["status"] == "FAILED":
            await self._reverse_failure_penalty(
                verification=verification,
                actor_discord_id=actor_discord_id,
                now=now.isoformat(),
                connection=connection,
            )
        return True

    async def waive_session_verifications(
        self,
        *,
        session_id: int,
        waived_reason: str,
        now: datetime,
        connection,
    ) -> int:
        """세션의 대기 중인 검증을 모두 면제한다(세션 취소 시)."""

        require_aware(now)
        pending = await self.stage_a_repository.list_pending_verifications_for_session(
            session_id=session_id,
            connection=connection,
        )
        for verification in pending:
            await self.stage_a_repository.waive_verification(
                verification_id=int(verification["id"]),
                waived_reason=waived_reason,
                now=now.isoformat(),
                connection=connection,
            )
        return len(pending)

    async def restore_session_verifications(
        self,
        *,
        session_id: int,
        waived_reason: str,
        now: datetime,
        connection,
    ) -> int:
        """세션 취소로 면제된 검증을 다시 대기 상태로 되돌린다(세션 재개 시)."""

        require_aware(now)
        waived = await self.stage_a_repository.list_waived_verifications_for_session(
            session_id=session_id,
            waived_reason=waived_reason,
            connection=connection,
        )
        restored = 0
        for verification in waived:
            if verification["verified_at"] is not None:
                status = "VERIFIED"
            elif verification["failed_at"] is not None:
                status = "FAILED"
            else:
                status = "PENDING"
            await self.stage_a_repository.restore_verification(
                verification_id=int(verification["id"]),
                status=status,
                now=now.isoformat(),
                connection=connection,
            )
            restored += 1
        return restored

    async def finalize_due_verifications(
        self,
        *,
        now: datetime,
    ) -> VerificationFinalizeResult:
        """종료 시간이 지난 대기 검증을 마무리한다."""

        require_aware(now)
        connection = await self.stage_a_repository.database.connect()
        processed = verified = failed = penalties = 0
        try:
            await connection.execute("BEGIN IMMEDIATE;")
            pending = await self.stage_a_repository.list_pending_verifications(
                now=now.isoformat(),
                connection=connection,
            )
            settings_cache: dict[str, dict[str, Any] | None] = {}
            for verification in pending:
                # 검증 하나가 실패해도 나머지 처리가 함께 롤백되지 않도록
                # 행 단위 SAVEPOINT로 격리한다.
                await connection.execute("SAVEPOINT verification_row;")
                try:
                    outcome = await self._finalize_one(
                        verification=verification,
                        now=now,
                        settings_cache=settings_cache,
                        connection=connection,
                    )
                    await connection.execute("RELEASE SAVEPOINT verification_row;")
                except Exception:
                    await connection.execute("ROLLBACK TO SAVEPOINT verification_row;")
                    await connection.execute("RELEASE SAVEPOINT verification_row;")
                    logger.exception(
                        "Verification finalize failed: verification_id=%s",
                        verification["id"],
                    )
                    continue
                processed += 1
                if outcome == "VERIFIED":
                    verified += 1
                elif outcome == "FAILED":
                    failed += 1
                elif outcome == "FAILED_WITH_PENALTY":
                    failed += 1
                    penalties += 1
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

    async def list_today_verifications(
        self,
        *,
        guild_id: int | str,
        now: datetime,
    ) -> VerificationOverview:
        """오늘 세션의 음성 검증 진행 상황을 운영자 확인용으로 모아 반환한다."""

        require_aware(now)
        guild_id_text = str(guild_id)
        settings = await self.guild_repository.get_by_guild_id(guild_id_text)
        if settings is None:
            return VerificationOverview(configured=False)
        overview = VerificationOverview(
            configured=True,
            enabled=bool(settings.get("voice_verification_enabled")),
            has_targets=self._has_voice_targets(settings),
            voice_channel_ids=sorted(self._parse_id_list(settings.get("voice_channel_ids"))),
            voice_category_ids=sorted(self._parse_id_list(settings.get("voice_category_ids"))),
            timezone_name=settings["timezone"],
        )
        attendance_date = get_server_today(now, settings["timezone"]).isoformat()
        session = await self.session_repository.get_by_guild_and_date(
            guild_id=guild_id_text,
            attendance_date=attendance_date,
        )
        if session is None:
            return overview
        rows = await self.stage_a_repository.list_session_verifications(
            session_id=int(session["id"]),
        )
        # 대기 중인 검증은 아직 열린 로그의 시간이 반영되지 않았을 수 있으므로
        # 표시 시점 기준으로 누적 시간을 다시 계산한다.
        connection = await self.stage_a_repository.database.connect()
        try:
            refreshed = []
            for row in rows:
                if row["status"] == "PENDING":
                    row = {
                        **row,
                        "accumulated_seconds": await self._calculate_accumulated_seconds(
                            verification=row,
                            now=now,
                            connection=connection,
                        ),
                    }
                refreshed.append(row)
        finally:
            await connection.close()
        return VerificationOverview(
            configured=True,
            enabled=overview.enabled,
            has_targets=overview.has_targets,
            voice_channel_ids=overview.voice_channel_ids,
            voice_category_ids=overview.voice_category_ids,
            timezone_name=overview.timezone_name,
            session=session,
            rows=refreshed,
        )

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

    async def _finalize_one(
        self,
        *,
        verification: dict[str, Any],
        now: datetime,
        settings_cache: dict[str, dict[str, Any] | None],
        connection,
    ) -> str:
        """검증 하나를 마무리하고 결과 종류를 문자열로 반환한다."""

        now_text = now.isoformat()
        session = await self.session_repository.get_by_id(
            session_id=int(verification["session_id"]),
            connection=connection,
        )
        guild_id = None if session is None else session["guild_id"]
        if guild_id is not None and guild_id not in settings_cache:
            settings_cache[guild_id] = await self.guild_repository.get_by_guild_id(guild_id)
        settings = None if guild_id is None else settings_cache[guild_id]

        # 세션이 취소되었거나 검증 기능이 꺼진 서버라면 감점 없이 면제한다.
        if session is None or session["status"] == "CANCELLED":
            waived_reason = WAIVE_REASON_SESSION_CANCELLED
        elif settings is None or not settings.get("voice_verification_enabled"):
            waived_reason = WAIVE_REASON_VERIFICATION_DISABLED
        else:
            waived_reason = None

        await self._close_current_voice_log(
            session_id=int(verification["session_id"]),
            member_id=int(verification["member_id"]),
            now=now,
            close_reason="VERIFICATION_ENDED",
            connection=connection,
        )
        if waived_reason is not None:
            await self.stage_a_repository.waive_verification(
                verification_id=int(verification["id"]),
                waived_reason=waived_reason,
                now=now.isoformat(),
                connection=connection,
            )
            return "WAIVED"

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
            return "VERIFIED"

        failure_reason = "NO_VOICE_JOIN" if accumulated == 0 else "INSUFFICIENT_DURATION"
        await self.stage_a_repository.mark_failed(
            verification_id=int(verification["id"]),
            accumulated_seconds=accumulated,
            failure_reason=failure_reason,
            now=now_text,
            connection=connection,
        )
        created = await self._create_failure_penalty(
            verification=verification,
            failure_reason=failure_reason,
            now=now_text,
            connection=connection,
        )
        return "FAILED_WITH_PENALTY" if created else "FAILED"

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
        await self._close_voice_log(
            open_log=open_log,
            left_at=now,
            close_reason=close_reason,
            connection=connection,
        )

    async def _close_voice_log(
        self,
        *,
        open_log: dict[str, Any],
        left_at: datetime,
        close_reason: str,
        connection,
    ) -> None:
        """열린 로그를 ``left_at`` 시각에 닫는다. 입장 시각보다 이르면 0초로 닫는다."""

        joined_at = datetime.fromisoformat(open_log["joined_at"])
        effective_left = max(joined_at, left_at)
        duration = max(0, int((effective_left - joined_at).total_seconds()))
        await self.stage_a_repository.close_voice_log(
            voice_log_id=int(open_log["id"]),
            left_at=effective_left.isoformat(),
            duration_seconds=duration,
            close_reason=close_reason,
            connection=connection,
        )

    async def _reconcile_cutoff(self, *, now: datetime, connection) -> datetime:
        """놓친 퇴장을 닫을 기준 시각(마지막 하트비트, 없으면 지금)을 반환한다."""

        heartbeat = await self.stage_a_repository.get_runtime_value(
            key=HEARTBEAT_KEY,
            connection=connection,
        )
        if heartbeat is None:
            return now
        try:
            parsed = datetime.fromisoformat(heartbeat)
        except ValueError:
            return now
        if parsed.tzinfo is None:
            return now
        return min(parsed, now)

    async def _reverse_failure_penalty(
        self,
        *,
        verification: dict[str, Any],
        actor_discord_id: str | None,
        now: str,
        connection,
    ) -> bool:
        """검증 실패 감점이 아직 유효하면 반대 이벤트로 되돌린다."""

        penalty = await self.stage_a_repository.get_failure_penalty_event(
            verification_id=int(verification["id"]),
            connection=connection,
        )
        if penalty is None or int(penalty["delta"]) == 0:
            return False
        await self.score_repository.create_reversal_event(
            guild_id=penalty["guild_id"],
            member_id=int(penalty["member_id"]),
            event_type="VOICE_PENALTY_WAIVED",
            delta=-int(penalty["delta"]),
            reference_type="VOICE_VERIFICATION",
            reference_id=int(verification["id"]),
            dedup_key=f"voice-verification:{verification['id']}:waive:{penalty['id']}",
            description="음성 검증 면제로 감점 취소",
            created_by_discord_id=actor_discord_id,
            created_at=now,
            reversed_event_id=int(penalty["id"]),
            connection=connection,
        )
        return True

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
            configured = session["no_participation_penalty"]
            delta = -2 if configured is None else int(configured)
            event_type = "NO_PARTICIPATION_PENALTY"
            description = "No voice participation"
        else:
            configured = session["early_leave_penalty"]
            delta = -1 if configured is None else int(configured)
            event_type = "EARLY_LEAVE_PENALTY"
            description = "Insufficient voice duration"
        if delta == 0:
            return False
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

