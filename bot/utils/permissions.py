"""Discord 역할과 관리자 권한을 검사하는 유틸리티."""

import discord


def is_server_admin(
    interaction: discord.Interaction,
) -> bool:
    """명령 실행자가 서버 소유자 또는 관리자인지 검사한다.

    Args:
        interaction:
            Discord 명령 실행 정보.

    Returns:
        서버 소유자 또는 Administrator 권한 보유자면 True.
    """

    guild = interaction.guild
    user = interaction.user

    if guild is None:
        return False

    if not isinstance(user, discord.Member):
        return False

    if user.id == guild.owner_id:
        return True

    return user.guild_permissions.administrator