"""사용자 친화적인 명령어 도움말 Cog.

Discord의 자동 슬래시 명령 설명은 짧은 한 줄 안내에 적합하지만, 실제
운영자는 "언제", "누가", "어떤 파라미터로" 써야 하는지를 한 화면에서
보고 싶어 한다. 이 Cog는 그런 운영 문서를 봇 안에서 바로 확인할 수
있도록 명령 그룹별 도움말을 제공한다.
"""

from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from bot.ui.embed_factory import EMBEDS
from bot.ui.formatters import truncate


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


OFFICER = "간부 또는 서버 관리자"
ADMIN = "서버 관리자"
MEMBER = "등록된 대원"
EVERYONE = "전체 사용자"

GUIDE_CATEGORIES: tuple[GuideCategory, ...] = (
    GuideCategory(
        key="settings",
        title="설정",
        description="서버 최초 설정과 근태 설정 변경 명령입니다. (`/설정 ...`)",
        commands=(
            CommandGuide(
                name="/설정 초기화",
                summary="근태관리봇을 서버에 연결하고 기본 역할/채널을 저장합니다.",
                usage="/설정 초기화 간부역할:@간부 출석채널:#출석 공지채널:#공지",
                permission=ADMIN,
                parameters="간부역할, 출석채널, 공지채널",
            ),
            CommandGuide(
                name="/설정 조회",
                summary="현재 서버의 근태 설정 값을 확인합니다.",
                usage="/설정 조회",
                permission=OFFICER,
                parameters="없음",
            ),
            CommandGuide(
                name="/설정 변경",
                summary="설정 한 항목을 변경합니다. (시간대, 출석 요일, 음성 검증 등)",
                usage="/설정 변경 항목:attendance_days 값:MON,WED,FRI",
                permission=OFFICER,
                parameters="항목(선택지), 값",
            ),
            CommandGuide(
                name="/설정 출석시간",
                summary="출석 시작, 지각 기준, 마감 시간을 한 번에 변경합니다.",
                usage="/설정 출석시간 출석시작:21:30 지각기준:21:40 마감시간:21:45",
                permission=ADMIN,
                parameters="HH:MM 형식의 출석시작, 지각기준, 마감시간",
            ),
        ),
    ),
    GuideCategory(
        key="members",
        title="대원",
        description="출석 대상 대원을 관리하는 명령입니다. (`/대원 ...`)",
        commands=(
            CommandGuide(
                name="/대원 등록",
                summary="Discord 사용자를 출석 대상자로 등록합니다.",
                usage="/대원 등록 사용자:@홍길동",
                permission=OFFICER,
                parameters="사용자",
            ),
            CommandGuide(
                name="/대원 제외",
                summary="대원을 이후 출석 대상에서 제외합니다. 과거 기록은 유지됩니다.",
                usage="/대원 제외 사용자:@홍길동 사유:전역",
                permission=OFFICER,
                parameters="사용자, 사유(2~200자)",
            ),
            CommandGuide(
                name="/대원 목록",
                summary="현재 활성 대원 목록을 확인합니다.",
                usage="/대원 목록",
                permission=EVERYONE,
                parameters="없음",
            ),
        ),
    ),
    GuideCategory(
        key="attendance",
        title="출석",
        description="매일 출석은 공지의 버튼으로, 그 외 운영은 `/출석 ...` 명령으로 처리합니다.",
        commands=(
            CommandGuide(
                name="출석하기 버튼",
                summary="출석 시작 공지에 붙은 [✅ 출석하기] 버튼을 누르면 바로 체크인됩니다. 마감되면 버튼이 잠깁니다.",
                usage="공지 메시지의 버튼 클릭",
                permission=MEMBER,
                parameters="없음",
            ),
            CommandGuide(
                name="/출석 체크인",
                summary="버튼 대신 명령으로 체크인합니다. 결과는 동일합니다.",
                usage="/출석 체크인",
                permission=MEMBER,
                parameters="없음",
            ),
            CommandGuide(
                name="/출석 현황",
                summary="오늘 세션의 정상/지각/결석/미체크 현황을 확인합니다.",
                usage="/출석 현황",
                permission=EVERYONE,
                parameters="없음",
            ),
            CommandGuide(
                name="/출석 수정",
                summary="특정 날짜의 출석 기록을 정정하고 점수를 보정합니다.",
                usage="/출석 수정 사용자:@홍길동 날짜:2026-07-02 상태:지각 사유:늦은 체크인 확인",
                permission=OFFICER,
                parameters="사용자, 날짜(YYYY-MM-DD), 상태, 사유",
            ),
            CommandGuide(
                name="/출석 오늘취소",
                summary="오늘 출석 세션을 취소하고 이미 반영된 점수를 되돌립니다.",
                usage="/출석 오늘취소 사유:서버 점검",
                permission=OFFICER,
                parameters="사유(2~500자)",
            ),
            CommandGuide(
                name="/출석 오늘재개",
                summary="취소된 오늘 출석 세션을 다시 엽니다.",
                usage="/출석 오늘재개",
                permission=OFFICER,
                parameters="없음",
            ),
        ),
    ),
    GuideCategory(
        key="excuses",
        title="사유",
        description="신청은 입력창, 승인/거절은 버튼으로 처리합니다. (`/사유 ...`)",
        commands=(
            CommandGuide(
                name="/사유 신청",
                summary="입력창이 열립니다. 유형(결석/지각/조퇴)을 고르고 날짜와 사유를 적어 제출하면 간부에게 알림이 갑니다.",
                usage="/사유 신청 → 입력창 작성 → 제출",
                permission=MEMBER,
                parameters="입력창: 유형, 날짜(YYYY-MM-DD, 기본값 내일), 사유(2~500자)",
            ),
            CommandGuide(
                name="/사유 취소",
                summary="아직 처리되지 않은 내 사유 신청을 취소합니다.",
                usage="/사유 취소 신청번호:12",
                permission=MEMBER,
                parameters="신청번호(/사유 목록에서 확인)",
            ),
            CommandGuide(
                name="/사유 목록",
                summary="내 사유 신청 목록을 봅니다. 간부는 전체조회로 서버 전체를 볼 수 있습니다.",
                usage="/사유 목록 상태:대기 전체조회:True",
                permission=EVERYONE,
                parameters="상태(선택), 전체조회(간부)",
            ),
            CommandGuide(
                name="/사유 검토",
                summary="대기 중인 신청 목록에서 하나를 고르면 상세 내용과 [승인]/[거절] 버튼이 나타납니다. 접수 알림의 [검토하기] 버튼과 같습니다.",
                usage="/사유 검토 → 신청 선택 → 승인 또는 거절",
                permission=OFFICER,
                parameters="없음 (거절 시 사유 입력창)",
            ),
            CommandGuide(
                name="/사유 예외등록",
                summary="마감 이후 긴급 사유를 간부가 승인 상태로 직접 등록합니다.",
                usage="/사유 예외등록 사용자:@홍길동 날짜:2026-07-03 유형:결석 사유:응급 관리자메모:전화 확인",
                permission=OFFICER,
                parameters="사용자, 날짜, 유형, 사유, 관리자메모",
            ),
            CommandGuide(
                name="/사유 정책",
                summary="현재 신청 마감 정책을 확인합니다. 마감시간+마감일수를 주면 변경, 공지:True면 채널에 공개합니다.",
                usage="/사유 정책 마감시간:23:00 마감일수:1 공지:True",
                permission="조회는 전체 사용자, 변경/공지는 " + OFFICER,
                parameters="마감시간(선택), 마감일수(선택), 공지(선택)",
            ),
        ),
    ),
    GuideCategory(
        key="reports",
        title="리포트",
        description="개인 통계, 랭킹, 주간 보고 명령입니다.",
        commands=(
            CommandGuide(
                name="/내정보",
                summary="내 점수, 계급, 출석률, 최근 점수 변동을 봅니다. 사용자를 지정하면 그 사람의 공개 리포트를 채널에 표시합니다.",
                usage="/내정보 또는 /내정보 사용자:@홍길동",
                permission=EVERYONE,
                parameters="사용자(선택)",
            ),
            CommandGuide(
                name="/랭킹",
                summary="서버 전체 점수 랭킹을 공개합니다.",
                usage="/랭킹",
                permission=EVERYONE,
                parameters="없음",
            ),
            CommandGuide(
                name="/주간보고",
                summary="이번 주 또는 지난 주 서버 근태 통계를 요약합니다.",
                usage="/주간보고 지난주:True",
                permission=EVERYONE,
                parameters="지난주(선택)",
            ),
        ),
    ),
    GuideCategory(
        key="scores",
        title="점수",
        description="간부가 점수 장부에 개입하는 명령입니다. 기존 기록은 지우지 않고 보정 이벤트를 추가합니다. (`/점수 ...`)",
        commands=(
            CommandGuide(
                name="/점수 평가",
                summary="대상자에게 -5~+5 평가 점수를 부여합니다.",
                usage="/점수 평가 사용자:@홍길동 점수:3 사유:훈련 우수",
                permission=OFFICER,
                parameters="사용자, 점수, 사유",
            ),
            CommandGuide(
                name="/점수 평가취소",
                summary="평가를 취소하고 반대 점수를 생성합니다.",
                usage="/점수 평가취소 평가번호:7 취소사유:중복 입력",
                permission=OFFICER,
                parameters="평가번호, 취소사유",
            ),
            CommandGuide(
                name="/점수 조정",
                summary="대상자의 점수를 수동으로 가감합니다.",
                usage="/점수 조정 사용자:@홍길동 점수:-10 사유:규정 위반",
                permission=OFFICER,
                parameters="사용자, 점수(-1000~+1000), 사유",
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

        return EMBEDS.build(
            title="근태관리봇 도움말",
            description=(
                "필요한 카테고리를 선택하면 명령어 사용 예시, 파라미터, 권한을 "
                "한 번에 확인할 수 있습니다."
            ),
            fields=fields,
            footer="Tip: 처음 운영자는 '설정'부터 확인하세요.",
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

        return EMBEDS.build(
            title=f"도움말: {category.title}",
            description=category.description,
            fields=fields,
            footer="명령 입력창에서 / 를 누르면 Discord가 실제 파라미터 입력칸을 보여줍니다.",
        )

    def _find_category(self, key: str) -> GuideCategory | None:
        """카테고리 key로 도움말 데이터를 찾는다."""

        for category in GUIDE_CATEGORIES:
            if category.key == key:
                return category
        return None
