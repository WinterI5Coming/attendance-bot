"""출석 시작 공지에 붙는 [출석하기] 버튼."""

from __future__ import annotations

import logging

import discord

from bot.cogs.common import GUILD_ONLY_MESSAGE
from bot.runtime.time_provider import TimeProvider
from bot.services.attendance_service import AttendanceService
from bot.ui.attendance_messages import build_check_in_message, current_voice_location

logger = logging.getLogger(__name__)

CHECK_IN_CUSTOM_ID = "attendance:checkin"


class CheckInView(discord.ui.View):
    """봇 재시작 후에도 동작하는 영속 [출석하기] 버튼 뷰.

    `timeout=None`과 고정 `custom_id` 덕분에 `bot.add_view()`로 등록해 두면
    과거 공지 메시지의 버튼도 계속 처리된다.
    """

    def __init__(
        self,
        *,
        attendance_service: AttendanceService,
        time_provider: TimeProvider,
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.attendance_service = attendance_service
        self.time_provider = time_provider

        button: discord.ui.Button = discord.ui.Button(
            label="출석 마감" if disabled else "출석하기",
            style=discord.ButtonStyle.secondary if disabled else discord.ButtonStyle.success,
            emoji="🔒" if disabled else "✅",
            custom_id=CHECK_IN_CUSTOM_ID,
            disabled=disabled,
        )
        button.callback = self._on_check_in
        self.add_item(button)

    def closed(self) -> CheckInView:
        """마감된 세션 공지에 사용할 비활성 버튼 뷰를 만든다."""

        return CheckInView(
            attendance_service=self.attendance_service,
            time_provider=self.time_provider,
            disabled=True,
        )

    async def _on_check_in(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(GUILD_ONLY_MESSAGE, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        voice_channel_id, voice_category_id = current_voice_location(interaction.user)
        try:
            result = await self.attendance_service.check_in(
                guild_id=guild.id,
                discord_id=interaction.user.id,
                now=self.time_provider.now_utc(),
                current_voice_channel_id=voice_channel_id,
                current_voice_category_id=voice_category_id,
            )
        except Exception:
            logger.exception(
                "Button check-in failed. guild_id=%s discord_id=%s",
                guild.id,
                interaction.user.id,
            )
            await interaction.followup.send(
                "❌ 출석 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(build_check_in_message(result), ephemeral=True)
