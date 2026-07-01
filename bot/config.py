"""애플리케이션 환경변수를 읽고 검증하는 모듈."""

from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """봇 실행에 필요한 설정값.

    Attributes:
        discord_token: Discord Bot 인증 토큰.
        development_guild_id: 개발 중 슬래시 명령어를 동기화할 서버 ID.
    """

    discord_token: str
    development_guild_id: int


def load_settings() -> Settings:
    """환경변수에서 봇 설정을 읽어 검증한다.

    Returns:
        검증이 완료된 Settings 객체.

    Raises:
        RuntimeError: 필수 환경변수가 없거나 서버 ID 형식이 잘못된 경우.
    """

    load_dotenv()

    discord_token = os.getenv("DISCORD_TOKEN")
    guild_id_value = os.getenv("DEVELOPMENT_GUILD_ID")

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

    return Settings(
        discord_token=discord_token,
        development_guild_id=development_guild_id,
    )