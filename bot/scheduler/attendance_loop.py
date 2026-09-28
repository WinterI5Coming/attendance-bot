"""분 단위로 출석 자동 처리를 수행하는 스케줄러."""

import logging
from datetime import datetime
from typing import Any

from discord.ext import tasks

from bot.runtime.time_provider import TimeProvider
from bot.services.guild_service import GuildService
from bot.services.session_service import SessionService
from bot.services.voice_verification_service import VoiceVerificationService
from bot.ui.attendance_messages import (
    build_close_announcement_embed,
    build_start_announcement_embed,
)
from bot.ui.views.attendance import CheckInView
from bot.utils.discord_channels import edit_channel_message, send_channel_message

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
        check_in_view: CheckInView | None = None,
    ) -> None:
        """
        스케줄러 의존성을 초기화한다.

        Args:
            guild_service: 설정된 서버 목록을 조회하는 서비스.
            session_service: 출석 세션 준비와 마감을 처리하는 서비스.
            voice_verification_service: 음성 검증 마감 처리를 담당하는 서비스.
            time_provider: 주기 실행 시 현재 시각을 공급하는 객체.
            bot: 공지 전송에 사용할 Discord 클라이언트.
            check_in_view: 시작 공지에 붙일 영속 [출석하기] 버튼 뷰.
        """

        self.guild_service = guild_service
        self.session_service = session_service
        self.voice_verification_service = voice_verification_service
        self.time_provider = time_provider or TimeProvider()
        self.bot = bot
        self.check_in_view = check_in_view
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

        if self.voice_verification_service is not None:
            try:
                await self.voice_verification_service.record_heartbeat(now=now)
            except Exception:
                logger.exception("Scheduler heartbeat failed.")

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
        """새로 열린 세션의 시작 공지(+출석하기 버튼)를 전송한다."""

        if self.bot is None:
            return

        sessions = await self.session_service.list_start_announcement_targets()
        for session in sessions:
            channel_id = session["announcement_channel_id"] or session["attendance_channel_id"]
            message = await send_channel_message(
                self.bot,
                channel_id,
                embed=build_start_announcement_embed(session),
                view=self.check_in_view,
            )
            if message is None:
                continue
            await self.session_service.mark_start_announced(
                session_id=int(session["id"]),
                now=now,
                message_id=str(getattr(message, "id", "")) or None,
            )

    async def _announce_closes(self, now: datetime) -> None:
        """마감된 세션의 종료 공지를 보내고 시작 공지의 버튼을 비활성화한다."""

        if self.bot is None:
            return

        sessions = await self.session_service.list_close_announcement_targets()
        for session in sessions:
            channel_id = session["announcement_channel_id"] or session["attendance_channel_id"]
            if self.check_in_view is not None and session.get("start_announcement_message_id"):
                await edit_channel_message(
                    self.bot,
                    channel_id,
                    session["start_announcement_message_id"],
                    view=self.check_in_view.closed(),
                )
            message = await send_channel_message(
                self.bot,
                channel_id,
                embed=build_close_announcement_embed(session),
            )
            if message is None:
                continue
            await self.session_service.mark_close_announced(
                session_id=int(session["id"]),
                now=now,
            )
