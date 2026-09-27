"""사유 신청, 검토, 정책 슬래시 명령어(`/사유 ...`)를 제공한다.

신청은 모달로, 승인/거절은 선택 메뉴와 버튼으로 처리한다. 화면 흐름은
`bot.ui.views.excuses.ExcuseFlow`가 담당하고 이 Cog는 진입점만 제공한다.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.common import (
    has_officer_access,
    require_guild,
    require_guild_settings,
    require_officer,
)
from bot.runtime.time_provider import TimeProvider
from bot.services.excuse_policy import EXCUSE_TYPE_LABELS
from bot.services.excuse_service import ExcuseService, ExcuseStatus
from bot.services.guild_service import GuildService
from bot.ui.excuse_messages import (
    build_list_embed,
    build_policy_message,
    build_policy_notice,
    message_for_status,
)
from bot.ui.views.excuses import ExcuseFlow

logger = logging.getLogger(__name__)

EXCUSE_TYPE_CHOICES = [
    app_commands.Choice(name=label, value=code)
    for code, label in EXCUSE_TYPE_LABELS.items()
]
EXCUSE_STATUS_CHOICES = [
    app_commands.Choice(name="대기", value="PENDING"),
    app_commands.Choice(name="승인", value="APPROVED"),
    app_commands.Choice(name="거절", value="REJECTED"),
    app_commands.Choice(name="취소", value="CANCELLED"),
]


class ExcusesCog(commands.Cog):
    """대원의 사유 신청과 간부의 검토 명령어를 제공한다."""

    excuses = app_commands.Group(
        name="사유",
        description="결석/지각/조퇴 사유 신청과 승인을 처리합니다.",
        guild_only=True,
    )

    def __init__(
        self,
        *,
        excuse_service: ExcuseService,
        guild_service: GuildService,
        excuse_flow: ExcuseFlow,
        time_provider: TimeProvider | None = None,
    ) -> None:
        self.excuse_service = excuse_service
        self.guild_service = guild_service
        self.flow = excuse_flow
        self.time_provider = time_provider or TimeProvider()

    @excuses.command(name="신청", description="결석/지각/조퇴 사유를 신청합니다. 입력창이 열립니다.")
    async def create_excuse(self, interaction: discord.Interaction) -> None:
        """/사유 신청: 입력 모달을 연다."""

        await self.flow.open_request_modal(interaction)

    @excuses.command(name="취소", description="대기 중인 내 사유 신청을 취소합니다.")
    @app_commands.rename(excuse_request_id="신청번호")
    @app_commands.describe(excuse_request_id="/사유 목록에서 확인한 번호")
    async def cancel_excuse(self, interaction: discord.Interaction, excuse_request_id: int) -> None:
        """/사유 취소 명령을 처리한다."""

        guild = await require_guild(interaction)
        if guild is None:
            return
        result = await self.excuse_service.cancel_request(
            guild_id=guild.id,
            discord_id=interaction.user.id,
            excuse_request_id=excuse_request_id,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(message_for_status(result), ephemeral=True)

    @excuses.command(name="목록", description="사유 신청 목록을 조회합니다.")
    @app_commands.rename(include_all="전체조회", status="상태")
    @app_commands.describe(include_all="간부만: 서버 전체 신청을 조회합니다.")
    @app_commands.choices(status=EXCUSE_STATUS_CHOICES)
    async def list_excuses(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str] | None = None,
        include_all: bool = False,
    ) -> None:
        """/사유 목록 명령을 처리한다."""

        guild = await require_guild(interaction)
        if guild is None:
            return
        result = await self.excuse_service.list_requests(
            guild_id=guild.id,
            discord_id=interaction.user.id,
            status=None if status is None else status.value,
            include_all=include_all,
            can_view_all=await has_officer_access(interaction, self.guild_service),
        )
        await interaction.response.send_message(embed=build_list_embed(result), ephemeral=True)

    @excuses.command(name="검토", description="간부가 대기 중인 사유 신청을 승인하거나 거절합니다.")
    async def review_excuses(self, interaction: discord.Interaction) -> None:
        """/사유 검토: 대기 목록과 승인/거절 버튼을 연다."""

        await self.flow.open_review(interaction)

    @excuses.command(name="예외등록", description="간부가 마감 이후 긴급 예외 사유를 승인 상태로 등록합니다.")
    @app_commands.rename(
        member="사용자",
        target_date="날짜",
        excuse_type="유형",
        reason="사유",
        admin_note="관리자메모",
    )
    @app_commands.choices(excuse_type=EXCUSE_TYPE_CHOICES)
    async def create_override(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        target_date: str,
        excuse_type: app_commands.Choice[str],
        reason: str,
        admin_note: str,
    ) -> None:
        """/사유 예외등록 명령을 처리한다."""

        if await require_officer(interaction, self.guild_service) is None:
            return
        assert interaction.guild is not None
        result = await self.excuse_service.create_admin_override(
            guild_id=interaction.guild.id,
            target_discord_id=member.id,
            actor_discord_id=interaction.user.id,
            target_date=target_date,
            excuse_type=excuse_type.value,
            reason=reason,
            admin_note=admin_note,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(message_for_status(result), ephemeral=True)

    @excuses.command(name="정책", description="사유 신청 정책을 조회하거나, 마감 시간을 변경하거나, 채널에 공지합니다.")
    @app_commands.rename(deadline_time="마감시간", deadline_days_before="마감일수", announce="공지")
    @app_commands.describe(
        deadline_time="변경할 마감 시각 (HH:MM). 마감일수와 함께 입력하면 정책을 변경합니다.",
        deadline_days_before="변경할 마감 일수 (출석일 N일 전)",
        announce="True면 현재 정책을 채널에 공개 공지합니다.",
    )
    async def policy(
        self,
        interaction: discord.Interaction,
        deadline_time: str | None = None,
        deadline_days_before: int | None = None,
        announce: bool = False,
    ) -> None:
        """/사유 정책 명령을 처리한다.

        인자가 없으면 현재 정책을 비공개로 보여 주고, 마감시간과 마감일수를
        함께 주면 정책을 변경하며, 공지=True면 현재 정책을 채널에 공개한다.
        변경과 공지는 간부 이상만 실행할 수 있다.
        """

        wants_update = deadline_time is not None or deadline_days_before is not None
        if wants_update and (deadline_time is None or deadline_days_before is None):
            await interaction.response.send_message(
                "정책을 변경하려면 마감시간과 마감일수를 모두 입력해주세요.",
                ephemeral=True,
            )
            return

        if not wants_update and not announce:
            settings = await require_guild_settings(interaction, self.guild_service)
            if settings is None:
                return
            await interaction.response.send_message(build_policy_message(settings), ephemeral=True)
            return

        settings = await require_officer(interaction, self.guild_service)
        if settings is None:
            return
        assert interaction.guild is not None

        if wants_update:
            result = await self.excuse_service.update_policy(
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                deadline_time=deadline_time,
                deadline_days_before=deadline_days_before,
                now=self.time_provider.now_utc(),
            )
            if result.status is not ExcuseStatus.POLICY_UPDATED or not announce:
                await interaction.response.send_message(message_for_status(result), ephemeral=True)
                return
            settings = await self.guild_service.get_settings(interaction.guild.id) or settings

        await interaction.response.send_message(build_policy_notice(settings))
