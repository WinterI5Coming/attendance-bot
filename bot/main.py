"""디스코드 근태관리봇의 실행 진입점."""

import logging

import discord
from discord.ext import commands

from bot.cogs.setup import SetupCog
from bot.config import load_settings
from bot.db.database import Database
from bot.repositories.guild_repository import GuildRepository
from bot.services.guild_service import GuildService


# .env 파일의 환경변수를 읽는다.
settings = load_settings()


# 로그 출력 형식을 설정한다.
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


# 데이터베이스 객체를 생성한다.
database = Database(settings.db_path)


# Repository는 DB에 직접 접근한다.
guild_repository = GuildRepository(
    database=database,
)


# Service는 서버 설정 관련 규칙을 처리한다.
guild_service = GuildService(
    repository=guild_repository,
    settings=settings,
)


class AttendanceBot(commands.Bot):
    """근태관리봇 Discord 클라이언트."""

    def __init__(self) -> None:
        """기본 Discord Intent로 봇 객체를 초기화한다."""

        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self) -> None:
        """DB, Cog, 슬래시 명령어를 순서대로 준비한다."""

        # 1. DB 파일과 테이블을 준비한다.
        await database.initialize()

        logger.info(
            "데이터베이스 초기화 완료: %s",
            settings.db_path,
        )

        # 2. /초기설정 명령어가 들어 있는 Cog를 봇에 등록한다.
        await self.add_cog(
            SetupCog(
                guild_service=guild_service,
            )
        )

        # 3. 개발 서버에 슬래시 명령어를 동기화한다.
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
    """봇이 Discord 연결을 완료했을 때 실행된다."""

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
    """현재 봇 연결 상태를 사용자에게 반환한다.

    Args:
        interaction:
            `/핑`을 실행한 Discord 상호작용 객체.
    """

    latency_ms = round(
        bot.latency * 1000
    )

    await interaction.response.send_message(
        f"정상 작동 중입니다. 응답 속도: {latency_ms}ms",
        ephemeral=True,
    )


def main() -> None:
    """환경변수에 저장된 Discord Token으로 봇을 실행한다."""

    bot.run(
        settings.discord_token,
    )


if __name__ == "__main__":
    main()