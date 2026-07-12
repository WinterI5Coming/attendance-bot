"""Windows-friendly executable entry point for AttendanceBot."""

from __future__ import annotations

import logging
import sys
import traceback

from bot.config import load_settings
from bot.main import run
from bot.runtime.exception_handler import print_startup_error, wait_before_exit
from bot.runtime.instance_lock import InstanceLock
from bot.runtime.logging_config import configure_logging, shutdown_logging
from bot.runtime.paths import ensure_runtime_directories, get_app_directory


def main() -> int:
    """Start the bot with packaged-app safety checks."""

    app_directory = get_app_directory()
    _, logs_directory = ensure_runtime_directories(app_directory)
    log_path = configure_logging(logs_directory)
    logger = logging.getLogger(__name__)

    logger.info("AttendanceBot starting. app_directory=%s", app_directory)

    lock = InstanceLock(app_directory / "attendance-bot.lock")
    try:
        if not lock.acquire():
            message = (
                "Discord 출석 봇이 이미 실행 중입니다.\n"
                "기존 프로그램을 종료한 후 다시 실행해주세요."
            )
            print(message)
            logger.warning("Duplicate AttendanceBot instance blocked.")
            return 2

        settings = load_settings(app_directory)
        configure_logging(logs_directory, settings.log_level)
        logger = logging.getLogger(__name__)
        logger.info("Environment settings loaded.")
        print("AttendanceBot을 시작합니다. Discord 연결을 준비 중입니다...")
        run(settings)
        logger.info("AttendanceBot exited normally.")
        return 0
    except KeyboardInterrupt:
        logger.info("AttendanceBot stopped by Ctrl+C.")
        return 130
    except Exception as exc:
        logger.error("AttendanceBot startup/runtime failure:\n%s", traceback.format_exc())
        print_startup_error(exc, log_path)
        wait_before_exit()
        return 1
    finally:
        lock.release()
        logger.info("AttendanceBot shutdown complete.")
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
