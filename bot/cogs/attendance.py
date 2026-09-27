"""출석 체크인, 현황 조회, 기록 수정, 오늘 세션 제어 슬래시 명령어(`/출석 ...`)를 제공한다."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.common import (
    NOT_CONFIGURED_MESSAGE,
    has_officer_access,
    require_guild,
    require_officer,
)
from bot.runtime.time_provider import TimeProvider
from bot.services.admin_service import (
    AdminService,
    SessionControlResult,
    SessionControlStatus,
)
from bot.services.attendance_service import (
    AttendanceCheckInResult,
    AttendanceCheckInStatus,
    AttendanceCorrectionResult,
    AttendanceCorrectionStatus,
    AttendanceService,
    AttendanceStatusMember,
    AttendanceStatusResult,
)
from bot.services.guild_service import GuildService
from bot.services.session_service import SessionPrepareStatus
from bot.ui.formatters import format_attendance_status, format_local_time

logger = logging.getLogger(__name__)


class AttendanceCog(commands.Cog):
    """사용자 출석과 오늘 출석 현황 명령어를 제공한다."""

    attendance = app_commands.Group(
        name="출석",
        description="출석 체크인, 현황 조회, 기록 수정, 오늘 세션 제어를 처리합니다.",
        guild_only=True,
    )

    def __init__(
        self,
        *,
        attendance_service: AttendanceService,
        guild_service: GuildService,
        admin_service: AdminService,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Cog 의존성을 초기화한다.

        Args:
            attendance_service: 출석 비즈니스 규칙을 담당하는 서비스.
            guild_service: 간부 권한 확인에 사용하는 서비스.
            admin_service: 오늘 세션 취소/재개를 담당하는 서비스.
            time_provider: 명령 처리 기준 시각을 공급하는 객체.
        """

        self.attendance_service = attendance_service
        self.guild_service = guild_service
        self.admin_service = admin_service
        self.time_provider = time_provider or TimeProvider()

    @attendance.command(name="체크인", description="오늘 출석 세션에 출석합니다.")
    async def check_in(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """/출석 체크인 명령을 처리한다."""

        guild = await require_guild(interaction)
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await self.attendance_service.check_in(
                guild_id=guild.id,
                discord_id=interaction.user.id,
                now=self.time_provider.now_utc(),
                current_voice_channel_id=self._current_voice_channel_id(
                    interaction.user
                ),
            )
        except Exception:
            logger.exception(
                "출석 처리 중 오류가 발생했습니다. guild_id=%s discord_id=%s",
                guild.id,
                interaction.user.id,
            )
            await interaction.followup.send(
                "❌ 출석 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            self._build_check_in_message(result),
            ephemeral=True,
        )

    @attendance.command(name="현황", description="오늘 출석 세션의 정상·지각·미체크 현황을 조회합니다.")
    async def show_status(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """/출석 현황 명령을 처리한다."""

        guild = await require_guild(interaction)
        if guild is None:
            return

        try:
            result = await self.attendance_service.get_today_status(
                guild_id=guild.id,
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception(
                "출석 현황 조회 중 오류가 발생했습니다. guild_id=%s",
                guild.id,
            )
            await interaction.response.send_message(
                "❌ 출석 현황 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_status_message(result),
            ephemeral=False,
        )

    @attendance.command(name="수정", description="간부가 특정 날짜의 출석 기록을 정정합니다.")
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
        """/출석 수정 명령을 처리한다."""

        if await require_officer(interaction, self.guild_service) is None:
            return
        guild = interaction.guild
        assert guild is not None

        try:
            result = await self.attendance_service.correct_attendance(
                guild_id=guild.id,
                target_discord_id=target_member.id,
                attendance_date=attendance_date,
                new_status=new_status.value,
                reason=reason,
                actor_discord_id=interaction.user.id,
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception(
                "출석 수정 중 오류가 발생했습니다. guild_id=%s actor_id=%s target_id=%s",
                guild.id,
                interaction.user.id,
                target_member.id,
            )
            await interaction.response.send_message(
                "❌ 출석 수정 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._build_correction_message(result, target_member.mention),
            ephemeral=True,
        )

    @attendance.command(name="오늘취소", description="오늘 출석 세션을 취소합니다.")
    @app_commands.rename(reason="사유")
    async def cancel_today(
        self,
        interaction: discord.Interaction,
        reason: str,
    ) -> None:
        """/출석 오늘취소 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        result = await self.admin_service.cancel_today_session(
            guild_id=guild.id,
            reason=reason,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(
            self._session_control_message(result),
            ephemeral=True,
        )

    @attendance.command(name="오늘재개", description="취소된 오늘 출석 세션을 재개합니다.")
    async def resume_today(self, interaction: discord.Interaction) -> None:
        """/출석 오늘재개 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        result = await self.admin_service.resume_today_session(
            guild_id=guild.id,
            actor_discord_id=interaction.user.id,
            has_permission=permission,
            now=self.time_provider.now_utc(),
        )
        await interaction.response.send_message(
            self._session_control_message(result),
            ephemeral=True,
        )

    def _session_control_message(self, result: SessionControlResult) -> str:
        """오늘 출석 세션 제어 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is SessionControlStatus.CANCELLED:
            return (
                "오늘 출석 세션을 취소했습니다.\n"
                f"세션 ID: {result.session_id}\n"
                f"점수 보정 이벤트: {result.score_event_count}개"
            )
        if result.status is SessionControlStatus.RESUMED:
            return (
                "오늘 출석 세션을 재개했습니다.\n"
                f"세션 ID: {result.session_id}\n"
                f"점수 복원 이벤트: {result.score_event_count}개"
            )
        messages = {
            SessionControlStatus.PERMISSION_DENIED: "관리 권한이 필요합니다.",
            SessionControlStatus.NOT_CONFIGURED: NOT_CONFIGURED_MESSAGE,
            SessionControlStatus.NO_SESSION: "오늘 출석 세션이 없습니다.",
            SessionControlStatus.INVALID_REASON: "사유는 2자 이상 500자 이하로 입력해 주세요.",
            SessionControlStatus.CLOSED: "이미 마감된 세션은 취소할 수 없습니다.",
            SessionControlStatus.ALREADY_CANCELLED: "이미 취소된 세션입니다.",
            SessionControlStatus.NOT_CANCELLED: "취소된 세션만 재개할 수 있습니다.",
            SessionControlStatus.CLOSE_ALREADY_PASSED: "마감 시간이 지난 세션은 재개할 수 없습니다.",
        }
        return messages.get(result.status, "요청을 처리할 수 없습니다.")

    def _build_check_in_message(
        self,
        result: AttendanceCheckInResult,
    ) -> str:
        """사용자에게 보여 줄 출석 응답 메시지를 만든다.

        Args:
            result: Service result for /출석.

        Returns:
            Discord message content.
        """

        if result.status is AttendanceCheckInStatus.PRESENT:
            checked_at = format_local_time(result.checked_at, result.timezone_name)
            return (
                "✅ 출석 완료: 정상 출석\n"
                f"{self._build_score_progress(result)}\n"
                f"🕒 처리 시각: {checked_at}"
            )

        if result.status is AttendanceCheckInStatus.LATE:
            checked_at = format_local_time(result.checked_at, result.timezone_name)
            return (
                "⏰ 출석 완료: 지각\n"
                f"{self._build_score_progress(result)}\n"
                f"🕒 처리 시각: {checked_at}"
            )

        if result.status is AttendanceCheckInStatus.EXCUSED_LATE:
            checked_at = format_local_time(result.checked_at, result.timezone_name)
            return (
                "📋 출석 완료: 사유 지각\n"
                f"{self._build_score_progress(result)}\n"
                f"🕒 처리 시각: {checked_at}"
            )

        if result.status is AttendanceCheckInStatus.ALREADY_CHECKED:
            checked_at = format_local_time(result.checked_at, result.timezone_name)
            return (
                "ℹ️ 이미 오늘 출석 처리가 완료되었습니다.\n"
                f"📌 상태: {format_attendance_status(result.attendance_status)}\n"
                f"🕒 처리 시각: {checked_at}\n"
                f"💯 현재 총점: {result.total_score}점"
            )

        if result.status is AttendanceCheckInStatus.NOT_OPEN:
            return (
                "⏳ 출석 시작 전입니다.\n"
                f"🕒 출석 시작: {format_local_time(result.start_at, result.timezone_name)}\n"
                f"⏰ 정상 출석 마감: {format_local_time(result.late_at, result.timezone_name)}\n"
                f"🔒 전체 마감: {format_local_time(result.close_at, result.timezone_name)}"
            )

        if result.status is AttendanceCheckInStatus.CLOSED:
            return (
                "🔒 오늘 출석은 이미 마감되었습니다.\n"
                f"🕒 마감 시각: {format_local_time(result.close_at, result.timezone_name)}"
            )

        if result.status is AttendanceCheckInStatus.NOT_REGISTERED:
            return (
                "🚫 출석 대원으로 등록되어 있지 않습니다.\n"
                "간부에게 대원 등록을 요청해주세요."
            )

        if result.status is AttendanceCheckInStatus.NOT_SESSION_MEMBER:
            return (
                "🚫 오늘 출석 세션의 참여 대상이 아닙니다.\n"
                "다음 출석일부터 참여할 수 있습니다."
            )

        if result.status is AttendanceCheckInStatus.NOT_ATTENDANCE_DAY:
            return "📅 오늘은 출석 일정이 없는 날입니다."

        if result.status is AttendanceCheckInStatus.NO_ACTIVE_MEMBERS:
            return "⚠️ 등록된 활성 대원이 없어 출석 세션을 만들 수 없습니다."

        if result.status is AttendanceCheckInStatus.CANCELLED:
            if result.cancel_reason:
                return (
                    "🚫 오늘 출석 일정은 취소되었습니다.\n"
                    f"사유: {result.cancel_reason}"
                )
            return "🚫 오늘 출석 일정은 취소되었습니다."

        return NOT_CONFIGURED_MESSAGE

    def _build_status_message(
        self,
        result: AttendanceStatusResult,
    ) -> str:
        """/출석현황 응답 메시지를 만든다.

        Args:
            result: Grouped attendance status result.

        Returns:
            Discord message content.
        """

        if result.status is SessionPrepareStatus.NOT_CONFIGURED:
            return NOT_CONFIGURED_MESSAGE

        if result.status is SessionPrepareStatus.NOT_ATTENDANCE_DAY:
            return "📅 오늘은 출석 일정이 없는 날입니다."

        if result.status is SessionPrepareStatus.NO_ACTIVE_MEMBERS:
            return "⚠️ 등록된 활성 대원이 없습니다."

        if result.status is SessionPrepareStatus.ALREADY_CLOSED and result.session is None:
            return "🔒 오늘 출석은 이미 마감되었고 생성된 출석 세션이 없습니다."

        if result.status is SessionPrepareStatus.CANCELLED:
            header = "🚫 오늘 출석 일정은 취소되었습니다."
            if result.cancel_reason:
                header = f"{header}\n사유: {result.cancel_reason}"
        else:
            header = "📊 오늘 출석 현황"

        sections = [header]
        self._append_member_section(sections, "✅ 정상 출석", result.present)
        self._append_member_section(sections, "⏰ 지각", result.late)
        self._append_member_section(sections, "❌ 결석", result.absent)
        self._append_member_section(sections, "📋 사유 지각", result.excused_late)
        self._append_member_section(sections, "🛡️ 사유 결석", result.excused_absent)
        self._append_member_section(sections, "❔ 미체크", result.unchecked)
        sections.append(
            "\n"
            f"👥 총원: {result.total_count}명\n"
            f"✅ 출석 완료: {result.checked_count}명\n"
            f"❔ 미체크: {len(result.unchecked)}명"
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
        """표시할 멤버가 있을 때 멤버 목록 섹션을 추가한다."""

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

    def _build_score_progress(self, result: AttendanceCheckInResult) -> str:
        """점수, 연속 출석 보너스, 계급 변경 안내 문구를 만든다."""

        lines = [
            f"🎯 이번 점수: {result.score_delta:+d}",
        ]
        if result.streak_bonus_delta:
            lines.append(f"🔥 연속 출석 보너스: {result.streak_bonus_delta:+d}")
        lines.append(f"💯 현재 총점: {result.total_score}점")
        if result.rank_changed:
            lines.append(f"🏅 계급 변경: {result.previous_rank} → {result.current_rank}")
        return "\n".join(lines)

    def _current_voice_channel_id(
        self,
        user: discord.abc.User,
    ) -> int | None:
        """사용자가 현재 접속한 음성 채널 ID를 반환한다."""

        if not isinstance(user, discord.Member):
            return None
        voice_state = user.voice
        if voice_state is None or voice_state.channel is None:
            return None
        return voice_state.channel.id

    def _build_correction_message(
        self,
        result: AttendanceCorrectionResult,
        target_mention: str,
    ) -> str:
        """사용자에게 보여 줄 출석 정정 응답 메시지를 만든다."""

        if result.status is AttendanceCorrectionStatus.UPDATED:
            return (
                "✏️ 출석 기록을 수정했습니다.\n\n"
                f"👤 대상: {target_mention}\n"
                f"🗓️ 날짜: {result.attendance_date}\n"
                f"📌 기존 상태: {format_attendance_status(result.previous_status)}\n"
                f"📌 변경 상태: {format_attendance_status(result.new_status)}\n"
                f"🎯 점수 보정: {result.score_delta:+d}\n"
                f"📝 정정 사유: {result.reason}"
            )

        if result.status is AttendanceCorrectionStatus.CREATED:
            return (
                "🆕 출석 기록을 생성했습니다.\n\n"
                f"👤 대상: {target_mention}\n"
                f"🗓️ 날짜: {result.attendance_date}\n"
                f"📌 상태: {format_attendance_status(result.new_status)}\n"
                f"🎯 점수 반영: {result.score_delta:+d}\n"
                f"📝 정정 사유: {result.reason}"
            )

        messages = {
            AttendanceCorrectionStatus.SAME_STATUS: "⚠️ 기존 출석 상태와 변경할 상태가 같습니다.",
            AttendanceCorrectionStatus.NOT_CONFIGURED: "⚙️ 아직 초기설정이 완료되지 않았습니다.",
            AttendanceCorrectionStatus.INVALID_DATE: "⚠️ 날짜는 YYYY-MM-DD 형식이어야 합니다.",
            AttendanceCorrectionStatus.FUTURE_DATE: "⚠️ 미래 날짜의 출석은 수정할 수 없습니다.",
            AttendanceCorrectionStatus.SESSION_NOT_FOUND: "⚠️ 해당 날짜의 출석 세션이 없습니다.",
            AttendanceCorrectionStatus.TARGET_NOT_FOUND: "⚠️ 대상 사용자가 대원으로 등록된 기록이 없습니다.",
            AttendanceCorrectionStatus.NOT_SESSION_MEMBER: "⚠️ 대상 사용자는 해당 날짜 출석 세션의 참여 대상이 아닙니다.",
            AttendanceCorrectionStatus.INVALID_REASON: "⚠️ 정정 사유는 2자 이상 500자 이하로 입력해주세요.",
        }
        return messages[result.status]
