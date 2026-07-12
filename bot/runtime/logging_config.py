"""Logging configuration for console and rotating file logs."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


LOG_FILE_NAME = "attendance-bot.log"


def configure_logging(logs_directory: Path, log_level: str = "INFO") -> Path:
    """Configure root logging and return the log file path."""

    logs_directory.mkdir(parents=True, exist_ok=True)
    log_path = logs_directory / LOG_FILE_NAME

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    return log_path


def shutdown_logging() -> None:
    """Flush and close all logging handlers."""

    logging.shutdown()
