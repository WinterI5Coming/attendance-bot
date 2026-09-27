"""환경 변수(또는 로컬 `.env`)에서 읽어 검증한 실행 설정."""

import os
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from bot.runtime.paths import get_app_directory

PROJECT_ROOT = get_app_directory()
ALLOWED_ATTENDANCE_DAYS = {
    "MON",
    "TUE",
    "WED",
    "THU",
    "FRI",
    "SAT",
    "SUN",
}

ALLOWED_EXCUSE_MODES = {
    "auto",
    "officer_approval",
}

TRUE_VALUES = {
    "1",
    "true",
    "yes",
    "on",
}

FALSE_VALUES = {
    "0",
    "false",
    "no",
    "off",
}


@dataclass(frozen=True)
class Settings:
    """Validated settings required to run the Discord attendance bot."""

    discord_token: str
    development_guild_id: int | None
    db_path: Path
    timezone: str
    log_level: str
    default_attendance_days: str
    default_attendance_start: str
    default_late_deadline: str
    default_close_deadline: str
    default_excuse_mode: str
    default_excuse_deadline_time: str
    default_excuse_deadline_days_before: int
    default_require_excuse_approval: bool
    default_allow_late_excuse: bool


def _parse_time(value: str, variable_name: str) -> time:
    """Parse a strict `HH:MM` environment variable value."""

    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise RuntimeError(
            f"{variable_name} must use HH:MM format. Current value: {value}"
        ) from exc


def _parse_bool(value: str, variable_name: str) -> bool:
    """Parse common boolean strings used in `.env` files."""

    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise RuntimeError(
        f"{variable_name} must be one of true/false, 1/0, yes/no, on/off."
    )


def _validate_attendance_days(value: str) -> str:
    """Validate attendance weekdays and remove duplicates while preserving order."""

    days = [
        day.strip().upper()
        for day in value.split(",")
        if day.strip()
    ]

    if not days:
        raise RuntimeError("DEFAULT_ATTENDANCE_DAYS must include at least one day.")

    invalid_days = [
        day
        for day in days
        if day not in ALLOWED_ATTENDANCE_DAYS
    ]

    if invalid_days:
        raise RuntimeError("Invalid attendance days: " + ", ".join(invalid_days))

    return ",".join(dict.fromkeys(days))



def load_settings(app_directory: Path = PROJECT_ROOT) -> Settings:
    """환경 변수와 선택적 `.env`에서 설정을 읽고 검증한다."""

    # `.env`는 로컬 개발 편의용이다. 컨테이너/서버에서는 환경 변수만으로 실행한다.
    env_path = app_directory / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    discord_token = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
    if not discord_token:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN is not set. Export it as an environment variable "
            "or put it in .env."
        )

    # 설정하면 해당 서버에만 즉시 동기화한다(개발용). 비우면 글로벌 동기화.
    development_guild_id: int | None = None
    guild_id_value = os.getenv("DEVELOPMENT_GUILD_ID", "").strip()
    if guild_id_value:
        try:
            development_guild_id = int(guild_id_value)
        except ValueError as exc:
            raise RuntimeError("DEVELOPMENT_GUILD_ID must be a number.") from exc

    data_directory = Path(os.getenv("DATA_DIR") or app_directory / "data")
    db_path = data_directory / "attendance.db"

    timezone_name = os.getenv("TIMEZONE", "Asia/Seoul")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Invalid TIMEZONE: {timezone_name}") from exc

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    allowed_log_levels = {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }
    if log_level not in allowed_log_levels:
        raise RuntimeError(
            "LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL."
        )

    attendance_days = _validate_attendance_days(
        os.getenv("DEFAULT_ATTENDANCE_DAYS", "MON,TUE,WED,THU,FRI")
    )
    attendance_start = os.getenv("DEFAULT_ATTENDANCE_START", "20:00")
    late_deadline = os.getenv("DEFAULT_LATE_DEADLINE", "20:15")
    close_deadline = os.getenv("DEFAULT_CLOSE_DEADLINE", "20:30")

    start_time = _parse_time(attendance_start, "DEFAULT_ATTENDANCE_START")
    late_time = _parse_time(late_deadline, "DEFAULT_LATE_DEADLINE")
    close_time = _parse_time(close_deadline, "DEFAULT_CLOSE_DEADLINE")

    if not start_time < late_time < close_time:
        raise RuntimeError(
            "Attendance times must be ordered as start < late < close."
        )

    excuse_mode = os.getenv("DEFAULT_EXCUSE_MODE", "officer_approval")
    if excuse_mode not in ALLOWED_EXCUSE_MODES:
        raise RuntimeError(
            "DEFAULT_EXCUSE_MODE must be auto or officer_approval."
        )
    excuse_deadline_time = os.getenv("EXCUSE_DEADLINE_TIME", "23:00")
    _parse_time(excuse_deadline_time, "EXCUSE_DEADLINE_TIME")

    try:
        excuse_deadline_days_before = int(
            os.getenv("EXCUSE_DEADLINE_DAYS_BEFORE", "1")
        )
    except ValueError as exc:
        raise RuntimeError("EXCUSE_DEADLINE_DAYS_BEFORE must be a number.") from exc
    if excuse_deadline_days_before < 0:
        raise RuntimeError("EXCUSE_DEADLINE_DAYS_BEFORE must be 0 or greater.")

    require_excuse_approval = _parse_bool(
        os.getenv("REQUIRE_EXCUSE_APPROVAL", "true"),
        "REQUIRE_EXCUSE_APPROVAL",
    )
    allow_late_excuse = _parse_bool(
        os.getenv("ALLOW_LATE_EXCUSE", "false"),
        "ALLOW_LATE_EXCUSE",
    )

    return Settings(
        discord_token=discord_token,
        development_guild_id=development_guild_id,
        db_path=db_path,
        timezone=timezone_name,
        log_level=log_level,
        default_attendance_days=attendance_days,
        default_attendance_start=attendance_start,
        default_late_deadline=late_deadline,
        default_close_deadline=close_deadline,
        default_excuse_mode=excuse_mode,
        default_excuse_deadline_time=excuse_deadline_time,
        default_excuse_deadline_days_before=excuse_deadline_days_before,
        default_require_excuse_approval=require_excuse_approval,
        default_allow_late_excuse=allow_late_excuse,
    )
