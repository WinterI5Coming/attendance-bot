"""Discord 출석 봇 실행 진입점 (`python main.py`)."""

from __future__ import annotations

import logging

from bot.config import load_settings
from bot.main import run
from bot.runtime.logging_config import configure_logging, shutdown_logging
from bot.runtime.paths import ensure_runtime_directories, get_app_directory


def main() -> int:
    """설정을 읽고 로깅을 준비한 뒤 봇을 실행한다."""

    app_directory = get_app_directory()
    _, logs_directory = ensure_runtime_directories(app_directory)
    configure_logging(logs_directory)
    logger = logging.getLogger(__name__)

    try:
        settings = load_settings(app_directory)
        configure_logging(logs_directory, settings.log_level)
        logger.info("AttendanceBot starting. data=%s", settings.db_path.parent)
        run(settings)
        return 0
    except KeyboardInterrupt:
        logger.info("AttendanceBot stopped by Ctrl+C.")
        return 130
    except Exception:
        logger.exception("AttendanceBot failed to start.")
        return 1
    finally:
        logger.info("AttendanceBot shutdown complete.")
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
