"""Slash commands for excuse requests and approvals."""

from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.excuse_policy import EXCUSE_TYPE_LABELS
from bot.services.excuse_service import ExcuseResult, ExcuseService, ExcuseStatus
from bot.services.guild_service import GuildService
from bot.utils.permissions import has_officer_permission


logger = logging.getLogger(__name__)


class ExcusesCog(commands.Cog):
    """Provide user and officer excuse request commands."""

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
        description="결석/지각/조퇴 사유를 신청합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(target_date="날짜", excuse_type="유형", reason="사유")
    @app_commands.describe(
        target_date="YYYY-MM-DD 형식의 대상 출석일",
        excuse_type="결석, 지각, 조퇴 중 하나",
        reason="2자 이상 500자 이하의 사유",
    )
    @app_commands.choices(
        excuse_type=[
            app_commands.Choice(name="결석", value="ABSENCE"),
            app_commands.Choice(name="지각", value="LATE"),
            app_commands.Choice(name="조퇴", value="EARLY_LEAVE"),
        ]
    )
    async def create_excuse(
        self,
        interaction: discord.Interaction,
        target_date: str,
        excuse_type: app_commands.Choice[str],
        reason: str,
    ) -> None:
        """Handle `/사유신청`."""

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
                expected_time=None,
                reason=reason,
                now=datetime.now(timezone.utc),
                excuse_type=excuse_type.value,
            )
        except Exception:
            logger.exception(
                "Excuse request failed. guild_id=%s user_id=%s",
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
        description="대기 중인 내 사유 신청을 취소합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(excuse_request_id="신청id")
    async def cancel_excuse(
        self,
        interaction: discord.Interaction,
        excuse_request_id: int,
    ) -> None:
        """Handle `/사유취소`."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        result = await self.excuse_service.cancel_request(
            guild_id=guild.id,
            discord_id=interaction.user.id,
            excuse_request_id=excuse_request_id,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._message_for_status(result),
            ephemeral=True,
        )

    @app_commands.command(name="사유목록", description="사유 신청 목록을 조회합니다.")
    @app_commands.guild_only()
    @app_commands.rename(include_all="전체조회", status="상태")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="대기", value="PENDING"),
            app_commands.Choice(name="승인", value="APPROVED"),
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
        """Handle `/사유목록`."""

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        result = await self.excuse_service.list_requests(
            guild_id=guild.id,
            discord_id=interaction.user.id,
            status=None if status is None else status.value,
            include_all=include_all,
            can_view_all=await self._has_officer_permission(interaction),
        )
        await interaction.response.send_message(
            self._build_list_message(result),
            ephemeral=True,
        )

    @app_commands.command(name="사유승인", description="대기 중인 사유 신청을 승인합니다.")
    @app_commands.guild_only()
    @app_commands.rename(excuse_request_id="신청id")
    async def approve_excuse(
        self,
        interaction: discord.Interaction,
        excuse_request_id: int,
    ) -> None:
        """Handle `/사유승인`."""

        if not await self._respond_if_not_officer(interaction):
            return
        assert interaction.guild is not None
        result = await self.excuse_service.approve_request(
            guild_id=interaction.guild.id,
            excuse_request_id=excuse_request_id,
            actor_discord_id=interaction.user.id,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._message_for_status(result),
            ephemeral=True,
        )

    @app_commands.command(name="사유거절", description="대기 중인 사유 신청을 거절합니다.")
    @app_commands.guild_only()
    @app_commands.rename(excuse_request_id="신청id", rejection_reason="거절사유")
    async def reject_excuse(
        self,
        interaction: discord.Interaction,
        excuse_request_id: int,
        rejection_reason: str,
    ) -> None:
        """Handle `/사유거절`."""

        if not await self._respond_if_not_officer(interaction):
            return
        assert interaction.guild is not None
        result = await self.excuse_service.reject_request(
            guild_id=interaction.guild.id,
            excuse_request_id=excuse_request_id,
            actor_discord_id=interaction.user.id,
            rejection_reason=rejection_reason,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._message_for_status(result),
            ephemeral=True,
        )

    @app_commands.command(name="사유상세", description="사유 신청 상세를 조회합니다.")
    @app_commands.guild_only()
    @app_commands.rename(excuse_request_id="신청id")
    async def excuse_detail(
        self,
        interaction: discord.Interaction,
        excuse_request_id: int,
    ) -> None:
        """Handle `/사유상세`."""

        if not await self._respond_if_not_officer(interaction):
            return
        row = await self.excuse_service.excuse_repository.get_by_id(
            excuse_request_id=excuse_request_id
        )
        if row is None:
            await interaction.response.send_message(
                "사유 신청을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            self._format_detail(row),
            ephemeral=True,
        )

    @app_commands.command(
        name="사유예외등록",
        description="관리자가 마감 이후 긴급 예외 사유를 등록합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(
        member="사용자",
        target_date="날짜",
        excuse_type="유형",
        reason="사유",
        admin_note="관리자메모",
    )
    @app_commands.choices(
        excuse_type=[
            app_commands.Choice(name="결석", value="ABSENCE"),
            app_commands.Choice(name="지각", value="LATE"),
            app_commands.Choice(name="조퇴", value="EARLY_LEAVE"),
        ]
    )
    async def create_override(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        target_date: str,
        excuse_type: app_commands.Choice[str],
        reason: str,
        admin_note: str,
    ) -> None:
        """Handle `/사유예외등록`."""

        if not await self._respond_if_not_officer(interaction):
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
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._message_for_status(result),
            ephemeral=True,
        )

    @app_commands.command(
        name="사유정책설정",
        description="사유 신청 마감 시간을 변경합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(deadline_time="마감시간", deadline_days_before="마감일수")
    async def update_policy(
        self,
        interaction: discord.Interaction,
        deadline_time: str,
        deadline_days_before: int,
    ) -> None:
        """Handle `/사유정책설정`."""

        if not await self._respond_if_not_officer(interaction):
            return
        assert interaction.guild is not None
        result = await self.excuse_service.update_policy(
            guild_id=interaction.guild.id,
            actor_discord_id=interaction.user.id,
            deadline_time=deadline_time,
            deadline_days_before=deadline_days_before,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            self._message_for_status(result),
            ephemeral=True,
        )

    @app_commands.command(name="사유정책조회", description="현재 사유 신청 정책을 조회합니다.")
    @app_commands.guild_only()
    async def view_policy(self, interaction: discord.Interaction) -> None:
        """Handle `/사유정책조회`."""

        if interaction.guild is None:
            return
        settings = await self.guild_service.get_settings(interaction.guild.id)
        if settings is None:
            await interaction.response.send_message(
                "아직 초기설정이 완료되지 않았습니다.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            self._build_policy_message(settings),
            ephemeral=True,
        )

    @app_commands.command(name="사유정책공지", description="출석 사유 신청 정책을 공지합니다.")
    @app_commands.guild_only()
    async def announce_policy(self, interaction: discord.Interaction) -> None:
        """Handle `/사유정책공지`."""

        if not await self._respond_if_not_officer(interaction):
            return
        assert interaction.guild is not None
        settings = await self.guild_service.get_settings(interaction.guild.id)
        if settings is None:
            await interaction.response.send_message(
                "아직 초기설정이 완료되지 않았습니다.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(self._build_policy_notice(settings))

    async def _has_officer_permission(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            return False
        settings = await self.guild_service.get_settings(guild.id)
        if settings is None:
            return False
        return has_officer_permission(interaction, settings["officer_role_id"])

    async def _respond_if_not_officer(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return False
        settings = await self.guild_service.get_settings(interaction.guild.id)
        if settings is None:
            await interaction.response.send_message(
                "아직 초기설정이 완료되지 않았습니다.",
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

        request = result.request
        label = EXCUSE_TYPE_LABELS.get(request.get("excuse_type"), request.get("excuse_type"))
        if result.status is ExcuseStatus.CREATED_PENDING:
            return (
                "사유 신청이 완료되었습니다.\n\n"
                f"대상 날짜: {request['target_date']}\n"
                f"신청 유형: {label}\n"
                f"신청 시각: {request['requested_at']}\n"
                f"신청 마감: {request.get('deadline_at')}\n"
                "처리 상태: 관리자 승인 대기\n\n"
                "신청은 관리자의 승인을 받아야 효력이 발생합니다."
            )
        return self._message_for_status(result)

    def _build_list_message(self, result: ExcuseResult) -> str:
        if result.status is ExcuseStatus.NOT_OWNER:
            return self._message_for_status(result)
        rows = [] if result.request is None else result.request.get("rows", [])
        if not rows:
            return "조회할 사유 신청이 없습니다."
        lines = ["사유 신청 목록"]
        for row in rows[:20]:
            label = EXCUSE_TYPE_LABELS.get(row.get("excuse_type"), row.get("excuse_type"))
            lines.append(
                f"#{row['id']} / {row['target_date']} / {label} / "
                f"{self._status_label(row['status'])} / <@{row['discord_id']}>"
            )
        return "\n".join(lines)

    def _format_detail(self, row: dict) -> str:
        label = EXCUSE_TYPE_LABELS.get(row.get("excuse_type"), row.get("excuse_type"))
        return (
            f"사유 신청 상세 #{row['id']}\n"
            f"대상 날짜: {row['target_date']}\n"
            f"유형: {label}\n"
            f"상태: {self._status_label(row['status'])}\n"
            f"신청 시각: {row['requested_at']}\n"
            f"마감 시각: {row.get('deadline_at')}\n"
            f"관리자 예외: {'예' if row.get('is_admin_override') else '아니오'}\n"
            f"사유: {row['reason']}"
        )

    def _message_for_status(self, result: ExcuseResult) -> str:
        if result.status is ExcuseStatus.TOO_LATE_TO_REQUEST:
            deadline = result.deadline_at.isoformat() if result.deadline_at else "알 수 없음"
            return (
                "사유 신청 기간이 마감되었습니다.\n\n"
                f"신청 마감: {deadline}\n\n"
                "마감 이후에는 일반 사유 신청을 제출할 수 없습니다.\n"
                "긴급한 사정이 있다면 관리자에게 문의해주세요."
            )
        messages = {
            ExcuseStatus.DUPLICATE_ACTIVE_REQUEST: "해당 날짜에 이미 등록된 사유 신청이 있습니다.",
            ExcuseStatus.INVALID_DATE: "날짜는 YYYY-MM-DD 형식으로 입력해주세요.",
            ExcuseStatus.PAST_DATE: "지난 날짜의 사유는 신청할 수 없습니다.",
            ExcuseStatus.NOT_ATTENDANCE_DAY: "선택한 날짜에는 등록된 출석 일정이 없습니다.",
            ExcuseStatus.INVALID_TIME: "시간은 HH:MM 형식으로 입력해주세요.",
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
            ExcuseStatus.ADMIN_OVERRIDE_CREATED: "관리자 예외 사유가 승인 상태로 등록되었습니다.",
            ExcuseStatus.POLICY_UPDATED: "사유 신청 정책을 변경했습니다.",
        }
        return messages.get(result.status, "요청을 처리했습니다.")

    def _status_label(self, status: str) -> str:
        labels = {
            "PENDING": "대기",
            "APPROVED": "승인",
            "AUTO_APPROVED": "자동승인",
            "REJECTED": "거절",
            "CANCELLED": "취소",
            "CANCELED": "취소",
        }
        return labels.get(status, status)

    def _build_policy_message(self, settings: dict) -> str:
        days = int(settings.get("excuse_deadline_days_before") or 1)
        time_text = settings.get("excuse_deadline_time") or "23:00"
        return (
            "현재 사유 신청 정책\n\n"
            f"기준 시간대: {settings['timezone']}\n"
            f"신청 마감: 출석일 {days}일 전 {time_text}\n"
            "관리자 승인: 필수\n"
            "마감 이후 일반 신청: 불가능\n"
            "긴급 예외 등록: 관리자만 가능"
        )

    def _build_policy_notice(self, settings: dict) -> str:
        days = int(settings.get("excuse_deadline_days_before") or 1)
        time_text = settings.get("excuse_deadline_time") or "23:00"
        return (
            "[출석 사유 신청 안내]\n\n"
            f"결석, 지각 또는 조퇴가 예상되는 경우 대상 출석일 {days}일 전 "
            f"{time_text}까지 사유를 신청해주세요.\n\n"
            "사유 신청 명령어: /사유신청\n\n"
            "사유 신청은 관리자 승인 후 효력이 발생합니다.\n"
            "마감 이후에는 일반 신청이 불가능합니다.\n\n"
            "사고, 응급 질병 등 긴급한 사정은 관리자에게 별도로 문의해주세요.\n\n"
            "봇이 실행 중인 시간에만 사유 신청이 가능하므로 마감 전에 미리 신청해주세요."
        )
