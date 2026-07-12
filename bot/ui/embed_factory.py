"""표준 Discord Embed를 만드는 팩토리."""

from collections.abc import Iterable

import discord

from bot.ui.message_theme import DEFAULT_THEME, MessageTheme


class EmbedFactory:
    """메시지 종류별 Embed 생성 규칙을 캡슐화한다."""

    def __init__(self, theme: MessageTheme = DEFAULT_THEME) -> None:
        """팩토리가 사용할 메시지 테마를 저장한다."""

        self.theme = theme

    def build(
        self,
        *,
        title: str,
        description: str | None = None,
        color: discord.Color | None = None,
        fields: Iterable[tuple[str, str, bool]] | None = None,
        footer: str | None = None,
    ) -> discord.Embed:
        """공통 footer와 field 규칙을 적용한 Embed를 생성한다."""

        embed = discord.Embed(
            title=title,
            description=description,
            color=color or self.theme.brand,
        )
        for name, value, inline in fields or []:
            embed.add_field(
                name=name,
                value=value,
                inline=inline,
            )
        embed.set_footer(text=footer if footer is not None else self.theme.footer)
        return embed

    def success(
        self,
        title: str,
        description: str,
        fields: Iterable[tuple[str, str, bool]] | None = None,
    ) -> discord.Embed:
        """성공 결과를 표현하는 Embed를 만든다."""

        return self.build(
            title=title,
            description=description,
            color=self.theme.success,
            fields=fields,
        )

    def info(
        self,
        title: str,
        description: str,
        fields: Iterable[tuple[str, str, bool]] | None = None,
    ) -> discord.Embed:
        """조회 결과나 일반 안내를 표현하는 Embed를 만든다."""

        return self.build(
            title=title,
            description=description,
            color=self.theme.info,
            fields=fields,
        )

    def warning(
        self,
        title: str,
        description: str,
        fields: Iterable[tuple[str, str, bool]] | None = None,
    ) -> discord.Embed:
        """주의가 필요한 상태를 표현하는 Embed를 만든다."""

        return self.build(
            title=title,
            description=description,
            color=self.theme.warning,
            fields=fields,
        )

    def error(
        self,
        title: str,
        description: str,
        fields: Iterable[tuple[str, str, bool]] | None = None,
    ) -> discord.Embed:
        """사용자가 해결할 수 있는 실패 상태를 표현하는 Embed를 만든다."""

        return self.build(
            title=title,
            description=description,
            color=self.theme.error,
            fields=fields,
        )

    def admin(
        self,
        title: str,
        description: str,
        fields: Iterable[tuple[str, str, bool]] | None = None,
    ) -> discord.Embed:
        """운영자 전용 결과를 표현하는 Embed를 만든다."""

        return self.build(
            title=title,
            description=description,
            color=self.theme.admin,
            fields=fields,
        )


EMBEDS = EmbedFactory()
