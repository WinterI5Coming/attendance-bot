"""애플리케이션 환경변수를 읽고 검증하는 모듈."""

from dataclasses import dataclass
from datetime import datetime, time
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent

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


@dataclass(frozen=True)
class Settings:
    """근태관리봇 실행에 필요한 설정값."""

    discord_token: str
    development_guild_id: int
    db_path: Path
    timezone: str
    log_level: str

    default_attendance_days: str
    default_attendance_start: str
    default_late_deadline: str
    default_close_deadline: str
    default_excuse_mode: str


def _parse_time(value: str, variable_name: str) -> time:
    """HH:MM 형식의 환경변수를 time 객체로 변환한다.

    Args:
        value:
            변환할 시간 문자열.
        variable_name:
            오류 메시지에 표시할 환경변수 이름.

    Returns:
        파싱된 time 객체.

    Raises:
        RuntimeError:
            시간이 HH:MM 형식이 아닌 경우.
    """

    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise RuntimeError(
            f"{variable_name}은 HH:MM 형식이어야 합니다: {value}"
        ) from exc


def _validate_attendance_days(value: str) -> str:
    """출석 요일 환경변수를 검증하고 정규화한다."""

    days = [
        day.strip().upper()
        for day in value.split(",")
        if day.strip()
    ]

    if not days:
        raise RuntimeError(
            "DEFAULT_ATTENDANCE_DAYS에는 최소 한 개의 요일이 필요합니다."
        )

    invalid_days = [
        day
        for day in days
        if day not in ALLOWED_ATTENDANCE_DAYS
    ]

    if invalid_days:
        raise RuntimeError(
            "잘못된 출석 요일이 있습니다: "
            + ", ".join(invalid_days)
        )

    # 중복 요일을 입력해도 최초 등장 순서를 유지하며 제거한다.
    normalized_days = list(dict.fromkeys(days))

    return ",".join(normalized_days)


def load_settings() -> Settings:
    """`.env` 파일에서 설정을 읽고 유효성을 검사한다."""

    load_dotenv(PROJECT_ROOT / ".env")

    discord_token = os.getenv("DISCORD_TOKEN")
    guild_id_value = os.getenv("DEVELOPMENT_GUILD_ID")

    missing_variables: list[str] = []

    if not discord_token:
        missing_variables.append("DISCORD_TOKEN")

    if not guild_id_value:
        missing_variables.append("DEVELOPMENT_GUILD_ID")

    if missing_variables:
        raise RuntimeError(
            "필수 환경변수가 설정되지 않았습니다: "
            + ", ".join(missing_variables)
        )

    try:
        development_guild_id = int(guild_id_value)
    except ValueError as exc:
        raise RuntimeError(
            "DEVELOPMENT_GUILD_ID는 숫자여야 합니다."
        ) from exc

    db_path = Path(
        os.getenv(
            "DB_PATH",
            "data/attendance.db",
        )
    )

    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    timezone_name = os.getenv(
        "TIMEZONE",
        "Asia/Seoul",
    )

    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"유효하지 않은 TIMEZONE입니다: {timezone_name}"
        ) from exc

    log_level = os.getenv(
        "LOG_LEVEL",
        "INFO",
    ).upper()

    allowed_log_levels = {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }

    if log_level not in allowed_log_levels:
        raise RuntimeError(
            "LOG_LEVEL은 DEBUG, INFO, WARNING, ERROR, "
            "CRITICAL 중 하나여야 합니다."
        )

    attendance_days = _validate_attendance_days(
        os.getenv(
            "DEFAULT_ATTENDANCE_DAYS",
            "MON,TUE,WED,THU,FRI",
        )
    )

    attendance_start = os.getenv(
        "DEFAULT_ATTENDANCE_START",
        "20:00",
    )

    late_deadline = os.getenv(
        "DEFAULT_LATE_DEADLINE",
        "20:15",
    )

    close_deadline = os.getenv(
        "DEFAULT_CLOSE_DEADLINE",
        "20:30",
    )

    start_time = _parse_time(
        attendance_start,
        "DEFAULT_ATTENDANCE_START",
    )

    late_time = _parse_time(
        late_deadline,
        "DEFAULT_LATE_DEADLINE",
    )

    close_time = _parse_time(
        close_deadline,
        "DEFAULT_CLOSE_DEADLINE",
    )

    if not start_time < late_time < close_time:
        raise RuntimeError(
            "출석 시간은 반드시 "
            "시작 < 지각 < 마감 순서여야 합니다."
        )

    excuse_mode = os.getenv(
        "DEFAULT_EXCUSE_MODE",
        "officer_approval",
    )

    if excuse_mode not in ALLOWED_EXCUSE_MODES:
        raise RuntimeError(
            "DEFAULT_EXCUSE_MODE는 auto 또는 "
            "officer_approval이어야 합니다."
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
    )