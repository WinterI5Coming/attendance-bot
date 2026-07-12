"""업적과 칭호 관련 Discord 슬래시 명령.

이 Cog는 Stage C에서 추가된 업적/칭호 기능을 사용자 친화적인 화면으로
제공한다. 업적 지급처럼 시즌 통계가 필요한 기능은 `ENABLE_SEASONS`가
켜져 있을 때만 실행하고, 시즌 기능이 꺼져 있어도 사용자는 본인이 이미
획득한 업적과 칭호를 계속 조회하고 장착할 수 있다.
"""

from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.guild_service import GuildService
from bot.services.stage_c_service import AchievementService
from bot.utils.discord_messages import error_embed, info_embed, success_embed, truncate
from bot.utils.permissions import has_officer_permission


logger = logging.getLogger(__name__)


class AchievementsCog(commands.Cog):
    """업적, 칭호, 업적 역할 보상 명령을 제공한다."""

    def __init__(
        self,
        *,
        guild_service: GuildService,
        achievement_service: AchievementService,
        enable_season_awards: bool = False,
    ) -> None:
        """Cog 의존성을 저장한다.

        Args:
            guild_service: 서버 설정과 간부 권한 확인에 사용하는 서비스.
            achievement_service: 업적/칭호 조회와 지급 규칙을 담당하는 서비스.
            enable_season_awards: 시즌 기반 업적 평가 명령 활성화 여부.
        """

        self.guild_service = guild_service
        self.achievement_service = achievement_service
        self.enable_season_awards = enable_season_awards

    @app_commands.command(name="업적안내", description="업적과 칭호 사용 방법을 안내합니다.")
    @app_commands.guild_only()
    async def achievement_guide(self, interaction: discord.Interaction) -> None:
        """일반 사용자와 운영자를 위한 업적/칭호 사용법을 안내한다."""

        await interaction.response.send_message(
            embed=info_embed(
                title="업적과 칭호 안내",
                description=(
                    "업적은 출석, 연속 참여, 음성 검증 같은 활동 조건을 달성하면 "
                    "획득할 수 있습니다. 칭호는 업적으로 잠금 해제되며 프로필에 "
                    "하나만 장착할 수 있습니다."
                ),
                fields=(
                    (
                        "일반 사용자",
                        "`/내업적`으로 획득 업적을 확인하고 `/내칭호`에서 보유 칭호를 봅니다.\n"
                        "`/칭호장착`으로 대표 칭호를 바꿀 수 있습니다.",
                        False,
                    ),
                    (
                        "운영자",
                        "`/업적초기화`로 기본 업적을 준비하고 `/업적역할설정`으로 "
                        "업적 보상 역할을 연결합니다.",
                        False,
                    ),
                    (
                        "시즌 기능 상태",
                        (
                            "시즌 기반 업적 평가는 활성화되어 있습니다."
                            if self.enable_season_awards
                            else "현재 시즌 기능이 비활성화되어 신규 시즌 업적 평가는 중단되어 있습니다."
                        ),
                        False,
                    ),
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="업적초기화", description="기본 업적 정의를 준비합니다.")
    @app_commands.guild_only()
    async def ensure_achievements(self, interaction: discord.Interaction) -> None:
        """서버에 기본 업적 정의를 idempotent하게 생성하거나 갱신한다."""

        if not await self._ensure_officer(interaction):
            return
        await self.achievement_service.ensure_defaults(
            guild_id=interaction.guild_id,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            embed=success_embed(
                title="업적 초기화 완료",
                description="기본 업적 정의를 준비했습니다.",
                fields=(
                    ("다음 단계", "`/업적목록`으로 목록을 확인하세요.", False),
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="업적평가", description="시즌 기준으로 업적을 평가하고 보상을 지급합니다.")
    @app_commands.guild_only()
    async def evaluate_achievements(
        self,
        interaction: discord.Interaction,
        season_id: int,
    ) -> None:
        """시즌 통계 기준으로 업적 보상과 역할 보상을 지급한다.

        시즌 기능이 꺼진 서버에서는 이 명령을 실행하지 않고 안전한 안내만
        반환한다. 이미 지급된 업적/칭호 데이터는 그대로 보존된다.
        """

        if not await self._ensure_officer(interaction):
            return
        if not self.enable_season_awards:
            await interaction.response.send_message(
                embed=error_embed(
                    title="업적 평가가 비활성화되어 있습니다",
                    description="현재 `ENABLE_SEASONS=false` 상태라 시즌 기반 신규 업적 평가는 실행하지 않습니다.",
                    fields=(
                        ("보존되는 데이터", "기존 업적, 칭호, 역할 매핑 데이터는 삭제되지 않습니다.", False),
                        ("사용 가능한 명령", "`/내업적`, `/내칭호`, `/칭호장착`, `/업적목록`", False),
                    ),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        result = await self.achievement_service.evaluate_season(
            guild_id=interaction.guild_id,
            season_id=season_id,
            created_by_discord_id=interaction.user.id,
            now=datetime.now(timezone.utc),
        )
        role_successes = 0
        role_failures = 0
        if interaction.guild is not None:
            for grant in result.role_grants:
                member = interaction.guild.get_member(int(grant["discord_id"]))
                role = interaction.guild.get_role(int(grant["role_id"]))
                if member is None or role is None:
                    role_failures += 1
                    continue
                try:
                    await member.add_roles(
                        role,
                        reason=f"Achievement reward: {grant['achievement_code']}",
                    )
                    role_successes += 1
                except Exception:
                    logger.exception(
                        "Achievement role grant failed: guild_id=%s role_id=%s",
                        interaction.guild_id,
                        grant["role_id"],
                    )
                    role_failures += 1

        await interaction.followup.send(
            embed=success_embed(
                title="업적 평가 완료",
                description="시즌 통계를 기준으로 신규 업적을 평가했습니다.",
                fields=(
                    ("시즌 ID", f"`{season_id}`", True),
                    ("신규 지급", f"`{result.awarded_count}`건", True),
                    ("역할 부여", f"성공 `{role_successes}`건 / 실패 `{role_failures}`건", False),
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="업적목록", description="서버 업적 목록을 조회합니다.")
    @app_commands.guild_only()
    async def list_achievements(self, interaction: discord.Interaction) -> None:
        """서버에 등록된 활성 업적 정의를 조회한다."""

        if interaction.guild_id is None:
            await self._send_guild_only(interaction)
            return
        definitions = await self.achievement_service.list_definitions(
            guild_id=interaction.guild_id,
        )
        if not definitions:
            await interaction.response.send_message(
                embed=info_embed(
                    title="등록된 업적이 없습니다",
                    description="운영자가 `/업적초기화`를 먼저 실행해야 합니다.",
                ),
                ephemeral=True,
            )
            return

        lines = []
        for definition in definitions[:20]:
            title = definition["title_name"] or "없음"
            lines.append(
                f"- **{definition['name']}** (`{definition['code']}`)\n"
                f"  보상: `{definition['reward_score']:+d}점` / 칭호: `{title}`"
            )
        await interaction.response.send_message(
            embed=info_embed(
                title="업적 목록",
                description=truncate("\n".join(lines), 3500),
                fields=(
                    ("안내", "`/업적안내`에서 획득과 칭호 사용 방법을 확인할 수 있습니다.", False),
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="업적역할설정", description="업적 획득 역할 매핑을 설정합니다.")
    @app_commands.guild_only()
    async def set_achievement_role(
        self,
        interaction: discord.Interaction,
        achievement_code: str,
        role: discord.Role,
    ) -> None:
        """특정 업적 코드와 Discord 역할을 연결한다."""

        if not await self._ensure_officer(interaction):
            return
        mapped = await self.achievement_service.set_role_mapping(
            guild_id=interaction.guild_id,
            achievement_code=achievement_code,
            role_id=role.id,
            now=datetime.now(timezone.utc),
        )
        if not mapped:
            await interaction.response.send_message(
                embed=error_embed(
                    title="업적 코드를 찾지 못했습니다",
                    description="`/업적목록`에서 정확한 업적 코드를 확인한 뒤 다시 시도하세요.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=success_embed(
                title="업적 역할 매핑 저장 완료",
                description="업적을 새로 획득한 사용자에게 연결된 역할을 부여합니다.",
                fields=(
                    ("업적 코드", f"`{achievement_code}`", True),
                    ("역할", role.mention, True),
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="업적역할목록", description="업적 역할 매핑을 조회합니다.")
    @app_commands.guild_only()
    async def list_achievement_roles(self, interaction: discord.Interaction) -> None:
        """서버의 업적-역할 매핑 목록을 조회한다."""

        if interaction.guild_id is None:
            await self._send_guild_only(interaction)
            return
        mappings = await self.achievement_service.list_role_mappings(
            guild_id=interaction.guild_id,
        )
        if not mappings:
            await interaction.response.send_message(
                embed=info_embed(
                    title="업적 역할 매핑이 없습니다",
                    description="`/업적역할설정`으로 업적 코드와 Discord 역할을 연결할 수 있습니다.",
                ),
                ephemeral=True,
            )
            return
        lines = [
            f"- `{mapping['code']}` **{mapping['name']}** -> <@&{mapping['role_id']}>"
            for mapping in mappings[:20]
        ]
        await interaction.response.send_message(
            embed=info_embed(
                title="업적 역할 매핑",
                description=truncate("\n".join(lines), 3500),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="내업적", description="내가 획득한 업적을 조회합니다.")
    @app_commands.guild_only()
    async def my_achievements(self, interaction: discord.Interaction) -> None:
        """현재 사용자가 획득한 업적을 조회한다."""

        if interaction.guild_id is None:
            await self._send_guild_only(interaction)
            return
        achievements = await self.achievement_service.list_member_achievements(
            guild_id=interaction.guild_id,
            discord_id=interaction.user.id,
        )
        if not achievements:
            await interaction.response.send_message(
                embed=info_embed(
                    title="획득한 업적이 없습니다",
                    description="출석과 활동을 이어가면 업적을 획득할 수 있습니다.",
                    fields=(("도움말", "`/업적안내`에서 업적과 칭호 사용법을 확인하세요.", False),),
                ),
                ephemeral=True,
            )
            return
        lines = [
            f"- **{achievement['name']}** / `{achievement['reward_score']:+d}점`"
            for achievement in achievements[:20]
        ]
        await interaction.response.send_message(
            embed=info_embed(
                title="내 업적",
                description=truncate("\n".join(lines), 3500),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="내칭호", description="보유한 칭호를 조회합니다.")
    @app_commands.guild_only()
    async def my_titles(self, interaction: discord.Interaction) -> None:
        """현재 사용자가 잠금 해제한 칭호 목록을 조회한다."""

        if interaction.guild_id is None:
            await self._send_guild_only(interaction)
            return
        titles = await self.achievement_service.list_member_titles(
            guild_id=interaction.guild_id,
            discord_id=interaction.user.id,
        )
        if not titles:
            await interaction.response.send_message(
                embed=info_embed(
                    title="보유한 칭호가 없습니다",
                    description="업적을 달성하면 칭호가 잠금 해제됩니다.",
                ),
                ephemeral=True,
            )
            return
        lines = []
        for title in titles:
            marker = "장착 중" if title["is_equipped"] else "보유"
            lines.append(f"- **{title['title_name']}** (`{marker}`)")
        await interaction.response.send_message(
            embed=info_embed(
                title="내 칭호",
                description=truncate("\n".join(lines), 3500),
                fields=(("장착 방법", "`/칭호장착`을 실행하고 보유 칭호를 선택하세요.", False),),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="칭호장착", description="보유한 칭호를 장착합니다.")
    @app_commands.guild_only()
    async def equip_title(
        self,
        interaction: discord.Interaction,
        title_name: str,
    ) -> None:
        """사용자의 대표 칭호를 변경한다."""

        if interaction.guild_id is None:
            await self._send_guild_only(interaction)
            return
        equipped = await self.achievement_service.equip_title(
            guild_id=interaction.guild_id,
            discord_id=interaction.user.id,
            title_name=title_name,
            now=datetime.now(timezone.utc),
        )
        if not equipped:
            await interaction.response.send_message(
                embed=error_embed(
                    title="칭호를 장착할 수 없습니다",
                    description="보유하지 않은 칭호이거나 사용할 수 없는 칭호입니다.",
                    fields=(("해결 방법", "`/내칭호`에서 보유 칭호 이름을 확인하세요.", False),),
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=success_embed(
                title="칭호 장착 완료",
                description="대표 칭호를 변경했습니다.",
                fields=(("새 칭호", f"**{title_name}**", False),),
            ),
            ephemeral=True,
        )

    @equip_title.autocomplete("title_name")
    async def title_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """사용자가 보유한 칭호를 자동완성 후보로 제공한다."""

        if interaction.guild_id is None:
            return []
        titles = await self.achievement_service.list_member_titles(
            guild_id=interaction.guild_id,
            discord_id=interaction.user.id,
        )
        current_folded = current.casefold()
        choices = []
        for title in titles:
            title_name = title["title_name"]
            if current_folded and current_folded not in title_name.casefold():
                continue
            choices.append(app_commands.Choice(name=title_name, value=title_name))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="칭호해제", description="장착 중인 칭호를 해제합니다.")
    @app_commands.guild_only()
    async def unequip_title(self, interaction: discord.Interaction) -> None:
        """현재 장착 중인 대표 칭호를 해제한다."""

        if interaction.guild_id is None:
            await self._send_guild_only(interaction)
            return
        await self.achievement_service.unequip_title(
            guild_id=interaction.guild_id,
            discord_id=interaction.user.id,
            now=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(
            embed=success_embed(
                title="칭호 해제 완료",
                description="대표 칭호를 비워 두었습니다. 보유 칭호는 삭제되지 않습니다.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="사용자프로필", description="사용자의 업적과 칭호 요약을 조회합니다.")
    @app_commands.guild_only()
    async def user_profile(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member | None = None,
    ) -> None:
        """업적/칭호 중심의 간단한 사용자 프로필을 표시한다."""

        if interaction.guild_id is None:
            await self._send_guild_only(interaction)
            return
        member = target_member or interaction.user
        achievements = await self.achievement_service.list_member_achievements(
            guild_id=interaction.guild_id,
            discord_id=member.id,
        )
        titles = await self.achievement_service.list_member_titles(
            guild_id=interaction.guild_id,
            discord_id=member.id,
        )
        equipped_title = next(
            (title["title_name"] for title in titles if title["is_equipped"]),
            "없음",
        )
        recent_achievements = "\n".join(
            f"- {achievement['name']}"
            for achievement in achievements[:5]
        ) or "최근 업적이 없습니다."
        await interaction.response.send_message(
            embed=info_embed(
                title=f"{member.display_name} 프로필",
                description="업적과 칭호 중심의 공개 요약입니다.",
                fields=(
                    ("장착 칭호", equipped_title, True),
                    ("획득 업적", f"`{len(achievements)}`개", True),
                    ("보유 칭호", f"`{len(titles)}`개", True),
                    ("최근 업적", truncate(recent_achievements), False),
                ),
            ),
            ephemeral=False,
        )

    async def _ensure_officer(self, interaction: discord.Interaction) -> bool:
        """명령 실행자가 간부 또는 서버 관리자인지 확인한다."""

        if interaction.guild is None or interaction.guild_id is None:
            await self._send_guild_only(interaction)
            return False
        settings = await self.guild_service.get_settings(interaction.guild_id)
        officer_role_id = None if settings is None else settings["officer_role_id"]
        if not has_officer_permission(interaction, officer_role_id):
            await interaction.response.send_message(
                embed=error_embed(
                    title="권한이 필요합니다",
                    description="이 명령은 간부 또는 서버 관리자만 사용할 수 있습니다.",
                ),
                ephemeral=True,
            )
            return False
        return True

    async def _send_guild_only(self, interaction: discord.Interaction) -> None:
        """서버 전용 명령을 DM에서 실행했을 때 안내한다."""

        await interaction.response.send_message(
            embed=error_embed(
                title="서버에서만 사용할 수 있습니다",
                description="이 명령은 Discord 서버 안에서 실행해야 합니다.",
            ),
            ephemeral=True,
        )
