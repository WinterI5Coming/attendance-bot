"""Stage C 간부 인사 검토 슬래시 명령어를 제공한다."""

from datetime import datetime, timezone
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.guild_service import GuildService
from bot.services.stage_c_service import OfficerReviewService
from bot.utils.permissions import has_officer_permission, is_server_admin


logger = logging.getLogger(__name__)


class OfficerReviewsCog(commands.Cog):
    """간부 인사 미리보기, 실행, 이력 조회 명령어를 제공한다."""

    def __init__(
        self,
        *,
        guild_service: GuildService,
        officer_review_service: OfficerReviewService,
    ) -> None:
        """Cog가 사용할 서버 설정 서비스와 간부 인사 서비스를 저장한다."""

        self.guild_service = guild_service
        self.officer_review_service = officer_review_service

    @app_commands.command(name="간부평가기준", description="간부 평가 설정을 조회합니다.")
    @app_commands.guild_only()
    async def show_settings(self, interaction: discord.Interaction) -> None:
        """현재 서버의 간부 평가 기준과 자동화 설정을 조회한다."""

        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        settings = await self.officer_review_service.get_settings(
            guild_id=interaction.guild_id,
            now=datetime.now(timezone.utc),
        )
        lines = [
            "간부 평가 설정",
            f"- enabled: {bool(settings['enabled'])}",
            f"- minimum_sessions: {settings['minimum_sessions']}",
            f"- promotion_threshold: {settings['promotion_threshold']}",
            f"- retention_threshold: {settings['retention_threshold']}",
            f"- officer_capacity: {settings['officer_capacity']}",
            f"- auto_review_enabled: {bool(settings['auto_review_enabled'])}",
            f"- auto_apply_roles_enabled: {bool(settings['auto_apply_roles_enabled'])}",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="간부평가기준설정", description="간부 평가 기준을 설정합니다.")
    @app_commands.guild_only()
    async def update_settings(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        officer_capacity: int,
        minimum_sessions: int,
        promotion_threshold: float,
        retention_threshold: float,
        officer_role: discord.Role | None = None,
        member_role: discord.Role | None = None,
    ) -> None:
        """서버 관리자가 간부 평가 기준과 역할 ID를 변경한다."""

        if not is_server_admin(interaction):
            await interaction.response.send_message("서버 관리자 권한이 필요합니다.", ephemeral=True)
            return
        values = {
            "enabled": 1 if enabled else 0,
            "officer_capacity": officer_capacity,
            "minimum_sessions": minimum_sessions,
            "promotion_threshold": promotion_threshold,
            "retention_threshold": retention_threshold,
        }
        if officer_role is not None:
            values["officer_role_id"] = str(officer_role.id)
        if member_role is not None:
            values["member_role_id"] = str(member_role.id)
        await self.officer_review_service.update_settings(
            guild_id=interaction.guild_id,
            values=values,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message("간부 평가 기준을 저장했습니다.", ephemeral=True)

    @app_commands.command(name="간부인사미리보기", description="시즌 통계 기준 간부 인사안을 생성합니다.")
    @app_commands.guild_only()
    async def preview(
        self,
        interaction: discord.Interaction,
        season_id: int | None = None,
    ) -> None:
        """현재 시즌 통계를 기준으로 역할 변경 없는 간부 인사안을 생성한다."""

        if not await self._ensure_officer(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        settings = await self.guild_service.get_settings(interaction.guild_id)
        officer_settings = await self.officer_review_service.get_settings(
            guild_id=interaction.guild_id,
            now=datetime.now(timezone.utc),
        )
        officer_role = self._get_role(interaction.guild, officer_settings, "officer_role_id")
        if officer_role is None:
            officer_role = self._get_role(interaction.guild, settings, "officer_role_id")
        current_officers = set()
        if officer_role is not None:
            current_officers = {str(member.id) for member in officer_role.members}
        protected = self._protected_member_ids(interaction.guild)
        result = await self.officer_review_service.create_preview(
            guild_id=interaction.guild_id,
            season_id=season_id,
            current_officer_discord_ids=current_officers,
            protected_discord_ids=protected,
            created_by_discord_id=interaction.user.id,
            now=datetime.now(timezone.utc),
        )
        await interaction.followup.send(
            self._build_preview_message(result),
            ephemeral=True,
        )

    @app_commands.command(name="간부인사실행", description="저장된 간부 인사안을 실행합니다.")
    @app_commands.guild_only()
    async def execute(
        self,
        interaction: discord.Interaction,
        review_id: int,
    ) -> None:
        """저장된 간부 인사 미리보기를 실제 Discord 역할 변경으로 적용한다."""

        if not is_server_admin(interaction):
            await interaction.response.send_message("서버 관리자 권한이 필요합니다.", ephemeral=True)
            return
        if interaction.guild is None or interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        settings = await self.guild_service.get_settings(interaction.guild_id)
        if settings is None:
            await interaction.followup.send("초기 설정이 필요합니다.", ephemeral=True)
            return
        review = await self.officer_review_service.get_review(
            guild_id=interaction.guild_id,
            review_id=review_id,
        )
        if review is None or review["status"] != "PREVIEW":
            await interaction.followup.send("실행 가능한 미리보기를 찾지 못했습니다.", ephemeral=True)
            return
        officer_settings = await self.officer_review_service.get_settings(
            guild_id=interaction.guild_id,
            now=datetime.now(timezone.utc),
        )
        officer_role = self._get_role(interaction.guild, officer_settings, "officer_role_id")
        if officer_role is None:
            officer_role = self._get_role(interaction.guild, settings, "officer_role_id")
        member_role = self._get_role(interaction.guild, officer_settings, "member_role_id")
        if officer_role is None:
            await interaction.followup.send("간부 역할 설정을 찾지 못했습니다.", ephemeral=True)
            return
        planned = json.loads(review["result_json"])
        successes = 0
        failures = 0
        protected = self._protected_member_ids(interaction.guild)
        for item in planned:
            status = "SKIPPED"
            error_message = None
            discord_id = int(item["discord_id"])
            member = interaction.guild.get_member(discord_id)
            try:
                if str(discord_id) in protected:
                    reason = "Protected member."
                elif member is None:
                    reason = "Discord member not found."
                    status = "FAILED"
                elif item["action"] == "PROMOTE":
                    await member.add_roles(officer_role, reason=item["reason"])
                    if member_role is not None:
                        await member.remove_roles(member_role, reason=item["reason"])
                    reason = item["reason"]
                    status = "SUCCEEDED"
                elif item["action"] == "DEMOTE":
                    await member.remove_roles(officer_role, reason=item["reason"])
                    if member_role is not None:
                        await member.add_roles(member_role, reason=item["reason"])
                    reason = item["reason"]
                    status = "SUCCEEDED"
                else:
                    reason = item["reason"]
            except Exception as exc:
                logger.exception("Officer role change failed: review_id=%s", review_id)
                status = "FAILED"
                reason = item["reason"]
                error_message = str(exc)

            if status == "SUCCEEDED":
                successes += 1
            elif status == "FAILED":
                failures += 1

            await self.officer_review_service.log_role_change(
                guild_id=interaction.guild_id,
                review_id=review_id,
                member_id=item.get("member_id"),
                discord_id=discord_id,
                action_type=item["action"],
                from_role_id=officer_role.id if item["action"] == "DEMOTE" else member_role.id if member_role else None,
                to_role_id=officer_role.id if item["action"] == "PROMOTE" else member_role.id if member_role else None,
                status=status,
                reason=reason,
                error_message=error_message,
                now=datetime.now(timezone.utc),
            )

        final_status = "COMPLETED" if failures == 0 else "PARTIAL"
        await self.officer_review_service.mark_review_executed(
            guild_id=interaction.guild_id,
            review_id=review_id,
            status=final_status,
            executed_by_discord_id=interaction.user.id,
            now=datetime.now(timezone.utc),
        )
        await interaction.followup.send(
            f"간부 인사안을 실행했습니다. 성공 {successes}건, 실패 {failures}건",
            ephemeral=True,
        )

    @app_commands.command(name="계급변경이력", description="간부 역할 변경 이력을 조회합니다.")
    @app_commands.guild_only()
    async def role_change_logs(self, interaction: discord.Interaction) -> None:
        """최근 간부 역할 변경 성공/실패 이력을 조회한다."""

        if not await self._ensure_officer(interaction):
            return
        logs = await self.officer_review_service.list_role_change_logs(
            guild_id=interaction.guild_id,
            limit=10,
        )
        if not logs:
            await interaction.response.send_message("계급 변경 이력이 없습니다.", ephemeral=True)
            return
        lines = ["계급 변경 이력"]
        for row in logs:
            lines.append(
                f"- <@{row['discord_id']}> {row['action_type']} / {row['status']} / {row['reason']}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def _ensure_officer(self, interaction: discord.Interaction) -> bool:
        """명령 실행자가 간부 또는 서버 관리자인지 확인한다."""

        if interaction.guild is None or interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return False
        settings = await self.guild_service.get_settings(interaction.guild_id)
        officer_role_id = None if settings is None else settings["officer_role_id"]
        if not has_officer_permission(interaction, officer_role_id):
            await interaction.response.send_message("간부 권한이 필요합니다.", ephemeral=True)
            return False
        return True

    def _get_role(
        self,
        guild: discord.Guild | None,
        settings: dict | None,
        key: str,
    ) -> discord.Role | None:
        """설정 딕셔너리에 저장된 역할 ID로 Discord 역할 객체를 찾는다."""

        if guild is None or settings is None or not settings.get(key):
            return None
        try:
            role_id = int(settings[key])
        except (TypeError, ValueError):
            return None
        return guild.get_role(role_id)

    def _protected_member_ids(self, guild: discord.Guild | None) -> set[str]:
        """간부 인사에서 보호해야 하는 서버 소유자와 관리자 ID를 반환한다."""

        if guild is None:
            return set()
        protected = {str(guild.owner_id)}
        for member in guild.members:
            if member.guild_permissions.administrator:
                protected.add(str(member.id))
        return protected

    def _build_preview_message(self, result) -> str:
        """간부 인사 미리보기 결과를 사용자 응답 문자열로 변환한다."""

        if not result.configured:
            return "초기 설정이 필요합니다."
        if not result.enabled:
            return "간부 평가가 비활성화되어 있습니다."
        candidates = result.candidates or []
        if not candidates:
            review_text = "없음" if result.review_id is None else str(result.review_id)
            return f"변경 후보가 없습니다. review_id={review_text}"
        lines = [f"간부 인사 미리보기 review_id={result.review_id}"]
        for candidate in candidates[:10]:
            lines.append(
                f"- <@{candidate.discord_id}> {candidate.action} "
                f"/ {candidate.score:.1f} / {candidate.reason}"
            )
        return "\n".join(lines)
