"""개발 환경에서 Discord 출석 봇을 실행하는 진입점."""

from bot.bot_factory import create_bot
from bot.config import Settings, load_settings


def run(settings: Settings) -> None:
    """
    검증된 설정으로 Discord 봇을 실행한다.

    Args:
        settings: `.env`에서 읽어 검증한 실행 설정.
    """

    bot = create_bot(settings)
    bot.run(settings.discord_token)


def main() -> None:
    """로컬 Python 개발 환경에서 설정을 읽고 봇을 실행한다."""

    run(load_settings())


if __name__ == "__main__":
    main()
