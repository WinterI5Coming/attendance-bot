"""디스코드 근태관리봇의 최초 실행 진입점."""

import logging

import discord
from discord.ext import commands

from bot.config import load_settings


settings = load_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


class AttendanceBot(commands.Bot):
    """근태관리봇 애플리케이션.

    개발 단계에서는 지정된 개발 서버에 슬래시 명령어를 동기화한다.
    """

    def __init__(self) -> None:
        """기본 Discord Intent로 봇을 초기화한다."""

        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self) -> None:
        """Discord 연결 전에 슬래시 명령어를 개발 서버에 동기화한다.

        개발 서버 단위 동기화를 사용하면 명령어 변경을 빠르게 확인할 수
        있다. 운영 배포 시에는 전역 명령어 동기화 방식으로 변경한다.
        """

        development_guild = discord.Object(
            id=settings.development_guild_id
        )

        # 전역 명령어로 등록된 명령을 개발 서버 명령어로 복사한다.
        self.tree.copy_global_to(guild=development_guild)

        synced_commands = await self.tree.sync(
            guild=development_guild
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
        logger.warning("봇 사용자 정보를 확인하지 못했습니다.")
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
async def ping(interaction: discord.Interaction) -> None:
    """사용자에게 봇 응답 상태를 전달한다.

    Args:
        interaction: Discord 슬래시 명령어 상호작용 객체.
    """

    latency_ms = round(bot.latency * 1000)

    await interaction.response.send_message(
        f"정상 작동 중입니다. 응답 속도: {latency_ms}ms",
        ephemeral=True,
    )


def main() -> None:
    """환경변수에 설정된 Token으로 봇을 실행한다."""

    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()