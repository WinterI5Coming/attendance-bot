"""디스코드 근태관리봇의 실행 진입점."""

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from bot.cogs.attendance import AttendanceCog
from bot.cogs.evaluations import EvaluationsCog
from bot.cogs.excuses import ExcusesCog
from bot.cogs.members import MembersCog
from bot.cogs.reports import ReportsCog
from bot.cogs.settings import SettingsCog
from bot.cogs.setup import SetupCog
from bot.config import load_settings
from bot.db.database import Database
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.evaluation_repository import EvaluationRepository
from bot.repositories.excuse_repository import ExcuseRepository
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.report_repository import ReportRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.scheduler.attendance_loop import AttendanceScheduler
from bot.scheduler.backup_loop import BackupScheduler
from bot.services.admin_service import AdminService
from bot.services.attendance_service import AttendanceService
from bot.services.backup_service import BackupService
from bot.services.evaluation_service import EvaluationService
from bot.services.excuse_service import ExcuseService
from bot.services.guild_service import GuildService
from bot.services.member_service import MemberService
from bot.services.report_service import ReportService
from bot.services.session_service import SessionService
from bot.services.streak_service import StreakService


# .env 파일의 환경변수를 읽는다.
settings = load_settings()


# 로그 출력 형식을 설정한다.
logging.basicConfig(
    level=getattr(
        logging,
        settings.log_level,
        logging.INFO,
    ),
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger(__name__)


# 데이터베이스 객체를 생성한다.
database = Database(settings.db_path)


# Repository는 DB에 직접 접근한다.
guild_repository = GuildRepository(
    database=database,
)


# Service는 서버 설정 관련 규칙을 처리한다.
guild_service = GuildService(
    repository=guild_repository,
    settings=settings,
)


# Repository는 DB에 직접 접근한다.
member_repository = MemberRepository(
    database=database,
)


# Service는 대원 등록/제외/조회 관련 규칙을 처리한다.
member_service = MemberService(
    repository=member_repository,
)


session_repository = SessionRepository(
    database=database,
)


attendance_repository = AttendanceRepository(
    database=database,
)


score_repository = ScoreRepository(
    database=database,
)


audit_repository = AuditRepository(
    database=database,
)


report_repository = ReportRepository(
    database=database,
)


excuse_repository = ExcuseRepository(
    database=database,
)


evaluation_repository = EvaluationRepository(
    database=database,
)


streak_service = StreakService(
    score_repository=score_repository,
)


session_service = SessionService(
    guild_repository=guild_repository,
    member_repository=member_repository,
    session_repository=session_repository,
    attendance_repository=attendance_repository,
    score_repository=score_repository,
    excuse_repository=excuse_repository,
)


attendance_service = AttendanceService(
    member_repository=member_repository,
    session_repository=session_repository,
    attendance_repository=attendance_repository,
    score_repository=score_repository,
    session_service=session_service,
    guild_repository=guild_repository,
    audit_repository=audit_repository,
    excuse_repository=excuse_repository,
    streak_service=streak_service,
)


excuse_service = ExcuseService(
    guild_repository=guild_repository,
    member_repository=member_repository,
    session_repository=session_repository,
    attendance_repository=attendance_repository,
    score_repository=score_repository,
    excuse_repository=excuse_repository,
    audit_repository=audit_repository,
)


report_service = ReportService(
    guild_repository=guild_repository,
    member_repository=member_repository,
    report_repository=report_repository,
    score_repository=score_repository,
    streak_service=streak_service,
    evaluation_repository=evaluation_repository,
)


evaluation_service = EvaluationService(
    member_repository=member_repository,
    score_repository=score_repository,
    evaluation_repository=evaluation_repository,
    audit_repository=audit_repository,
)


admin_service = AdminService(
    guild_repository=guild_repository,
    session_repository=session_repository,
    score_repository=score_repository,
    audit_repository=audit_repository,
)


attendance_scheduler = AttendanceScheduler(
    guild_service=guild_service,
    session_service=session_service,
)


backup_service = BackupService(
    database=database,
)


backup_scheduler = BackupScheduler(
    backup_service=backup_service,
)


class AttendanceBot(commands.Bot):
    """근태관리봇 Discord 클라이언트."""

    def __init__(self) -> None:
        """기본 Discord Intent로 봇 객체를 초기화한다."""

        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self) -> None:
        """DB, Cog, 슬래시 명령어를 순서대로 준비한다."""

        # 1. DB 파일과 테이블을 준비한다.
        await database.initialize()

        logger.info(
            "데이터베이스 초기화 완료: %s",
            settings.db_path,
        )

        # 2. /초기설정 명령어가 들어 있는 Cog를 봇에 등록한다.
        await self.add_cog(
            SetupCog(
                guild_service=guild_service,
            )
        )

        # 2-1. 대원 등록/제외/조회 명령어가 들어 있는 Cog를 봇에 등록한다.
        await self.add_cog(
            MembersCog(
                guild_service=guild_service,
                member_service=member_service,
            )
        )

        # 2-2. 출석/출석현황 명령어가 들어 있는 Cog를 봇에 등록한다.
        await self.add_cog(
            AttendanceCog(
                attendance_service=attendance_service,
                guild_service=guild_service,
            )
        )

        await self.add_cog(
            ReportsCog(
                report_service=report_service,
            )
        )

        await self.add_cog(
            ExcusesCog(
                excuse_service=excuse_service,
                guild_service=guild_service,
            )
        )

        await self.add_cog(
            EvaluationsCog(
                evaluation_service=evaluation_service,
                guild_service=guild_service,
            )
        )

        await self.add_cog(
            SettingsCog(
                admin_service=admin_service,
                guild_service=guild_service,
            )
        )

        # 3. 개발 서버에 슬래시 명령어를 동기화한다.
        development_guild = discord.Object(
            id=settings.development_guild_id,
        )

        self.tree.copy_global_to(
            guild=development_guild,
        )

        synced_commands = await self.tree.sync(
            guild=development_guild,
        )

        logger.info(
            "개발 서버에 슬래시 명령어 %d개를 동기화했습니다.",
            len(synced_commands),
        )

        attendance_scheduler.bot = self
        await attendance_scheduler.recover_overdue_sessions(
            datetime.now(timezone.utc)
        )
        attendance_scheduler.start()
        backup_scheduler.start()


bot = AttendanceBot()


@bot.event
async def on_ready() -> None:
    """봇이 Discord 연결을 완료했을 때 실행된다."""

    if bot.user is None:
        logger.warning(
            "봇 사용자 정보를 확인하지 못했습니다."
        )
        return

    logger.info(
        "봇 연결 완료: %s (%s)",
        bot.user,
        bot.user.id,
    )


@bot.tree.command(
    name="핑",
    description="봇의 연결 상태와 응답 속도를 확인합니다.",
)
async def ping(
    interaction: discord.Interaction,
) -> None:
    """현재 봇 연결 상태를 사용자에게 반환한다.

    Args:
        interaction:
            `/핑`을 실행한 Discord 상호작용 객체.
    """

    latency_ms = round(
        bot.latency * 1000
    )

    await interaction.response.send_message(
        f"🏓 정상 작동 중입니다. 응답 속도: {latency_ms}ms",
        ephemeral=True,
    )


def main() -> None:
    """환경변수에 저장된 Discord Token으로 봇을 실행한다."""

    bot.run(
        settings.discord_token,
    )


if __name__ == "__main__":
    main()
