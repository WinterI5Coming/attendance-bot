"""애플리케이션 환경변수를 읽고 검증하는 모듈."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


# 프로젝트 최상위 디렉터리 경로다.
# bot/config.py 기준으로 두 단계 위가 attendance-bot 폴더다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """근태관리봇 실행에 필요한 설정값.

    Attributes:
        discord_token:
            Discord Bot 계정으로 로그인할 때 사용하는 비밀 토큰.
        development_guild_id:
            개발 중 슬래시 명령어를 빠르게 동기화할 Discord 서버 ID.
        db_path:
            SQLite 데이터베이스 파일의 절대 경로.
        timezone:
            출석 시간 판정과 사용자 표시에서 사용할 서버 시간대.
        log_level:
            애플리케이션 로그 출력 수준.
    """

    discord_token: str
    development_guild_id: int
    db_path: Path
    timezone: str
    log_level: str


def load_settings() -> Settings:
    """`.env` 파일에서 설정을 읽고 유효성을 검사한다.

    상대 경로로 입력된 DB_PATH는 프로젝트 최상위 디렉터리를
    기준으로 절대 경로로 변환한다.

    Returns:
        검증이 완료된 Settings 객체.

    Raises:
        RuntimeError:
            필수 환경변수가 없거나 서버 ID가 숫자가 아닌 경우.
    """

    load_dotenv(PROJECT_ROOT / ".env")

    discord_token = os.getenv("DISCORD_TOKEN")
    guild_id_value = os.getenv("DEVELOPMENT_GUILD_ID")

    db_path_value = os.getenv(
        "DB_PATH",
        "data/attendance.db",
    )

    timezone = os.getenv(
        "TIMEZONE",
        "Asia/Seoul",
    )

    log_level = os.getenv(
        "LOG_LEVEL",
        "INFO",
    ).upper()

    missing_variables: list[str] = []

    if not discord_token:
        missing_variables.append("DISCORD_TOKEN")

    if not guild_id_value:
        missing_variables.append("DEVELOPMENT_GUILD_ID")

    if missing_variables:
        joined_names = ", ".join(missing_variables)
        raise RuntimeError(
            f"필수 환경변수가 설정되지 않았습니다: {joined_names}"
        )

    try:
        development_guild_id = int(guild_id_value)
    except ValueError as exc:
        raise RuntimeError(
            "DEVELOPMENT_GUILD_ID는 숫자로 입력해야 합니다."
        ) from exc

    db_path = Path(db_path_value)

    # 상대 경로는 프로젝트 최상위 경로를 기준으로 계산한다.
    # 실행 위치가 달라져도 동일한 DB 파일을 사용하기 위한 처리다.
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

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

    return Settings(
        discord_token=discord_token,
        development_guild_id=development_guild_id,
        db_path=db_path,
        timezone=timezone,
        log_level=log_level,
    )