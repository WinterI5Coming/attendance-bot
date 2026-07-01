"""Excuse request slash commands."""

from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.excuse_service import ExcuseResult, ExcuseService, ExcuseStatus
from bot.services.guild_service import GuildService
from bot.utils.permissions import has_officer_permission


logger = logging.getLogger(__name__)


class ExcusesCog(commands.Cog):
    """Slash commands for excuse request workflows."""

    def __init__(
        self,
        *,
        excuse_service: ExcuseService,
        guild_service: GuildService,
    ) -> None:
        self.excuse_service = excuse_service
        self.guild_service = guild_service

    @app_commands.command(
        name="사유신청",
        description="출석 시작 전 사유 지각/결석을 신청합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(
        target_date="날짜",
        reason="사유",
        expected_time="예상시간",
    )
    @app_commands.describe(
        target_date="YYYY-MM-DD 형식의 출석일",
        reason="2자 이상 500자 이하의 사유",
        expected_time="선택 사항, HH:MM 형식",
    )
    async def create_excuse(
        self,
        interaction: discord.Interaction,
        target_date: str,
        reason: str,
        expected_time: str | None = None,
    ) -> None:
        """Handle /사유신청."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.excuse_service.create_request(
                guild_id=guild.id,
                discord_id=interaction.user.id,
                target_date=target_date,
                expected_time=expected_time,
                reason=reason,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception(
                "사유 신청 중 오류가 발생했습니다. guild_id=%s discord_id=%s",
                guild.id,
                interaction.user.id,
            )
            await interaction.response.send_message(
                "사유 신청 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_create_message(result),
            ephemeral=True,
        )

    @app_commands.command(
        name="사유취소",
        description="아직 반영되지 않은 내 사유 신청을 취소합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(excuse_request_id="신청번호")
    async def cancel_excuse(
        self,
        interaction: discord.Interaction,
        excuse_request_id: int,
    ) -> None:
        """Handle /사유취소."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.excuse_service.cancel_request(
                guild_id=guild.id,
                discord_id=interaction.user.id,
                excuse_request_id=excuse_request_id,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("사유 취소 중 오류가 발생했습니다. guild_id=%s", guild.id)
            await interaction.response.send_message(
                "사유 취소 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._message_for_status(result),
            ephemeral=True,
        )

    @app_commands.command(
        name="사유목록",
        description="사유 신청 목록을 조회합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(include_all="전체조회", status="상태")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="대기", value="PENDING"),
            app_commands.Choice(name="승인", value="APPROVED"),
            app_commands.Choice(name="자동승인", value="AUTO_APPROVED"),
            app_commands.Choice(name="거절", value="REJECTED"),
            app_commands.Choice(name="취소", value="CANCELLED"),
        ]
    )
    async def list_excuses(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str] | None = None,
        include_all: bool = False,
    ) -> None:
        """Handle /사유목록."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        can_view_all = await self._has_officer_permission(interaction)
        try:
            result = await self.excuse_service.list_requests(
                guild_id=guild.id,
                discord_id=interaction.user.id,
                status=None if status is None else status.value,
                include_all=include_all,
                can_view_all=can_view_all,
            )
        except Exception:
            logger.exception("사유 목록 조회 중 오류가 발생했습니다. guild_id=%s", guild.id)
            await interaction.response.send_message(
                "사유 목록 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_list_message(result),
            ephemeral=True,
        )

    @app_commands.command(
        name="사유승인",
        description="대기 중인 사유 신청을 승인합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(excuse_request_id="신청번호")
    async def approve_excuse(
        self,
        interaction: discord.Interaction,
        excuse_request_id: int,
    ) -> None:
        """Handle /사유승인."""

        if not await self._respond_if_not_officer(interaction):
            return
        guild = interaction.guild
        assert guild is not None

        try:
            result = await self.excuse_service.approve_request(
                guild_id=guild.id,
                excuse_request_id=excuse_request_id,
                actor_discord_id=interaction.user.id,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("사유 승인 중 오류가 발생했습니다. guild_id=%s", guild.id)
            await interaction.response.send_message(
                "사유 승인 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._message_for_status(result),
            ephemeral=True,
        )

    @app_commands.command(
        name="사유거절",
        description="대기 중인 사유 신청을 거절합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(excuse_request_id="신청번호", rejection_reason="거절사유")
    async def reject_excuse(
        self,
        interaction: discord.Interaction,
        excuse_request_id: int,
        rejection_reason: str,
    ) -> None:
        """Handle /사유거절."""

        if not await self._respond_if_not_officer(interaction):
            return
        guild = interaction.guild
        assert guild is not None

        try:
            result = await self.excuse_service.reject_request(
                guild_id=guild.id,
                excuse_request_id=excuse_request_id,
                actor_discord_id=interaction.user.id,
                rejection_reason=rejection_reason,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("사유 거절 중 오류가 발생했습니다. guild_id=%s", guild.id)
            await interaction.response.send_message(
                "사유 거절 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._message_for_status(result),
            ephemeral=True,
        )

    async def _has_officer_permission(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            return False
        settings = await self.guild_service.get_settings(guild.id)
        if settings is None:
            return False
        return has_officer_permission(interaction, settings["officer_role_id"])

    async def _respond_if_not_officer(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return False
        settings = await self.guild_service.get_settings(guild.id)
        if settings is None:
            await interaction.response.send_message(
                "아직 초기설정이 완료되지 않았습니다. 먼저 /초기설정을 실행해주세요.",
                ephemeral=True,
            )
            return False
        if not has_officer_permission(interaction, settings["officer_role_id"]):
            await interaction.response.send_message(
                "간부 또는 서버 관리자만 사용할 수 있는 명령어입니다.",
                ephemeral=True,
            )
            return False
        return True

    def _build_create_message(self, result: ExcuseResult) -> str:
        if result.request is None:
            return self._message_for_status(result)

        request_id = result.request["id"]
        target_date = result.request["target_date"]
        if result.status is ExcuseStatus.CREATED_AUTO_APPROVED:
            return f"사유 신청이 자동 승인되었습니다.\n신청번호: {request_id}\n날짜: {target_date}"
        if result.status is ExcuseStatus.CREATED_PENDING:
            return f"사유 신청이 접수되었습니다.\n신청번호: {request_id}\n날짜: {target_date}"
        return self._message_for_status(result)

    def _build_list_message(self, result: ExcuseResult) -> str:
        if result.status is ExcuseStatus.NOT_OWNER:
            return self._message_for_status(result)

        rows = [] if result.request is None else result.request.get("rows", [])
        if not rows:
            return "조회할 사유 신청이 없습니다."

        lines = ["사유 신청 목록"]
        for row in rows[:20]:
            lines.append(
                f"#{row['id']} / {row['target_date']} / {self._status_label(row['status'])} "
                f"/ <@{row['discord_id']}>"
            )
        if len(rows) > 20:
            lines.append("일부 목록은 생략되었습니다.")
        return "\n".join(lines)

    def _message_for_status(self, result: ExcuseResult) -> str:
        messages = {
            ExcuseStatus.DUPLICATE_ACTIVE_REQUEST: "이미 해당 날짜에 활성 사유 신청이 있습니다.",
            ExcuseStatus.INVALID_DATE: "날짜는 YYYY-MM-DD 형식으로 입력해주세요.",
            ExcuseStatus.PAST_DATE: "지난 날짜의 사유는 신청할 수 없습니다.",
            ExcuseStatus.NOT_ATTENDANCE_DAY: "해당 날짜는 출석일이 아닙니다.",
            ExcuseStatus.TOO_LATE_TO_REQUEST: "출석 시작 이후에는 사유를 신청할 수 없습니다.",
            ExcuseStatus.INVALID_TIME: "예상시간은 HH:MM 형식으로 입력해주세요.",
            ExcuseStatus.INVALID_REASON: "사유는 2자 이상 500자 이하로 입력해주세요.",
            ExcuseStatus.NOT_REGISTERED: "출석 대원으로 등록되어 있지 않습니다.",
            ExcuseStatus.NOT_SESSION_MEMBER: "해당 날짜 출석 세션의 참여 대상이 아닙니다.",
            ExcuseStatus.NOT_FOUND: "사유 신청을 찾을 수 없습니다.",
            ExcuseStatus.NOT_OWNER: "본인 신청만 조회하거나 취소할 수 있습니다.",
            ExcuseStatus.INVALID_STATUS: "현재 상태에서는 처리할 수 없습니다.",
            ExcuseStatus.CANCELLED: "사유 신청을 취소했습니다.",
            ExcuseStatus.APPROVED: f"사유 신청을 승인했습니다. 점수 보정: {result.score_delta:+d}",
            ExcuseStatus.REJECTED: "사유 신청을 거절했습니다.",
            ExcuseStatus.ALREADY_DECIDED: "이미 처리된 사유 신청입니다.",
            ExcuseStatus.ALREADY_APPLIED: "이미 출석 기록에 반영되어 취소할 수 없습니다.",
            ExcuseStatus.NOT_CONFIGURED: "아직 초기설정이 완료되지 않았습니다.",
        }
        return messages.get(result.status, "요청을 처리했습니다.")

    def _status_label(self, status: str) -> str:
        labels = {
            "PENDING": "대기",
            "APPROVED": "승인",
            "AUTO_APPROVED": "자동승인",
            "REJECTED": "거절",
            "CANCELLED": "취소",
        }
        return labels.get(status, status)
