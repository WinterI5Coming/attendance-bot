"""Discord 음성 상태 이벤트를 출석 검증 서비스로 전달한다."""

import logging
from typing import Any

import discord
from discord.ext import commands

from bot.runtime.time_provider import TimeProvider
from bot.services.voice_verification_service import VoiceVerificationService

logger = logging.getLogger(__name__)


class VoiceTrackingCog(commands.Cog):
    """검증 대상 음성 상태 변경을 서비스 계층으로 전달한다."""

    def __init__(
        self,
        *,
        voice_verification_service: VoiceVerificationService,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Cog 의존성을 초기화한다."""

        self.voice_verification_service = voice_verification_service
        self.time_provider = time_provider or TimeProvider()
        self.bot: Any | None = None

    def attach_bot(self, bot: Any) -> None:
        """재시작 복구 시 길드 음성 상태를 읽기 위해 클라이언트를 연결한다."""

        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """연결(재연결) 직후 실제 음성 채널 재실 상태와 열린 로그를 맞춘다."""

        await self.reconcile_all_guilds()

    async def reconcile_all_guilds(self) -> None:
        """봇이 속한 모든 서버의 음성 재실 상태를 검증 로그와 동기화한다."""

        if self.bot is None:
            return
        now = self.time_provider.now_utc()
        for guild in list(getattr(self.bot, "guilds", [])):
            present: dict[str, tuple[str, str | None]] = {}
            channels = list(getattr(guild, "voice_channels", [])) + list(
                getattr(guild, "stage_channels", [])
            )
            for channel in channels:
                category = getattr(channel, "category", None)
                category_id = None if category is None else str(category.id)
                for user_id in getattr(channel, "voice_states", {}):
                    present[str(user_id)] = (str(channel.id), category_id)
            try:
                await self.voice_verification_service.reconcile_guild_voice_presence(
                    guild_id=guild.id,
                    present=present,
                    now=now,
                )
            except Exception:
                logger.exception("Voice presence reconcile failed: guild_id=%s", guild.id)

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
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception(
                "Voice verification update failed: guild_id=%s member_id=%s",
                member.guild.id,
                member.id,
            )
