"""도움말 명령 카탈로그의 기본 품질을 검증한다."""

from bot.cogs.help import GUIDE_CATEGORIES


def test_help_catalog_has_usage_permission_and_parameters():
    """모든 도움말 항목에 사용 예시, 권한, 파라미터 설명이 있어야 한다."""

    assert GUIDE_CATEGORIES

    command_names = set()
    for category in GUIDE_CATEGORIES:
        assert category.key
        assert category.title
        assert category.description
        assert category.commands

        for command in category.commands:
            assert command.name.startswith("/")
            assert command.summary
            assert command.usage
            assert command.permission
            assert command.parameters
            command_names.add(command.name)

    assert "/출석" in command_names
    assert "/도움말" not in command_names
