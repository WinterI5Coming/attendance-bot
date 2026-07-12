"""Discord 연결 수명주기와 명령 오류 이벤트를 등록한다."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from bot.bot_client import AttendanceBot


logger = logging.getLogger(__name__)


def register_lifecycle_events(bot: AttendanceBot) -> None:
    """
    Discord 연결 상태와 명령 오류를 기록하는 이벤트 핸들러를 등록한다.

    Args:
        bot: 이벤트 핸들러를 연결할 출석 봇 클라이언트.
    """

    @bot.event
    async def on_ready() -> None:
        if bot.user is None:
            logger.warning("Connected, but bot user information is unavailable.")
            return
        logger.info("Discord login complete: %s (%s)", bot.user, bot.user.id)

    @bot.event
    async def on_disconnect() -> None:
        logger.warning("Discord connection lost.")

    @bot.event
    async def on_resumed() -> None:
        logger.info("Discord connection resumed.")

    @bot.event
    async def on_command_error(
        context: commands.Context,
        error: commands.CommandError,
    ) -> None:
        logger.exception(
            "Prefix command failed: command=%s author=%s",
            getattr(context.command, "qualified_name", None),
            getattr(context.author, "id", None),
            exc_info=error,
        )

    async def on_app_command_error(
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        logger.exception(
            "Slash command failed: command=%s user=%s",
            getattr(interaction.command, "qualified_name", None),
            getattr(interaction.user, "id", None),
            exc_info=error,
        )

    bot.tree.on_error = on_app_command_error
