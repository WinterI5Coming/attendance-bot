"""Stage C 시즌 운영 슬래시 명령어를 제공한다."""

from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.guild_service import GuildService
from bot.services.stage_c_service import SeasonService
from bot.utils.permissions import has_officer_permission


logger = logging.getLogger(__name__)


class SeasonsCog(commands.Cog):
    """시즌 관리와 시즌 랭킹 명령어를 제공한다."""

    def __init__(
        self,
        *,
        guild_service: GuildService,
        season_service: SeasonService,
    ) -> None:
        """Cog가 사용할 서버 설정 서비스와 시즌 서비스를 저장한다."""

        self.guild_service = guild_service
        self.season_service = season_service

    @app_commands.command(name="시즌생성", description="근태 시즌을 생성합니다.")
    @app_commands.guild_only()
    async def create_season(
        self,
        interaction: discord.Interaction,
        name: str,
        start_date: str,
        end_date: str,
    ) -> None:
        """새 시즌을 예약 상태로 생성한다."""

        if not await self._ensure_officer(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            season_id = await self.season_service.create_season(
                guild_id=interaction.guild_id,
                name=name,
                start_date=start_date,
                end_date=end_date,
                created_by_discord_id=interaction.user.id,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("Season create failed: guild_id=%s", interaction.guild_id)
            await interaction.followup.send("시즌 생성에 실패했습니다.", ephemeral=True)
            return
        await interaction.followup.send(
            f"시즌을 생성했습니다. id={season_id}",
            ephemeral=True,
        )

    @app_commands.command(name="시즌목록", description="근태 시즌 목록을 조회합니다.")
    @app_commands.guild_only()
    async def list_seasons(self, interaction: discord.Interaction) -> None:
        """현재 서버에 등록된 시즌 목록을 조회한다."""

        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        seasons = await self.season_service.list_seasons(guild_id=interaction.guild_id)
        if not seasons:
            await interaction.response.send_message("등록된 시즌이 없습니다.", ephemeral=True)
            return
        lines = ["시즌 목록"]
        for season in seasons[:10]:
            lines.append(
                f"- #{season['id']} {season['name']} "
                f"{season['start_date']}~{season['end_date']} / {season['status']}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="시즌시작", description="예약된 시즌을 활성화합니다.")
    @app_commands.guild_only()
    async def start_season(
        self,
        interaction: discord.Interaction,
        season_id: int,
    ) -> None:
        """예약 상태의 시즌을 활성 시즌으로 전환한다."""

        if not await self._ensure_officer(interaction):
            return
        updated = await self.season_service.start_season(
            guild_id=interaction.guild_id,
            season_id=season_id,
            now=datetime.now(timezone.utc),
        )
        message = "시즌을 시작했습니다." if updated else "시작할 수 있는 시즌을 찾지 못했습니다."
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="시즌종료", description="시즌을 종료하고 통계를 확정합니다.")
    @app_commands.guild_only()
    async def close_season(
        self,
        interaction: discord.Interaction,
        season_id: int,
    ) -> None:
        """시즌을 종료하고 현재 통계 스냅샷을 확정한다."""

        if not await self._ensure_officer(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        updated = await self.season_service.close_season(
            guild_id=interaction.guild_id,
            season_id=season_id,
            now=datetime.now(timezone.utc),
        )
        message = "시즌을 종료하고 통계를 확정했습니다." if updated else "종료할 수 있는 시즌을 찾지 못했습니다."
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="시즌취소", description="시즌을 취소합니다.")
    @app_commands.guild_only()
    async def cancel_season(
        self,
        interaction: discord.Interaction,
        season_id: int,
        reason: str = "",
    ) -> None:
        """예약 또는 활성 상태의 시즌을 취소한다."""

        if not await self._ensure_officer(interaction):
            return
        updated = await self.season_service.cancel_season(
            guild_id=interaction.guild_id,
            season_id=season_id,
            reason=reason or None,
            now=datetime.now(timezone.utc),
        )
        message = "시즌을 취소했습니다." if updated else "취소할 수 있는 시즌을 찾지 못했습니다."
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="시즌재집계", description="시즌 통계를 다시 계산합니다.")
    @app_commands.guild_only()
    async def reconcile_season(
        self,
        interaction: discord.Interaction,
        season_id: int,
    ) -> None:
        """시즌 통계 스냅샷을 다시 계산한다."""

        if not await self._ensure_officer(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        count = await self.season_service.reconcile_season(
            guild_id=interaction.guild_id,
            season_id=season_id,
            now=datetime.now(timezone.utc),
        )
        await interaction.followup.send(
            f"시즌 통계를 다시 계산했습니다. 대상 {count}명",
            ephemeral=True,
        )

    @app_commands.command(name="시즌랭킹", description="시즌 랭킹을 조회합니다.")
    @app_commands.guild_only()
    async def season_ranking(
        self,
        interaction: discord.Interaction,
        season_id: int | None = None,
    ) -> None:
        """활성 시즌 또는 지정한 시즌의 랭킹을 공개 조회한다."""

        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)
        result = await self.season_service.get_ranking(
            guild_id=interaction.guild_id,
            season_id=season_id,
        )
        if not result.configured:
            await interaction.followup.send("초기 설정이 필요합니다.", ephemeral=True)
            return
        if result.season is None:
            await interaction.followup.send("조회할 시즌이 없습니다.", ephemeral=True)
            return
        entries = result.entries or []
        if not entries:
            await interaction.followup.send("시즌 통계가 없습니다.", ephemeral=False)
            return
        lines = [f"시즌 랭킹: {result.season['name']}"]
        for entry in entries:
            lines.append(
                f"{entry.rank_no}. <@{entry.discord_id}> "
                f"{entry.season_score}점 / 출석률 {entry.attendance_rate:.1f}% / {entry.personal_rank}"
            )
        await interaction.followup.send("\n".join(lines), ephemeral=False)

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
