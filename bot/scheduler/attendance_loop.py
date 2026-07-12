"""분 단위로 출석 자동 처리를 수행하는 스케줄러."""

from datetime import datetime
import logging
from typing import Any

from discord.ext import tasks

from bot.runtime.time_provider import TimeProvider
from bot.services.guild_service import GuildService
from bot.services.session_service import SessionService
from bot.services.voice_verification_service import VoiceVerificationService
from bot.utils.time_utils import format_local_hhmm


logger = logging.getLogger(__name__)


class AttendanceScheduler:
    """출석 준비, 시작, 마감, 복구 작업을 자동으로 실행한다."""

    def __init__(
        self,
        *,
        guild_service: GuildService,
        session_service: SessionService,
        voice_verification_service: VoiceVerificationService | None = None,
        time_provider: TimeProvider | None = None,
        bot: Any | None = None,
    ) -> None:
        """
        스케줄러 의존성을 초기화한다.

        Args:
            guild_service: 설정된 서버 목록을 조회하는 서비스.
            session_service: 출석 세션 준비와 마감을 처리하는 서비스.
            voice_verification_service: 음성 검증 마감 처리를 담당하는 서비스.
            time_provider: 주기 실행 시 현재 시각을 공급하는 객체.
            bot: 공지 전송에 사용할 Discord 클라이언트.
        """

        self.guild_service = guild_service
        self.session_service = session_service
        self.voice_verification_service = voice_verification_service
        self.time_provider = time_provider or TimeProvider()
        self.bot = bot
        self._started = False

    def start(self) -> None:
        """아직 실행 중이 아니면 1분 주기 스케줄러 루프를 시작한다."""

        if self._started:
            return

        self._started = True
        logger.info("Attendance scheduler started.")
        self._loop.start()

    def stop(self) -> None:
        """실행 중인 스케줄러 루프를 중지한다."""

        if self._loop.is_running():
            self._loop.cancel()
        self._started = False
        logger.info("Attendance scheduler stopped.")

    async def run_once(self, now: datetime) -> None:
        """실제 1분 대기 없이 스케줄러 작업을 한 번 실행한다.

        Args:
            now: Current timezone-aware UTC time supplied by caller.

        Raises:
            ValueError: If ``now`` is naive.
        """

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")

        settings_rows = await self.guild_service.list_all_settings()

        for settings in settings_rows:
            try:
                result = await self.session_service.prepare_today_session(
                    guild_id=settings["guild_id"],
                    now=now,
                )
                if result.session is not None:
                    logger.info(
                        "Attendance scheduler prepared session: guild_id=%s session_id=%s status=%s",
                        settings["guild_id"],
                        result.session["id"],
                        result.session["status"],
                    )
            except Exception:
                logger.exception(
                    "Attendance scheduler session preparation failed: guild_id=%s",
                    settings["guild_id"],
                )

        await self._announce_starts(now)

        try:
            await self.session_service.process_overdue_sessions(now=now)
        except Exception:
            logger.exception("Attendance scheduler overdue processing failed.")

        if self.voice_verification_service is not None:
            try:
                await self.voice_verification_service.finalize_due_verifications(
                    now=now,
                )
            except Exception:
                logger.exception("Attendance verification finalization failed.")

        await self._announce_closes(now)

    async def recover_overdue_sessions(self, now: datetime) -> None:
        """주기 루프 시작 전에 재시작 복구를 한 번 실행한다.

        Args:
            now: Current timezone-aware UTC time.
        """

        await self.session_service.process_overdue_sessions(now=now)

    @tasks.loop(minutes=1)
    async def _loop(self) -> None:
        """주기적으로 실행되는 작업 본문이다."""

        try:
            await self.run_once(self.time_provider.now_utc())
        except Exception:
            logger.exception("Attendance scheduler tick failed.")

    async def _announce_starts(self, now: datetime) -> None:
        """새로 열린 세션의 시작 안내를 전송한다."""

        if self.bot is None:
            return

        sessions = (
            await self.session_service.session_repository.list_start_announcement_targets()
        )
        for session in sessions:
            channel_id = session["announcement_channel_id"] or session["attendance_channel_id"]
            if await self._send_channel_message(
                channel_id=channel_id,
                content=self._build_start_message(session),
            ):
                await self.session_service.session_repository.mark_start_announced(
                    session_id=int(session["id"]),
                    now=now.isoformat(),
                )

    async def _announce_closes(self, now: datetime) -> None:
        """마감된 세션의 종료 안내를 전송한다."""

        if self.bot is None:
            return

        sessions = (
            await self.session_service.session_repository.list_close_announcement_targets()
        )
        for session in sessions:
            channel_id = session["announcement_channel_id"] or session["attendance_channel_id"]
            if await self._send_channel_message(
                channel_id=channel_id,
                content=self._build_close_message(session),
            ):
                await self.session_service.session_repository.mark_close_announced(
                    session_id=int(session["id"]),
                    now=now.isoformat(),
                )

    async def _send_channel_message(self, *, channel_id: str | None, content: str) -> bool:
        """Discord 텍스트 채널을 찾을 수 있으면 메시지를 전송한다."""

        if self.bot is None or not channel_id:
            return False

        try:
            channel_id_int = int(channel_id)
        except (TypeError, ValueError):
            logger.warning("Invalid announcement channel id: %s", channel_id)
            return False

        channel = self.bot.get_channel(channel_id_int)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id_int)
            except Exception:
                logger.exception("Announcement channel lookup failed: %s", channel_id)
                return False

        try:
            await channel.send(content)
        except Exception:
            logger.exception("Announcement send failed: channel_id=%s", channel_id)
            return False
        return True

    def _build_start_message(self, session: dict[str, Any]) -> str:
        """출석 시작 공지 메시지를 생성한다."""

        timezone_name = session["timezone"]
        return (
            "🚀 출석이 시작되었습니다.\n"
            f"⏰ 정상 출석 마감: {format_local_hhmm(datetime.fromisoformat(session['late_at']), timezone_name)}\n"
            f"🔒 전체 마감: {format_local_hhmm(datetime.fromisoformat(session['close_at']), timezone_name)}\n"
            "✅ 지금 /출석 명령어로 체크인해주세요."
        )

    def _build_close_message(self, session: dict[str, Any]) -> str:
        """출석 마감 공지 메시지를 생성한다."""

        timezone_name = session["timezone"]
        closed_at = session["closed_at"] or session["close_at"]
        return (
            "🔒 출석이 마감되었습니다.\n"
            f"🕒 마감 시각: {format_local_hhmm(datetime.fromisoformat(closed_at), timezone_name)}\n"
            "📊 결과는 /출석현황 또는 /랭킹에서 확인할 수 있습니다."
        )
