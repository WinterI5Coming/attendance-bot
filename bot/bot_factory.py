"""Discord 봇 클라이언트를 생성하고 등록 절차를 조율한다."""

from __future__ import annotations

from bot.bot_client import AttendanceBot
from bot.commands.system_commands import register_system_commands
from bot.config import Settings
from bot.container import create_bot_container
from bot.event_handlers.lifecycle_events import register_lifecycle_events


def create_bot(settings: Settings) -> AttendanceBot:
    """
    실행 설정을 받아 완전히 조립된 Discord 봇 클라이언트를 생성한다.

    Args:
        settings: `.env`에서 읽어 검증한 실행 설정.

    Returns:
        이벤트와 시스템 명령이 등록된 AttendanceBot 인스턴스.
    """

    container = create_bot_container(settings)
    bot = AttendanceBot(
        settings=settings,
        database=container.database,
        attendance_scheduler=container.attendance_scheduler,
        backup_scheduler=container.backup_scheduler,
        time_provider=container.time_provider,
        cogs=container.cogs,
    )
    register_lifecycle_events(bot)
    register_system_commands(bot)
    return bot
