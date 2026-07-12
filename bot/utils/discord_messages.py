"""Discord 응답 메시지를 일관된 형식으로 만들기 위한 호환 유틸리티.

새 코드에서는 `bot.ui.embed_factory`와 `bot.ui.formatters`를 우선 사용한다.
이 모듈은 이미 작성된 Cog가 같은 API로 계속 동작하도록 얇은 wrapper 역할을
한다.
"""

from collections.abc import Iterable

import discord

from bot.ui.embed_factory import EMBEDS
from bot.ui.formatters import truncate
from bot.ui.message_theme import DEFAULT_THEME


BRAND_COLOR = DEFAULT_THEME.brand
SUCCESS_COLOR = DEFAULT_THEME.success
WARNING_COLOR = DEFAULT_THEME.warning
ERROR_COLOR = DEFAULT_THEME.error
INFO_COLOR = DEFAULT_THEME.info


def build_embed(
    *,
    title: str,
    description: str | None = None,
    color: discord.Color = BRAND_COLOR,
    fields: Iterable[tuple[str, str, bool]] | None = None,
    footer: str | None = "Attendance Bot",
) -> discord.Embed:
    """표준 Discord Embed를 생성한다.

    Args:
        title: Embed 상단에 표시할 제목.
        description: 제목 아래에 표시할 본문. 필요 없으면 ``None``.
        color: Embed 왼쪽 강조선 색상.
        fields: ``(이름, 값, inline 여부)`` 튜플 목록.
        footer: 하단 보조 문구. ``None``이면 footer를 표시하지 않는다.

    Returns:
        Discord 응답에 바로 전달할 수 있는 ``discord.Embed`` 객체.
    """

    return EMBEDS.build(
        title=title,
        description=description,
        color=color,
        fields=fields,
        footer=footer,
    )


def success_embed(
    title: str,
    description: str,
    fields: Iterable[tuple[str, str, bool]] | None = None,
) -> discord.Embed:
    """성공 결과를 초록색 Embed로 표현한다."""

    return EMBEDS.success(title, description, fields)


def error_embed(
    title: str,
    description: str,
    fields: Iterable[tuple[str, str, bool]] | None = None,
) -> discord.Embed:
    """사용자가 조치해야 하는 오류를 빨간색 Embed로 표현한다."""

    return EMBEDS.error(title, description, fields)


def info_embed(
    title: str,
    description: str,
    fields: Iterable[tuple[str, str, bool]] | None = None,
) -> discord.Embed:
    """일반 안내 메시지를 파란색 Embed로 표현한다."""

    return EMBEDS.info(title, description, fields)
