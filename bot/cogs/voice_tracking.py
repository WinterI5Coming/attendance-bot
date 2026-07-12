"""Discord 음성 상태 이벤트를 출석 검증 서비스로 전달한다."""

from datetime import datetime, timezone
import logging

import discord
from discord.ext import commands

from bot.services.voice_verification_service import VoiceVerificationService


logger = logging.getLogger(__name__)


class VoiceTrackingCog(commands.Cog):
    """검증 대상 음성 상태 변경을 서비스 계층으로 전달한다."""

    def __init__(
        self,
        *,
        voice_verification_service: VoiceVerificationService,
    ) -> None:
        """Cog 의존성을 초기화한다."""

        self.voice_verification_service = voice_verification_service

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """설정된 음성 채널의 입장, 퇴장, 이동 이벤트를 처리한다."""

        if member.bot or member.guild is None:
            return

        before_channel = before.channel
        after_channel = after.channel
        if before_channel == after_channel:
            return

        try:
            await self.voice_verification_service.handle_voice_update(
                guild_id=member.guild.id,
                discord_id=member.id,
                before_channel_id=None if before_channel is None else before_channel.id,
                before_category_id=(
                    None
                    if before_channel is None or before_channel.category is None
                    else before_channel.category.id
                ),
                after_channel_id=None if after_channel is None else after_channel.id,
                after_category_id=(
                    None
                    if after_channel is None or after_channel.category is None
                    else after_channel.category.id
                ),
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception(
                "Voice verification update failed: guild_id=%s member_id=%s",
                member.guild.id,
                member.id,
            )
