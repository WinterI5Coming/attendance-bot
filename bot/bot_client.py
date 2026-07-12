"""Discord 클라이언트의 수명주기와 백그라운드 작업을 관리한다."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from bot.config import Settings
from bot.db.database import Database
from bot.runtime.time_provider import TimeProvider
from bot.scheduler.attendance_loop import AttendanceScheduler
from bot.scheduler.backup_loop import BackupScheduler


logger = logging.getLogger(__name__)


class AttendanceBot(commands.Bot):
    """
    출석 봇의 Discord 클라이언트와 실행 중 필요한 리소스를 보관한다.

    데이터베이스 초기화, Cog 등록, 슬래시 명령 동기화, 스케줄러 시작처럼
    Discord 클라이언트 수명주기에 붙어야 하는 작업만 담당한다.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        attendance_scheduler: AttendanceScheduler,
        backup_scheduler: BackupScheduler,
        time_provider: TimeProvider,
        cogs: list[commands.Cog],
    ) -> None:
        """
        봇 클라이언트를 생성하고 종료 시 정리할 런타임 의존성을 저장한다.

        Args:
            settings: 검증된 실행 환경 설정.
            database: 봇이 사용하는 SQLite 데이터베이스 래퍼.
            attendance_scheduler: 출석 자동 공지와 마감 복구 스케줄러.
            backup_scheduler: 주기적 데이터베이스 백업 스케줄러.
            time_provider: 재시작 복구 시각을 공급하는 객체.
            cogs: Discord 명령과 이벤트 리스너를 담은 Cog 목록.
        """

        intents = discord.Intents.default()
        intents.voice_states = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )
        self.settings = settings
        self.database = database
        self.attendance_scheduler = attendance_scheduler
        self.backup_scheduler = backup_scheduler
        self.time_provider = time_provider
        self._configured_cogs = cogs

    async def setup_hook(self) -> None:
        """
        Discord 로그인 직후 필요한 애플리케이션 준비 작업을 수행한다.

        데이터베이스 마이그레이션을 적용한 뒤 Cog를 등록하고 개발 서버에
        슬래시 명령을 동기화한다. 이후 미처리 출석 세션을 복구하고
        백그라운드 스케줄러를 시작한다.
        """

        await self.database.initialize()
        logger.info("Database initialized: %s", self.settings.db_path)

        for cog in self._configured_cogs:
            await self.add_cog(cog)

        development_guild = discord.Object(id=self.settings.development_guild_id)
        self.tree.copy_global_to(guild=development_guild)
        synced_commands = await self.tree.sync(guild=development_guild)
        logger.info(
            "Synced %d slash commands to development guild %s.",
            len(synced_commands),
            self.settings.development_guild_id,
        )

        self.attendance_scheduler.bot = self
        await self.attendance_scheduler.recover_overdue_sessions(
            self.time_provider.now_utc()
        )
        self.attendance_scheduler.start()
        self.backup_scheduler.start()

    async def close(self) -> None:
        """Discord 연결을 닫기 전에 백그라운드 스케줄러를 먼저 중지한다."""

        logger.info("Discord bot shutdown requested.")
        self.attendance_scheduler.stop()
        self.backup_scheduler.stop()
        await super().close()
        logger.info("Discord bot closed.")
