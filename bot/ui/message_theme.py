"""Discord 메시지 색상과 공통 문구를 정의한다."""

from dataclasses import dataclass

import discord


@dataclass(frozen=True)
class MessageTheme:
    """메시지 종류별 Embed 색상과 footer 문구."""

    success: discord.Color = discord.Color.from_rgb(46, 204, 113)
    info: discord.Color = discord.Color.from_rgb(52, 152, 219)
    warning: discord.Color = discord.Color.from_rgb(245, 166, 35)
    error: discord.Color = discord.Color.from_rgb(231, 76, 60)
    admin: discord.Color = discord.Color.from_rgb(108, 92, 231)
    brand: discord.Color = discord.Color.from_rgb(64, 120, 255)
    footer: str = "Attendance Bot"


DEFAULT_THEME = MessageTheme()
