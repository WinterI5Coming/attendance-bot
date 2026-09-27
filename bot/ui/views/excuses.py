"""사유 신청 모달과 간부 검토(승인/거절) 컴포넌트.

`ExcuseFlow`가 서비스 호출과 화면 전환을 담당하고, 모달/뷰 클래스는 사용자
입력을 받아 Flow에 넘기는 얇은 껍데기다. 슬래시 명령(`/사유 신청`,
`/사유 검토`)과 공지 메시지의 버튼이 같은 Flow를 공유한다.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import discord

from bot.cogs.common import require_guild_settings, require_officer
from bot.runtime.time_provider import TimeProvider
from bot.services.excuse_policy import EXCUSE_TYPE_LABELS
from bot.services.excuse_service import ExcuseService, ExcuseStatus
from bot.services.guild_service import GuildService
from bot.ui.embed_factory import EMBEDS
from bot.ui.excuse_messages import (
    build_create_message,
    build_detail_embed,
    build_pending_notice_embed,
    excuse_type_label,
    message_for_status,
)
from bot.utils.discord_channels import send_channel_message

logger = logging.getLogger(__name__)

EXCUSE_REVIEW_CUSTOM_ID = "excuse:review"
MAX_SELECT_OPTIONS = 25


class ExcuseFlow:
    """사유 신청/검토 화면 흐름과 서비스 호출을 묶는다."""

    def __init__(
        self,
        *,
        excuse_service: ExcuseService,
        guild_service: GuildService,
        time_provider: TimeProvider,
    ) -> None:
        self.excuse_service = excuse_service
        self.guild_service = guild_service
        self.time_provider = time_provider

    # ----- 신청 -----

    async def open_request_modal(self, interaction: discord.Interaction) -> None:
        """`/사유 신청`: 유형/날짜/사유를 입력받는 모달을 연다."""

        settings = await require_guild_settings(interaction, self.guild_service)
        if settings is None:
            return
        local_now = self.time_provider.now_utc().astimezone(ZoneInfo(settings["timezone"]))
        default_date = (local_now + timedelta(days=1)).date().isoformat()
        await interaction.response.send_modal(ExcuseRequestModal(self, default_date=default_date))

    async def submit_request(
        self,
        interaction: discord.Interaction,
        *,
        excuse_type: str,
        target_date: str,
        reason: str,
    ) -> None:
        """모달 제출을 서비스에 전달하고, 접수되면 간부에게 알린다."""

        guild = interaction.guild
        if guild is None:
            return
        result = await self.excuse_service.create_request(
            guild_id=guild.id,
            discord_id=interaction.user.id,
            target_date=target_date.strip(),
            expected_time=None,
            reason=reason,
            now=self.time_provider.now_utc(),
            excuse_type=excuse_type,
        )
        await interaction.response.send_message(build_create_message(result), ephemeral=True)

        if result.status is ExcuseStatus.CREATED_PENDING and result.request is not None:
            await self._notify_officers(interaction, result.request)

    async def _notify_officers(self, interaction: discord.Interaction, request: dict[str, Any]) -> None:
        """공지 채널에 신청 접수 알림과 [검토하기] 버튼을 올린다."""

        assert interaction.guild is not None
        settings = await self.guild_service.get_settings(interaction.guild.id)
        if settings is None:
            return
        channel_id = settings["announcement_channel_id"] or settings["attendance_channel_id"]
        await send_channel_message(
            interaction.client,
            channel_id,
            embed=build_pending_notice_embed(request, interaction.user),
            view=ExcuseNoticeView(self),
        )

    # ----- 검토 -----

    async def list_pending(self, guild_id: int, actor_id: int) -> list[dict[str, Any]]:
        result = await self.excuse_service.list_requests(
            guild_id=guild_id,
            discord_id=actor_id,
            status="PENDING",
            include_all=True,
            can_view_all=True,
        )
        rows = [] if result.request is None else result.request.get("rows", [])
        return rows[:MAX_SELECT_OPTIONS]

    async def open_review(self, interaction: discord.Interaction) -> None:
        """`/사유 검토`와 [검토하기] 버튼: 대기 중인 신청 목록을 비공개로 연다."""

        if await require_officer(interaction, self.guild_service) is None:
            return
        assert interaction.guild is not None
        rows = await self.list_pending(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(
            embed=self._pending_embed(rows),
            view=ExcuseReviewView(self, rows) if rows else None,
            ephemeral=True,
        )

    async def refresh_review(
        self,
        interaction: discord.Interaction,
        *,
        notice: str,
    ) -> None:
        """승인/거절 뒤 결과 문구와 남은 대기 목록으로 검토 화면을 갱신한다."""

        assert interaction.guild is not None
        rows = await self.list_pending(interaction.guild.id, interaction.user.id)
        await interaction.response.edit_message(
            content=notice,
            embed=self._pending_embed(rows),
            view=ExcuseReviewView(self, rows) if rows else None,
        )

    async def approve(self, interaction: discord.Interaction, request_id: int) -> None:
        assert interaction.guild is not None
        result = await self.excuse_service.approve_request(
            guild_id=interaction.guild.id,
            excuse_request_id=request_id,
            actor_discord_id=interaction.user.id,
            now=self.time_provider.now_utc(),
        )
        await self.refresh_review(interaction, notice=f"#{request_id}: {message_for_status(result)}")

    async def reject(self, interaction: discord.Interaction, request_id: int, reason: str) -> None:
        assert interaction.guild is not None
        result = await self.excuse_service.reject_request(
            guild_id=interaction.guild.id,
            excuse_request_id=request_id,
            actor_discord_id=interaction.user.id,
            rejection_reason=reason,
            now=self.time_provider.now_utc(),
        )
        await self.refresh_review(interaction, notice=f"#{request_id}: {message_for_status(result)}")

    def _pending_embed(self, rows: list[dict[str, Any]]) -> discord.Embed:
        if not rows:
            return EMBEDS.info("사유 검토", "대기 중인 사유 신청이 없습니다.")
        return EMBEDS.admin(
            f"사유 검토 · 대기 {len(rows)}건",
            "아래 목록에서 신청을 선택하면 상세 내용과 승인/거절 버튼이 나타납니다.",
        )


class ExcuseRequestModal(discord.ui.Modal, title="사유 신청"):
    """유형(선택), 날짜, 사유를 입력받는 모달."""

    excuse_type = discord.ui.Label(
        text="유형",
        component=discord.ui.Select(
            placeholder="결석 / 지각 / 조퇴",
            options=[
                discord.SelectOption(label=label, value=code)
                for code, label in EXCUSE_TYPE_LABELS.items()
            ],
        ),
    )
    target_date = discord.ui.TextInput(
        label="대상 출석일 (YYYY-MM-DD)",
        placeholder="2026-07-03",
        min_length=10,
        max_length=10,
    )
    reason = discord.ui.TextInput(
        label="사유",
        style=discord.TextStyle.paragraph,
        placeholder="2자 이상 500자 이하",
        min_length=2,
        max_length=500,
    )

    def __init__(self, flow: ExcuseFlow, *, default_date: str) -> None:
        super().__init__()
        self.flow = flow
        self.target_date.default = default_date

    async def on_submit(self, interaction: discord.Interaction) -> None:
        select = self.excuse_type.component
        assert isinstance(select, discord.ui.Select)
        await self.flow.submit_request(
            interaction,
            excuse_type=select.values[0],
            target_date=self.target_date.value,
            reason=self.reason.value,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Excuse request modal failed.", exc_info=error)
        await _reply_error(interaction, "사유 신청 처리 중 오류가 발생했습니다.")


class ExcuseNoticeView(discord.ui.View):
    """접수 알림 메시지의 영속 [검토하기] 버튼."""

    def __init__(self, flow: ExcuseFlow) -> None:
        super().__init__(timeout=None)
        self.flow = flow
        button: discord.ui.Button = discord.ui.Button(
            label="검토하기",
            style=discord.ButtonStyle.primary,
            emoji="🗂️",
            custom_id=EXCUSE_REVIEW_CUSTOM_ID,
        )
        button.callback = self._on_review
        self.add_item(button)

    async def _on_review(self, interaction: discord.Interaction) -> None:
        await self.flow.open_review(interaction)


class ExcuseReviewView(discord.ui.View):
    """대기 신청 선택 메뉴와 승인/거절 버튼. 비공개 메시지에서만 사용한다."""

    def __init__(self, flow: ExcuseFlow, rows: list[dict[str, Any]]) -> None:
        super().__init__(timeout=15 * 60)
        self.flow = flow
        self.rows = {int(row["id"]): row for row in rows}
        self.selected_id: int | None = None

        self.select: discord.ui.Select = discord.ui.Select(
            placeholder="검토할 사유 신청을 선택하세요",
            options=[
                discord.SelectOption(
                    label=f"#{row['id']} {row['target_date']} {excuse_type_label(row.get('excuse_type'))}",
                    description=str(row.get("display_name") or row["discord_id"])[:100],
                    value=str(row["id"]),
                )
                for row in rows
            ],
        )
        self.select.callback = self._on_select
        self.approve_button: discord.ui.Button = discord.ui.Button(
            label="승인", style=discord.ButtonStyle.success, emoji="✅", disabled=True
        )
        self.approve_button.callback = self._on_approve
        self.reject_button: discord.ui.Button = discord.ui.Button(
            label="거절", style=discord.ButtonStyle.danger, emoji="⛔", disabled=True
        )
        self.reject_button.callback = self._on_reject
        self.add_item(self.select)
        self.add_item(self.approve_button)
        self.add_item(self.reject_button)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.selected_id = int(self.select.values[0])
        row = await self.flow.excuse_service.get_request(excuse_request_id=self.selected_id)
        if row is None:
            row = self.rows[self.selected_id]
        for option in self.select.options:
            option.default = option.value == str(self.selected_id)
        self.approve_button.disabled = False
        self.reject_button.disabled = False
        await interaction.response.edit_message(embed=build_detail_embed(row), view=self)

    async def _on_approve(self, interaction: discord.Interaction) -> None:
        if self.selected_id is None:
            await interaction.response.send_message("먼저 신청을 선택하세요.", ephemeral=True)
            return
        await self.flow.approve(interaction, self.selected_id)

    async def _on_reject(self, interaction: discord.Interaction) -> None:
        if self.selected_id is None:
            await interaction.response.send_message("먼저 신청을 선택하세요.", ephemeral=True)
            return
        await interaction.response.send_modal(RejectReasonModal(self.flow, self.selected_id))

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        logger.exception("Excuse review view failed.", exc_info=error)
        await _reply_error(interaction, "사유 검토 처리 중 오류가 발생했습니다.")


class RejectReasonModal(discord.ui.Modal, title="사유 신청 거절"):
    """거절 사유를 입력받는 모달."""

    rejection_reason = discord.ui.TextInput(
        label="거절 사유",
        style=discord.TextStyle.paragraph,
        min_length=2,
        max_length=500,
    )

    def __init__(self, flow: ExcuseFlow, request_id: int) -> None:
        super().__init__()
        self.flow = flow
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.flow.reject(interaction, self.request_id, self.rejection_reason.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Reject reason modal failed.", exc_info=error)
        await _reply_error(interaction, "거절 처리 중 오류가 발생했습니다.")


async def _reply_error(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)
