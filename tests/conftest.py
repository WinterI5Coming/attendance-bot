"""테스트 전반에서 공유하는 SQLite 기반 fixture."""

import pytest_asyncio

from bot.db.database import Database
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.services.member_service import MemberService


# members.guild_id는 guild_settings.guild_id를 참조하므로,
# 테스트에서 사용할 서버 ID에 대한 기본 설정 행을 미리 만들어야 한다.
TEST_GUILD_IDS = ("111", "222")


@pytest_asyncio.fixture
async def database(tmp_path):
    """임시 파일에 마이그레이션이 적용된 Database를 제공한다."""

    db = Database(tmp_path / "test.db")
    await db.initialize()
    return db


@pytest_asyncio.fixture
async def guild_repository(database):
    """테스트용 GuildRepository."""

    return GuildRepository(database=database)


@pytest_asyncio.fixture
async def member_repository(database):
    """테스트용 MemberRepository."""

    return MemberRepository(database=database)


@pytest_asyncio.fixture
async def member_service(member_repository):
    """테스트용 MemberService."""

    return MemberService(repository=member_repository)


@pytest_asyncio.fixture(autouse=True)
async def guild_settings_rows(guild_repository):
    """members의 외래키 제약을 만족하기 위한 기본 guild_settings 행."""

    for guild_id in TEST_GUILD_IDS:
        await guild_repository.create_settings(
            guild_id=guild_id,
            timezone_name="Asia/Seoul",
            attendance_days="MON,TUE,WED,THU,FRI",
            attendance_start="20:00",
            late_deadline="20:15",
            close_deadline="20:30",
            excuse_mode="officer_approval",
            officer_role_id="999",
            attendance_channel_id="1",
            announcement_channel_id="2",
            created_at="2026-01-01T00:00:00+00:00",
        )
