"""봇 상태 확인처럼 특정 기능 Cog에 속하지 않는 시스템 명령을 등록한다."""

from __future__ import annotations

import discord

from bot.bot_client import AttendanceBot
from bot.utils.discord_messages import info_embed


def register_system_commands(bot: AttendanceBot) -> None:
    """
    모든 서버에서 사용할 수 있는 전역 시스템 슬래시 명령을 등록한다.

    Args:
        bot: 명령을 등록할 출석 봇 클라이언트.
    """

    @bot.tree.command(
        name="핑",
        description="봇의 연결 상태와 응답 속도를 확인합니다.",
    )
    async def ping(interaction: discord.Interaction) -> None:
        """현재 Discord Gateway 지연 시간을 사용자에게 알려준다."""

        latency_ms = round(bot.latency * 1000)
        await interaction.response.send_message(
            embed=info_embed(
                title="봇 상태",
                description="정상 작동 중입니다.",
                fields=(("응답 속도", f"{latency_ms}ms", True),),
            ),
            ephemeral=True,
        )
