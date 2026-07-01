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


def has_officer_permission(
    interaction: discord.Interaction,
    officer_role_id: str | None,
) -> bool:
    """명령 실행자가 간부 역할 보유자 또는 관리자인지 검사한다.

    서버 소유자와 Administrator 권한 보유자는 간부 역할이 없어도
    항상 허용한다. `officer_role_id`가 비어 있거나, 숫자로 변환할
    수 없거나, 서버에서 삭제된 역할을 가리키거나, 사용자가 해당
    역할을 보유하지 않은 경우 False를 반환한다.

    DB에 저장된 값은 신뢰하지 않고, 매 호출마다 Discord 서버에서
    현재 역할과 사용자의 역할 보유 여부를 다시 조회해 검사한다.

    Args:
        interaction:
            Discord 명령 실행 정보.
        officer_role_id:
            `guild_settings.officer_role_id`에 저장된 역할 ID 문자열.

    Returns:
        서버 소유자, Administrator, 또는 현재 간부 역할 보유자면 True.
    """

    if is_server_admin(interaction):
        return True

    guild = interaction.guild
    user = interaction.user

    if guild is None:
        return False

    if not isinstance(user, discord.Member):
        return False

    if not officer_role_id:
        return False

    try:
        officer_role_id_int = int(officer_role_id)
    except (TypeError, ValueError):
        return False

    officer_role = guild.get_role(officer_role_id_int)

    if officer_role is None:
        return False

    return officer_role in user.roles