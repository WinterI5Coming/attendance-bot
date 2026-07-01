"""Attendance check-in and status slash commands."""

from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.attendance_service import (
    AttendanceCorrectionResult,
    AttendanceCorrectionStatus,
    AttendanceCheckInResult,
    AttendanceCheckInStatus,
    AttendanceService,
    AttendanceStatusMember,
    AttendanceStatusResult,
)
from bot.services.guild_service import GuildService
from bot.services.session_service import SessionPrepareStatus
from bot.utils.time_utils import format_local_hhmm
from bot.utils.permissions import has_officer_permission


logger = logging.getLogger(__name__)


class AttendanceCog(commands.Cog):
    """Slash commands for user check-in and today's attendance status."""

    def __init__(
        self,
        attendance_service: AttendanceService,
        guild_service: GuildService | None = None,
    ) -> None:
        """Create the Cog.

        Args:
            attendance_service: Service that owns attendance business rules.
            guild_service: Optional service used for administrator permission
                checks in /출석수정.
        """

        self.attendance_service = attendance_service
        self.guild_service = guild_service

    @app_commands.command(
        name="출석",
        description="오늘 출석 세션에 출석합니다.",
    )
    @app_commands.guild_only()
    async def check_in(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Handle the /출석 command."""

        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.attendance_service.check_in(
                guild_id=guild.id,
                discord_id=interaction.user.id,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception(
                "출석 처리 중 오류가 발생했습니다. guild_id=%s discord_id=%s",
                guild.id,
                interaction.user.id,
            )
            await interaction.response.send_message(
                "출석 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_check_in_message(result),
            ephemeral=True,
        )

    @app_commands.command(
        name="출석현황",
        description="오늘 출석 세션의 정상·지각·미체크 현황을 조회합니다.",
    )
    @app_commands.guild_only()
    async def show_status(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Handle the /출석현황 command."""

        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.attendance_service.get_today_status(
                guild_id=guild.id,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception(
                "출석 현황 조회 중 오류가 발생했습니다. guild_id=%s",
                guild.id,
            )
            await interaction.response.send_message(
                "출석 현황 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_status_message(result),
            ephemeral=False,
        )

    @app_commands.command(
        name="출석수정",
        description="간부가 특정 날짜의 출석 기록을 정정합니다.",
    )
    @app_commands.guild_only()
    @app_commands.rename(
        target_member="사용자",
        attendance_date="날짜",
        new_status="상태",
        reason="사유",
    )
    @app_commands.describe(
        target_member="출석을 정정할 사용자",
        attendance_date="YYYY-MM-DD 형식의 서버 기준 날짜",
        new_status="PRESENT, LATE, ABSENT 중 하나",
        reason="정정 사유",
    )
    @app_commands.choices(
        new_status=[
            app_commands.Choice(name="정상 출석", value="PRESENT"),
            app_commands.Choice(name="지각", value="LATE"),
            app_commands.Choice(name="결석", value="ABSENT"),
        ]
    )
    async def correct_attendance(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
        attendance_date: str,
        new_status: app_commands.Choice[str],
        reason: str,
    ) -> None:
        """Handle the /출석수정 command."""

        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "이 명령어는 Discord 서버에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.guild_service is None:
            await interaction.response.send_message(
                "출석수정 기능이 아직 초기화되지 않았습니다.",
                ephemeral=True,
            )
            return

        settings = await self.guild_service.get_settings(guild.id)
        if settings is None:
            await interaction.response.send_message(
                "아직 초기설정이 완료되지 않았습니다. 먼저 /초기설정을 실행해주세요.",
                ephemeral=True,
            )
            return

        if not has_officer_permission(interaction, settings["officer_role_id"]):
            await interaction.response.send_message(
                "간부 또는 서버 관리자만 사용할 수 있는 명령어입니다.",
                ephemeral=True,
            )
            return

        try:
            result = await self.attendance_service.correct_attendance(
                guild_id=guild.id,
                target_discord_id=target_member.id,
                attendance_date=attendance_date,
                new_status=new_status.value,
                reason=reason,
                actor_discord_id=interaction.user.id,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception(
                "출석 수정 중 오류가 발생했습니다. guild_id=%s actor_id=%s target_id=%s",
                guild.id,
                interaction.user.id,
                target_member.id,
            )
            await interaction.response.send_message(
                "출석 수정 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_correction_message(result, target_member.mention),
            ephemeral=True,
        )

    def _build_check_in_message(
        self,
        result: AttendanceCheckInResult,
    ) -> str:
        """Build a user-facing check-in response.

        Args:
            result: Service result for /출석.

        Returns:
            Discord message content.
        """

        if result.status is AttendanceCheckInStatus.PRESENT:
            checked_at = self._format_time(result.checked_at, result.timezone_name)
            return (
                "출석 완료: 정상 출석\n"
                f"이번 점수: +{result.score_delta}\n"
                f"현재 총점: {result.total_score}점\n"
                f"처리 시각: {checked_at}"
            )

        if result.status is AttendanceCheckInStatus.LATE:
            checked_at = self._format_time(result.checked_at, result.timezone_name)
            return (
                "출석 완료: 지각\n"
                f"이번 점수: +{result.score_delta}\n"
                f"현재 총점: {result.total_score}점\n"
                f"처리 시각: {checked_at}"
            )

        if result.status is AttendanceCheckInStatus.ALREADY_CHECKED:
            checked_at = self._format_time(result.checked_at, result.timezone_name)
            return (
                "이미 오늘 출석 처리가 완료되었습니다.\n"
                f"상태: {self._attendance_status_label(result.attendance_status)}\n"
                f"처리 시각: {checked_at}\n"
                f"현재 총점: {result.total_score}점"
            )

        if result.status is AttendanceCheckInStatus.NOT_OPEN:
            return (
                "출석 시작 전입니다.\n"
                f"출석 시작: {self._format_time(result.start_at, result.timezone_name)}\n"
                f"정상 출석 마감: {self._format_time(result.late_at, result.timezone_name)}\n"
                f"전체 마감: {self._format_time(result.close_at, result.timezone_name)}"
            )

        if result.status is AttendanceCheckInStatus.CLOSED:
            return (
                "오늘 출석은 이미 마감되었습니다.\n"
                f"마감 시각: {self._format_time(result.close_at, result.timezone_name)}"
            )

        if result.status is AttendanceCheckInStatus.NOT_REGISTERED:
            return (
                "출석 대원으로 등록되어 있지 않습니다.\n"
                "간부에게 대원 등록을 요청해주세요."
            )

        if result.status is AttendanceCheckInStatus.NOT_SESSION_MEMBER:
            return (
                "오늘 출석 세션의 참여 대상이 아닙니다.\n"
                "다음 출석일부터 참여할 수 있습니다."
            )

        if result.status is AttendanceCheckInStatus.NOT_ATTENDANCE_DAY:
            return "오늘은 출석 일정이 없는 날입니다."

        if result.status is AttendanceCheckInStatus.NO_ACTIVE_MEMBERS:
            return "등록된 활성 대원이 없어 출석 세션을 만들 수 없습니다."

        if result.status is AttendanceCheckInStatus.CANCELLED:
            if result.cancel_reason:
                return (
                    "오늘 출석 일정은 취소되었습니다.\n"
                    f"사유: {result.cancel_reason}"
                )
            return "오늘 출석 일정은 취소되었습니다."

        return "아직 초기설정이 완료되지 않았습니다. 먼저 /초기설정을 실행해주세요."

    def _build_status_message(
        self,
        result: AttendanceStatusResult,
    ) -> str:
        """Build a user-facing /출석현황 response.

        Args:
            result: Grouped attendance status result.

        Returns:
            Discord message content.
        """

        if result.status is SessionPrepareStatus.NOT_CONFIGURED:
            return "아직 초기설정이 완료되지 않았습니다. 먼저 /초기설정을 실행해주세요."

        if result.status is SessionPrepareStatus.NOT_ATTENDANCE_DAY:
            return "오늘은 출석 일정이 없는 날입니다."

        if result.status is SessionPrepareStatus.NO_ACTIVE_MEMBERS:
            return "등록된 활성 대원이 없습니다."

        if result.status is SessionPrepareStatus.ALREADY_CLOSED and result.session is None:
            return "오늘 출석은 이미 마감되었고 생성된 출석 세션이 없습니다."

        if result.status is SessionPrepareStatus.CANCELLED:
            header = "오늘 출석 일정은 취소되었습니다."
            if result.cancel_reason:
                header = f"{header}\n사유: {result.cancel_reason}"
        else:
            header = "오늘 출석 현황"

        sections = [header]
        self._append_member_section(sections, "정상 출석", result.present)
        self._append_member_section(sections, "지각", result.late)
        self._append_member_section(sections, "결석", result.absent)
        self._append_member_section(sections, "사유 지각", result.excused_late)
        self._append_member_section(sections, "사유 결석", result.excused_absent)
        self._append_member_section(sections, "미체크", result.unchecked)
        sections.append(
            "\n"
            f"총원: {result.total_count}명\n"
            f"출석 완료: {result.checked_count}명\n"
            f"미체크: {len(result.unchecked)}명"
        )

        message = "\n\n".join(sections)
        if len(message) > 1900:
            return message[:1890] + "\n... 일부 목록이 생략되었습니다."
        return message

    def _append_member_section(
        self,
        sections: list[str],
        title: str,
        members: list[AttendanceStatusMember],
    ) -> None:
        """Append a member list section when it has content."""

        if not members:
            return

        lines = [
            title,
            *[
                f"- <@{member.discord_id}>"
                for member in members
            ],
        ]
        sections.append("\n".join(lines))

    def _format_time(
        self,
        value: str | None,
        timezone_name: str | None,
    ) -> str:
        """Format a UTC ISO 8601 timestamp as guild-local HH:MM."""

        if value is None or timezone_name is None:
            return "-"

        parsed = datetime.fromisoformat(value)
        formatted = format_local_hhmm(parsed, timezone_name)
        return "-" if formatted is None else formatted

    def _attendance_status_label(self, status: str | None) -> str:
        """Return a Korean label for a stored attendance status."""

        labels = {
            "PRESENT": "정상 출석",
            "LATE": "지각",
            "ABSENT": "결석",
            "EXCUSED_LATE": "사유 지각",
            "EXCUSED_ABSENT": "사유 결석",
        }
        return labels.get(status, status or "-")

    def _build_correction_message(
        self,
        result: AttendanceCorrectionResult,
        target_mention: str,
    ) -> str:
        """Build a user-facing correction response."""

        if result.status is AttendanceCorrectionStatus.UPDATED:
            return (
                "출석 기록을 수정했습니다.\n\n"
                f"대상: {target_mention}\n"
                f"날짜: {result.attendance_date}\n"
                f"기존 상태: {self._attendance_status_label(result.previous_status)}\n"
                f"변경 상태: {self._attendance_status_label(result.new_status)}\n"
                f"점수 보정: {result.score_delta:+d}\n"
                f"정정 사유: {result.reason}"
            )

        if result.status is AttendanceCorrectionStatus.CREATED:
            return (
                "출석 기록을 생성했습니다.\n\n"
                f"대상: {target_mention}\n"
                f"날짜: {result.attendance_date}\n"
                f"상태: {self._attendance_status_label(result.new_status)}\n"
                f"점수 반영: {result.score_delta:+d}\n"
                f"정정 사유: {result.reason}"
            )

        messages = {
            AttendanceCorrectionStatus.SAME_STATUS: "기존 출석 상태와 변경할 상태가 같습니다.",
            AttendanceCorrectionStatus.NOT_CONFIGURED: "아직 초기설정이 완료되지 않았습니다.",
            AttendanceCorrectionStatus.INVALID_DATE: "날짜는 YYYY-MM-DD 형식이어야 합니다.",
            AttendanceCorrectionStatus.FUTURE_DATE: "미래 날짜의 출석은 수정할 수 없습니다.",
            AttendanceCorrectionStatus.SESSION_NOT_FOUND: "해당 날짜의 출석 세션이 없습니다.",
            AttendanceCorrectionStatus.TARGET_NOT_FOUND: "대상 사용자가 대원으로 등록된 기록이 없습니다.",
            AttendanceCorrectionStatus.NOT_SESSION_MEMBER: "대상 사용자는 해당 날짜 출석 세션의 참여 대상이 아닙니다.",
            AttendanceCorrectionStatus.INVALID_REASON: "정정 사유는 2자 이상 500자 이하로 입력해주세요.",
        }
        return messages[result.status]
