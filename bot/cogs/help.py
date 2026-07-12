"""사용자 친화적인 명령어 도움말 Cog.

Discord의 자동 슬래시 명령 설명은 짧은 한 줄 안내에 적합하지만, 실제
운영자는 "언제", "누가", "어떤 파라미터로" 써야 하는지를 한 화면에서
보고 싶어 한다. 이 Cog는 그런 운영 문서를 봇 안에서 바로 확인할 수
있도록 카테고리별 도움말을 제공한다.
"""

from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.discord_messages import BRAND_COLOR, build_embed, truncate


@dataclass(frozen=True)
class CommandGuide:
    """도움말에 표시할 한 개 명령의 설명 데이터."""

    name: str
    summary: str
    usage: str
    permission: str
    parameters: str


@dataclass(frozen=True)
class GuideCategory:
    """도움말 카테고리와 그 안에 속한 명령 목록."""

    key: str
    title: str
    description: str
    commands: tuple[CommandGuide, ...]


GUIDE_CATEGORIES: tuple[GuideCategory, ...] = (
    GuideCategory(
        key="start",
        title="시작하기",
        description="서버 최초 설정과 대원 등록에 필요한 명령입니다.",
        commands=(
            CommandGuide(
                name="/초기설정",
                summary="근태관리봇을 서버에 연결하고 기본 역할/채널을 저장합니다.",
                usage="/초기설정 간부역할:@간부 출석채널:#출석 공지채널:#공지",
                permission="서버 관리자",
                parameters="간부역할, 출석채널, 공지채널",
            ),
            CommandGuide(
                name="/출석시간설정",
                summary="출석 시작, 지각 기준, 출석 마감 시간을 변경합니다.",
                usage="/출석시간설정 출석시작:21:30 지각기준:21:40 마감:21:45",
                permission="서버 관리자",
                parameters="HH:MM 형식의 출석시작, 지각기준, 마감",
            ),
            CommandGuide(
                name="/대원등록",
                summary="Discord 사용자를 출석 대상자로 등록합니다.",
                usage="/대원등록 사용자:@홍길동",
                permission="간부 또는 서버 관리자",
                parameters="사용자",
            ),
            CommandGuide(
                name="/대원제외",
                summary="대원을 이후 출석 대상에서 제외합니다. 과거 기록은 유지됩니다.",
                usage="/대원제외 사용자:@홍길동",
                permission="간부 또는 서버 관리자",
                parameters="사용자",
            ),
            CommandGuide(
                name="/대원목록",
                summary="현재 활성 대원 목록을 확인합니다.",
                usage="/대원목록",
                permission="전체 사용자",
                parameters="없음",
            ),
        ),
    ),
    GuideCategory(
        key="attendance",
        title="출석",
        description="매일 사용하는 출석 체크와 현황 확인 명령입니다.",
        commands=(
            CommandGuide(
                name="/출석",
                summary="오늘 열린 출석 세션에 체크인합니다.",
                usage="/출석",
                permission="등록된 대원",
                parameters="없음",
            ),
            CommandGuide(
                name="/출석현황",
                summary="오늘 세션의 정상/지각/결석/미체크 현황을 확인합니다.",
                usage="/출석현황",
                permission="전체 사용자",
                parameters="없음",
            ),
            CommandGuide(
                name="/출석수정",
                summary="특정 날짜의 출석 기록을 관리자가 정정합니다.",
                usage="/출석수정 사용자:@홍길동 날짜:2026-07-02 상태:PRESENT 사유:운영자 확인",
                permission="간부 또는 서버 관리자",
                parameters="사용자, 날짜(YYYY-MM-DD), 상태(PRESENT/LATE/ABSENT), 사유",
            ),
            CommandGuide(
                name="/오늘출석취소",
                summary="오늘 세션을 취소하고 필요한 점수 보정 이벤트를 남깁니다.",
                usage="/오늘출석취소 사유:서버 점검",
                permission="간부 또는 서버 관리자",
                parameters="사유",
            ),
            CommandGuide(
                name="/오늘출석재개",
                summary="취소된 오늘 세션을 다시 열고 점수를 복원합니다.",
                usage="/오늘출석재개",
                permission="간부 또는 서버 관리자",
                parameters="없음",
            ),
        ),
    ),
    GuideCategory(
        key="excuse",
        title="사유 신청",
        description="지각/결석 사유 신청과 승인 흐름입니다.",
        commands=(
            CommandGuide(
                name="/사유신청",
                summary="출석 시작 전에 지각 또는 결석 사유를 신청합니다.",
                usage="/사유신청 날짜:2026-07-02 사유:야근 예상시간:21:50",
                permission="등록된 대원",
                parameters="날짜, 사유, 예상시간(선택)",
            ),
            CommandGuide(
                name="/사유취소",
                summary="아직 반영되지 않은 본인의 사유 신청을 취소합니다.",
                usage="/사유취소 신청id:12",
                permission="신청자 본인",
                parameters="신청id",
            ),
            CommandGuide(
                name="/사유목록",
                summary="사유 신청 목록을 조회합니다.",
                usage="/사유목록 전체조회:true 상태:PENDING",
                permission="본인 목록은 전체 사용자, 전체조회는 간부 이상",
                parameters="전체조회, 상태",
            ),
            CommandGuide(
                name="/사유승인",
                summary="대기 중인 사유 신청을 승인합니다.",
                usage="/사유승인 신청id:12",
                permission="간부 또는 서버 관리자",
                parameters="신청id",
            ),
            CommandGuide(
                name="/사유거절",
                summary="대기 중인 사유 신청을 거절합니다.",
                usage="/사유거절 신청id:12 사유:증빙 부족",
                permission="간부 또는 서버 관리자",
                parameters="신청id, 사유",
            ),
        ),
    ),
    GuideCategory(
        key="report",
        title="리포트와 점수",
        description="개인 통계, 랭킹, 평가 점수 관련 명령입니다.",
        commands=(
            CommandGuide(
                name="/내정보",
                summary="내 출석 통계, 총점, 계급, 최근 점수 변동을 확인합니다.",
                usage="/내정보",
                permission="등록된 대원",
                parameters="없음",
            ),
            CommandGuide(
                name="/랭킹",
                summary="서버 출석 점수 랭킹을 확인합니다.",
                usage="/랭킹",
                permission="전체 사용자",
                parameters="없음",
            ),
            CommandGuide(
                name="/리포트",
                summary="대상자의 공개 가능한 근태 리포트를 확인합니다.",
                usage="/리포트 사용자:@홍길동",
                permission="전체 사용자",
                parameters="사용자",
            ),
            CommandGuide(
                name="/주간보고",
                summary="이번 주 또는 지난 주 근태 요약을 확인합니다.",
                usage="/주간보고 지난주:false",
                permission="전체 사용자",
                parameters="지난주 여부",
            ),
            CommandGuide(
                name="/평가",
                summary="대상자에게 운영 평가 점수를 부여합니다.",
                usage="/평가 사용자:@홍길동 점수:5 사유:운영 기여",
                permission="간부 또는 서버 관리자",
                parameters="사용자, 점수, 사유",
            ),
            CommandGuide(
                name="/점수조정",
                summary="대상자의 점수를 수동으로 조정합니다.",
                usage="/점수조정 사용자:@홍길동 점수:-2 사유:정책 위반",
                permission="간부 또는 서버 관리자",
                parameters="사용자, 점수, 사유",
            ),
        ),
    ),
    GuideCategory(
        key="adjustment",
        title="감면과 면제",
        description="Stage B의 지각 감면과 결석 면제 명령입니다.",
        commands=(
            CommandGuide(
                name="/지각감면",
                summary="승인된 사유를 근거로 지각 시간을 감면합니다.",
                usage="/지각감면 사용자:@홍길동 날짜:2026-07-02 감면분:5 사유ID:12",
                permission="간부 또는 서버 관리자",
                parameters="사용자, 날짜, 감면분, 사유ID",
            ),
            CommandGuide(
                name="/지각감면취소",
                summary="활성 지각 감면을 취소하고 점수를 되돌립니다.",
                usage="/지각감면취소 조정ID:3 사유:오입력",
                permission="간부 또는 서버 관리자",
                parameters="조정ID, 사유",
            ),
            CommandGuide(
                name="/결석면제",
                summary="승인된 사유를 근거로 결석 감점을 면제합니다.",
                usage="/결석면제 사용자:@홍길동 날짜:2026-07-02 사유ID:12",
                permission="간부 또는 서버 관리자",
                parameters="사용자, 날짜, 사유ID",
            ),
            CommandGuide(
                name="/결석면제취소",
                summary="활성 결석 면제를 취소하고 점수를 되돌립니다.",
                usage="/결석면제취소 조정ID:4 사유:정책 변경",
                permission="간부 또는 서버 관리자",
                parameters="조정ID, 사유",
            ),
        ),
    ),
    GuideCategory(
        key="season",
        title="업적과 칭호",
        description="업적, 칭호, 사용자 프로필 명령입니다. 시즌 명령은 기본 비활성화 상태입니다.",
        commands=(
            CommandGuide(
                name="/업적안내",
                summary="업적과 칭호 사용 방법을 안내합니다.",
                usage="/업적안내",
                permission="전체 사용자",
                parameters="없음",
            ),
            CommandGuide(
                name="/내업적",
                summary="내가 획득한 업적을 조회합니다.",
                usage="/내업적",
                permission="전체 사용자",
                parameters="없음",
            ),
            CommandGuide(
                name="/내칭호",
                summary="보유한 칭호와 현재 장착 상태를 조회합니다.",
                usage="/내칭호",
                permission="전체 사용자",
                parameters="없음",
            ),
            CommandGuide(
                name="/칭호장착",
                summary="보유한 칭호를 대표 칭호로 장착합니다.",
                usage="/칭호장착 칭호명:Perfect",
                permission="칭호 보유자",
                parameters="칭호명(자동완성 지원)",
            ),
            CommandGuide(
                name="/사용자프로필",
                summary="사용자의 업적과 칭호 공개 요약을 조회합니다.",
                usage="/사용자프로필 사용자:@홍길동",
                permission="전체 사용자",
                parameters="사용자(선택)",
            ),
            CommandGuide(
                name="/업적역할설정",
                summary="업적 코드와 Discord 역할을 연결합니다.",
                usage="/업적역할설정 업적코드:FIRST_PRESENT 역할:@출석왕",
                permission="간부 또는 서버 관리자",
                parameters="업적코드, 역할",
            ),
            CommandGuide(
                name="/업적평가",
                summary="시즌 기능이 활성화된 서버에서만 신규 업적을 평가합니다.",
                usage="/업적평가 시즌ID:1",
                permission="간부 또는 서버 관리자",
                parameters="시즌ID, ENABLE_SEASONS=true 필요",
            ),
        ),
    ),
    GuideCategory(
        key="officer",
        title="간부 인사",
        description="시즌 통계 기반 간부 평가와 역할 변경 명령입니다. 기본값에서는 비활성화되어 있습니다.",
        commands=(
            CommandGuide(
                name="/간부평가기준설정",
                summary="간부 인사 평가 기준과 역할을 설정합니다.",
                usage="/간부평가기준설정 enabled:true 정원:5 최소세션:5 승격기준:80 유지기준:65",
                permission="서버 관리자",
                parameters="활성화 여부, 정원, 최소세션, 승격/유지 기준, 역할(선택)",
            ),
            CommandGuide(
                name="/간부인사미리보기",
                summary="역할 변경 없이 인사안을 저장하고 확인합니다.",
                usage="/간부인사미리보기 시즌ID:1",
                permission="간부 또는 서버 관리자",
                parameters="시즌ID(선택)",
            ),
            CommandGuide(
                name="/간부인사실행",
                summary="저장된 미리보기를 실제 Discord 역할 변경으로 적용합니다.",
                usage="/간부인사실행 review_id:7",
                permission="서버 관리자",
                parameters="review_id",
            ),
            CommandGuide(
                name="/계급변경이력",
                summary="간부 역할 변경 성공/실패 이력을 조회합니다.",
                usage="/계급변경이력",
                permission="간부 또는 서버 관리자",
                parameters="없음",
            ),
        ),
    ),
)


