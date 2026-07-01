"""디스코드 근태관리봇의 실행 진입점."""

import logging

import discord
from discord.ext import commands

from bot.config import load_settings
from bot.db.database import Database


settings = load_settings()

logging.basicConfig(
    level=getattr(
        logging,
        settings.log_level,
        logging.INFO,
    ),
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger(__name__)

database = Database(settings.db_path)


class AttendanceBot(commands.Bot):
    """근태관리봇 Discord 클라이언트."""

    def __init__(self) -> None:
        """기본 Intent로 봇 객체를 초기화한다."""

        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self) -> None:
        """Discord 연결 전에 DB와 슬래시 명령어를 준비한다.

        실행 순서:
            1. SQLite 연결과 마이그레이션 실행
            2. 개발 서버에 슬래시 명령어 동기화
        """

        await database.initialize()

        logger.info(
            "데이터베이스 초기화 완료: %s",
            settings.db_path,
        )

        development_guild = discord.Object(
            id=settings.development_guild_id,
        )

        self.tree.copy_global_to(
            guild=development_guild,
        )

        synced_commands = await self.tree.sync(
            guild=development_guild,
        )

        logger.info(
            "개발 서버에 슬래시 명령어 %d개를 동기화했습니다.",
            len(synced_commands),
        )


bot = AttendanceBot()


@bot.event
async def on_ready() -> None:
    """Discord 연결이 완료되면 봇 정보를 로그로 출력한다."""

    if bot.user is None:
        logger.warning(
            "봇 사용자 정보를 확인하지 못했습니다."
        )
        return

    logger.info(
        "봇 연결 완료: %s (%s)",
        bot.user,
        bot.user.id,
    )


@bot.tree.command(
    name="핑",
    description="봇의 연결 상태와 응답 속도를 확인합니다.",
)
async def ping(
    interaction: discord.Interaction,
) -> None:
    """현재 Discord 연결 상태를 사용자에게 반환한다.

    Args:
        interaction:
            `/핑`을 실행한 Discord 상호작용 정보.
    """

    latency_ms = round(
        bot.latency * 1000
    )

    await interaction.response.send_message(
        f"정상 작동 중입니다. 응답 속도: {latency_ms}ms",
        ephemeral=True,
    )


def main() -> None:
    """환경변수의 Discord Token으로 봇을 실행한다."""

    bot.run(
        settings.discord_token,
    )


if __name__ == "__main__":
    main()