"""Discord 출석 봇을 실행하는 진입점. 종료 시그널을 받아 정상 종료한다."""

from __future__ import annotations

import asyncio
import logging
import signal

from bot.app.client import AttendanceBot
from bot.app.factory import create_bot
from bot.config import Settings, load_settings

logger = logging.getLogger(__name__)


def run(settings: Settings) -> None:
    """
    검증된 설정으로 Discord 봇을 실행한다.

    `Client.run()` 대신 자체 러너를 사용해 SIGTERM/SIGINT를 받으면 스케줄러
    정지와 종료 백업을 거친 뒤 연결을 닫는다. Docker `stop`이 보내는 SIGTERM에도
    강제 종료 없이 정리된다. discord.py의 기본 로깅 설정은 쓰지 않으므로
    콘솔 로그가 중복 출력되지 않는다.

    Args:
        settings: `.env`에서 읽어 검증한 실행 설정.
    """

    bot = create_bot(settings)
    asyncio.run(_run_bot(bot, settings.discord_token))


async def _run_bot(bot: AttendanceBot, token: str) -> None:
    loop = asyncio.get_running_loop()
    shutdown_requested = False

    def request_shutdown(signal_name: str) -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True
        logger.info("Shutdown signal received: %s", signal_name)
        loop.create_task(bot.close())

    _install_signal_handlers(loop, request_shutdown)

    async with bot:
        await bot.start(token)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, callback) -> None:
    """가능하면 이벤트 루프에, 아니면(Windows) 스레드 안전한 우회로 시그널을 연결한다."""

    for sig in (signal.SIGINT, signal.SIGTERM):
        name = sig.name
        try:
            loop.add_signal_handler(sig, callback, name)
            continue
        except (NotImplementedError, RuntimeError):
            pass
        try:
            signal.signal(
                sig,
                lambda signum, frame, name=name: loop.call_soon_threadsafe(callback, name),
            )
        except (ValueError, OSError):
            logger.debug("Signal handler not installed: %s", name)


def main() -> None:
    """로컬 Python 개발 환경에서 설정을 읽고 봇을 실행한다."""

    run(load_settings())


if __name__ == "__main__":
    main()