CATEGORY_CHOICES = [
    app_commands.Choice(name=category.title, value=category.key)
    for category in GUIDE_CATEGORIES
]


class HelpCog(commands.Cog):
    """봇 안에서 확인할 수 있는 상세 사용 설명서를 제공한다."""

    @app_commands.command(name="도움말", description="근태관리봇 명령어 사용법을 자세히 확인합니다.")
    @app_commands.guild_only()
    @app_commands.choices(카테고리=CATEGORY_CHOICES)
    async def help_command(
        self,
        interaction: discord.Interaction,
        카테고리: app_commands.Choice[str] | None = None,
    ) -> None:
        """카테고리별 명령 사용법을 Embed로 응답한다.

        Args:
            interaction: Discord 슬래시 명령 상호작용 객체.
            카테고리: 사용자가 선택한 도움말 카테고리. 없으면 전체 목차를 보여준다.
        """

        if 카테고리 is None:
            await interaction.response.send_message(
                embed=self._build_index_embed(),
                ephemeral=True,
            )
            return

        category = self._find_category(카테고리.value)
        if category is None:
            await interaction.response.send_message(
                "알 수 없는 도움말 카테고리입니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=self._build_category_embed(category),
            ephemeral=True,
        )

    def _build_index_embed(self) -> discord.Embed:
        """도움말 첫 화면에 표시할 카테고리 목차를 만든다."""

        fields = []
        for category in GUIDE_CATEGORIES:
            command_names = ", ".join(command.name for command in category.commands[:4])
            if len(category.commands) > 4:
                command_names += " ..."
            fields.append(
                (
                    category.title,
                    f"{category.description}\n`/도움말 카테고리:{category.title}`\n{command_names}",
                    False,
                )
            )

        return build_embed(
            title="근태관리봇 도움말",
            description=(
                "필요한 카테고리를 선택하면 명령어 사용 예시, 파라미터, 권한을 "
                "한 번에 확인할 수 있습니다."
            ),
            color=BRAND_COLOR,
            fields=fields,
            footer="Tip: 처음 운영자는 '시작하기'부터 확인하세요.",
        )

    def _build_category_embed(self, category: GuideCategory) -> discord.Embed:
        """선택된 카테고리의 상세 명령 목록 Embed를 만든다."""

        fields = []
        for command in category.commands:
            value = (
                f"{command.summary}\n"
                f"사용: `{command.usage}`\n"
                f"권한: {command.permission}\n"
                f"파라미터: {command.parameters}"
            )
            fields.append((command.name, truncate(value), False))

        return build_embed(
            title=f"도움말: {category.title}",
            description=category.description,
            color=BRAND_COLOR,
            fields=fields,
            footer="명령 입력창에서 / 를 누르면 Discord가 실제 파라미터 입력칸을 보여줍니다.",
        )

    def _find_category(self, key: str) -> GuideCategory | None:
        """카테고리 key로 도움말 데이터를 찾는다."""

        for category in GUIDE_CATEGORIES:
            if category.key == key:
                return category
        return None
