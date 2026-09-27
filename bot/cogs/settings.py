"""서버 초기 설정과 근태 설정 변경 슬래시 명령어(`/설정 ...`)를 제공한다."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.common import (
    NOT_CONFIGURED_MESSAGE,
    has_officer_access,
    require_officer,
    require_server_admin,
)
from bot.runtime.time_provider import TimeProvider
from bot.services.admin_service import (
    AdminService,
    SettingsUpdateResult,
    SettingsUpdateStatus,
)
from bot.services.guild_service import GuildService
from bot.ui.embed_factory import EMBEDS

logger = logging.getLogger(__name__)

SETTING_FIELD_CHOICES = [
    app_commands.Choice(name=field, value=field)
    for field in (
        "timezone",
        "attendance_days",
        "attendance_start",
        "late_deadline",
        "close_deadline",
        "excuse_mode",
        "officer_role_id",
        "attendance_channel_id",
        "announcement_channel_id",
        "voice_verification_enabled",
        "voice_channel_ids",
        "voice_category_ids",
    )
]


class SettingsCog(commands.Cog):
    """서버 초기 설정, 설정 조회/변경, 출석 시간 변경 명령어를 제공한다."""

    settings = app_commands.Group(
        name="설정",
        description="근태관리봇 서버 설정을 관리합니다.",
        guild_only=True,
    )

    def __init__(
        self,
        *,
        admin_service: AdminService,
        guild_service: GuildService,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Cog가 사용할 관리자 서비스와 서버 설정 서비스를 저장한다."""

        self.admin_service = admin_service
        self.guild_service = guild_service
        self.time_provider = time_provider or TimeProvider()

    @settings.command(name="초기화", description="근태관리봇을 현재 서버에 처음 설정합니다.")
    @app_commands.rename(
        officer_role="간부역할",
        attendance_channel="출석채널",
        announcement_channel="공지채널",
    )
    @app_commands.describe(
        officer_role="간부 명령어를 사용할 Discord 역할",
        attendance_channel="출석 명령어를 사용할 텍스트 채널",
        announcement_channel="출석 시작과 마감 공지를 보낼 채널",
    )
    async def initial_setup(
        self,
        interaction: discord.Interaction,
        officer_role: discord.Role,
        attendance_channel: discord.TextChannel,
        announcement_channel: discord.TextChannel,
    ) -> None:
        """현재 Discord 서버의 기본 근태 설정을 생성한다."""

        guild = await require_server_admin(interaction)
        if guild is None:
            return

        if officer_role.is_default():
            await interaction.response.send_message(
                "⚠️ @everyone 역할은 간부 역할로 사용할 수 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await self.guild_service.initialize_guild(
                guild_id=guild.id,
                officer_role_id=officer_role.id,
                attendance_channel_id=attendance_channel.id,
                announcement_channel_id=announcement_channel.id,
            )
        except Exception:
            logger.exception("서버 초기설정 중 오류가 발생했습니다. guild_id=%s", guild.id)
            await interaction.followup.send(
                "❌ 초기설정 중 DB 오류가 발생했습니다. 서버 로그를 확인해주세요.",
                ephemeral=True,
            )
            return

        if not result.created:
            await interaction.followup.send(
                "ℹ️ 이미 초기설정이 완료된 서버입니다.",
                ephemeral=True,
            )
            return

        embed = EMBEDS.success(
            "🎉 근태관리봇 초기설정 완료",
            "✅ 현재 서버의 기본 근태 설정을 저장했습니다.",
            fields=(
                ("🧑‍💼 간부 역할", officer_role.mention, False),
                ("📋 출석 채널", attendance_channel.mention, True),
                ("📢 공지 채널", announcement_channel.mention, True),
                ("🗓️ 출석 요일", result.attendance_days, False),
                (
                    "🕒 출석 시간",
                    f"✅ 정상: {result.attendance_start} ~ {result.late_deadline}\n"
                    f"⏰ 지각: {result.late_deadline} ~ {result.close_deadline}\n"
                    f"🔒 마감: {result.close_deadline}",
                    False,
                ),
                ("📝 사유 승인 방식", result.excuse_mode, False),
            ),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @settings.command(name="조회", description="현재 근태관리 설정을 조회합니다.")
    async def show_settings(self, interaction: discord.Interaction) -> None:
        """/설정 조회 명령을 처리한다."""

        settings = await require_officer(interaction, self.guild_service)
        if settings is None:
            return
        await interaction.response.send_message(
            (
                "현재 설정\n"
                f"timezone: {settings['timezone']}\n"
                f"attendance_days: {settings['attendance_days']}\n"
                f"attendance_start: {settings['attendance_start']}\n"
                f"late_deadline: {settings['late_deadline']}\n"
                f"close_deadline: {settings['close_deadline']}\n"
                f"excuse_mode: {settings['excuse_mode']}\n"
                f"officer_role_id: {settings['officer_role_id']}\n"
                f"attendance_channel_id: {settings['attendance_channel_id']}\n"
                f"announcement_channel_id: {settings['announcement_channel_id']}"
            ),
            ephemeral=True,
        )

    @settings.command(name="변경", description="근태관리 설정 한 항목을 변경합니다.")
    @app_commands.rename(field="항목", value="값")
    @app_commands.choices(field=SETTING_FIELD_CHOICES)
    async def update_setting(
        self,
        interaction: discord.Interaction,
        field: app_commands.Choice[str],
        value: str,
    ) -> None:
        """/설정 변경 명령을 처리한다."""

        permission = await has_officer_access(interaction, self.guild_service)
        guild = interaction.guild
        assert guild is not None
        try:
            result = await self.admin_service.update_setting(
                guild_id=guild.id,
                field=field.value,
                value=value,
                actor_discord_id=interaction.user.id,
                has_permission=permission,
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception("Setting update failed: guild_id=%s", guild.id)
            await interaction.response.send_message(
                "설정 변경 중 오류가 발생했습니다.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            self._settings_update_message(result),
            ephemeral=True,
        )

    @settings.command(name="출석시간", description="출석 시작, 지각 기준, 마감 시간을 한 번에 변경합니다.")
    @app_commands.rename(
        attendance_start="출석시작",
        late_deadline="지각기준",
        close_deadline="마감시간",
    )
    @app_commands.describe(
        attendance_start="HH:MM 형식, 예: 21:30",
        late_deadline="HH:MM 형식, 예: 21:40",
        close_deadline="HH:MM 형식, 예: 21:45",
    )
    async def update_attendance_time(
        self,
        interaction: discord.Interaction,
        attendance_start: str,
        late_deadline: str,
        close_deadline: str,
    ) -> None:
        """/설정 출석시간 명령을 처리한다."""

        guild = await require_server_admin(interaction)
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await self.guild_service.update_attendance_times(
                guild_id=guild.id,
                attendance_start=attendance_start,
                late_deadline=late_deadline,
                close_deadline=close_deadline,
                now=self.time_provider.now_utc(),
            )
        except Exception:
            logger.exception("출석 시간 변경 중 오류가 발생했습니다. guild_id=%s", guild.id)
            await interaction.followup.send(
                "❌ 출석 시간 변경 중 오류가 발생했습니다. 서버 로그를 확인해주세요.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            self._build_time_update_message(result),
            ephemeral=True,
        )

    @settings.command(name="음성검증", description="출석 후 음성 채널 체류 검증을 켜거나 끄고 대상 채널을 지정합니다.")
    @app_commands.rename(enabled="사용", voice_channel="채널", category="카테고리")
    @app_commands.describe(
        enabled="True면 체크인 후 음성 채널 체류 시간을 검증합니다.",
        voice_channel="검증 대상 음성 채널 (지정하면 기존 채널 목록을 대체)",
        category="검증 대상 카테고리 (안의 모든 음성 채널 인정, 지정하면 기존 목록을 대체)",
    )
    async def configure_voice_verification(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        voice_channel: discord.VoiceChannel | None = None,
        category: discord.CategoryChannel | None = None,
    ) -> None:
        """/설정 음성검증 명령을 처리한다."""

        settings = await require_officer(interaction, self.guild_service)
        if settings is None:
            return
        guild = interaction.guild
        assert guild is not None

        updates: list[tuple[str, str]] = [("voice_verification_enabled", "1" if enabled else "0")]
        if voice_channel is not None:
            updates.append(("voice_channel_ids", str(voice_channel.id)))
        if category is not None:
            updates.append(("voice_category_ids", str(category.id)))

        has_targets = bool(
            voice_channel or category or settings.get("voice_channel_ids") or settings.get("voice_category_ids")
        )
        if enabled and not has_targets:
            await interaction.response.send_message(
                "⚠️ 검증 대상 음성 채널 또는 카테고리를 함께 지정해주세요.",
                ephemeral=True,
            )
            return

        for db_field, value in updates:
            result = await self.admin_service.update_setting(
                guild_id=guild.id,
                field=db_field,
                value=value,
                actor_discord_id=interaction.user.id,
                has_permission=True,
                now=self.time_provider.now_utc(),
            )
            if result.status is not SettingsUpdateStatus.UPDATED:
                await interaction.response.send_message(
                    self._settings_update_message(result),
                    ephemeral=True,
                )
                return

        updated = await self.guild_service.get_settings(guild.id) or settings
        targets = [f"<#{cid}>" for cid in (updated.get("voice_channel_ids") or "").split(",") if cid] + [
            f"카테고리 <#{cid}>" for cid in (updated.get("voice_category_ids") or "").split(",") if cid
        ]
        await interaction.response.send_message(
            embed=EMBEDS.success(
                "음성 검증 설정 저장",
                "체크인 후 정해진 시간 이상 음성 채널에 머물러야 검증이 완료됩니다. "
                "미참여/부족 시 감점이 별도 이벤트로 기록됩니다.",
                fields=(
                    ("사용 여부", "켜짐" if enabled else "꺼짐", True),
                    ("대상", ", ".join(targets) or "없음", False),
                    ("확인", "`/출석 검증현황`으로 오늘 진행 상황을 볼 수 있습니다.", False),
                ),
            ),
            ephemeral=True,
        )

    def _settings_update_message(self, result: SettingsUpdateResult) -> str:
        """설정 변경 결과를 사용자 응답 문자열로 변환한다."""

        if result.status is SettingsUpdateStatus.UPDATED:
            return (
                "설정을 변경했습니다.\n"
                f"항목: {result.field}\n"
                f"변경 전: {result.before_value}\n"
                f"변경 후: {result.after_value}"
            )
        messages = {
            SettingsUpdateStatus.PERMISSION_DENIED: "관리 권한이 필요합니다.",
            SettingsUpdateStatus.NOT_CONFIGURED: NOT_CONFIGURED_MESSAGE,
            SettingsUpdateStatus.INVALID_FIELD: "변경할 수 없는 설정 항목입니다.",
            SettingsUpdateStatus.INVALID_VALUE: "설정 값이 올바르지 않습니다.",
            SettingsUpdateStatus.INVALID_TIME_ORDER: "출석 시간은 시작 < 지각 < 마감 순서여야 합니다.",
        }
        return messages[result.status]

    def _build_time_update_message(self, result) -> str:
        """출석 시간 변경 응답 메시지를 만든다."""

        if result.status == "NOT_CONFIGURED":
            return NOT_CONFIGURED_MESSAGE

        if result.status == "INVALID_TIME":
            return "⚠️ 시간은 HH:MM 형식으로 입력해주세요. 예: 21:30"

        if result.status == "INVALID_ORDER":
            return "⚠️ 시간은 출석시작 < 지각기준 < 마감시간 순서여야 합니다."

        session_messages = {
            "UPDATED": "🔄 오늘 생성된 출석 세션도 함께 갱신했습니다.",
            "NO_SESSION": "ℹ️ 오늘 생성된 출석 세션은 아직 없어 다음 생성부터 적용됩니다.",
            "HAS_RECORDS": "ℹ️ 오늘 세션에는 이미 출석 기록이 있어 기존 세션 시간은 변경하지 않았습니다.",
            "SESSION_LOCKED": "ℹ️ 오늘 세션은 이미 마감/취소되어 기존 세션 시간은 변경하지 않았습니다.",
        }
        session_message = session_messages.get(
            result.today_session_status,
            "오늘 세션 상태는 변경하지 않았습니다.",
        )

        return (
            "✅ 출석 시간이 변경되었습니다.\n"
            f"✅ 정상 출석: {result.attendance_start} ~ {result.late_deadline}\n"
            f"⏰ 지각: {result.late_deadline} ~ {result.close_deadline}\n"
            f"🔒 마감: {result.close_deadline}\n"
            f"{session_message}"
        )
