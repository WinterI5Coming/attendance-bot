"""Executable entry point for AttendanceBotManager."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox

from bot.manager.cli import run_cli
from bot.manager.database_service import DatabaseManagerService
from bot.manager.gui import DatabaseManagerGui
from bot.runtime.paths import ensure_runtime_directories, get_app_directory


def configure_manager_logging(logs_directory: Path) -> Path:
    """Configure rotating file logging for the database manager."""

    logs_directory.mkdir(parents=True, exist_ok=True)
    log_path = logs_directory / "database-manager.log"
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    return log_path


def main(argv: list[str] | None = None) -> int:
    """Run CLI commands when provided; otherwise launch the Tkinter GUI."""

    argv = list(sys.argv[1:] if argv is None else argv)
    app_directory = get_app_directory()
    _, logs_directory = ensure_runtime_directories(app_directory)
    (app_directory / "backups").mkdir(parents=True, exist_ok=True)
    log_path = configure_manager_logging(logs_directory)
    logger = logging.getLogger(__name__)
    logger.info("AttendanceBotManager starting. app_directory=%s", app_directory)

    service = DatabaseManagerService(app_directory)
    try:
        if argv:
            return run_cli(argv, service)

        DatabaseManagerGui(service).run()
        return 0
    except Exception as exc:
        logger.exception("AttendanceBotManager failed.")
        if argv:
            print(f"Error: {exc}", file=sys.stderr)
            print(f"Log file: {log_path}", file=sys.stderr)
        else:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "AttendanceBotManager 오류",
                f"{exc}\n\n로그 파일: {log_path}",
            )
            root.destroy()
        return 1
    finally:
        logger.info("AttendanceBotManager exited.")
        logging.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
