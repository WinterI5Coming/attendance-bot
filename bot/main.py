"""Discord attendance bot application assembly."""

from datetime import datetime, timezone
import logging

import discord
from discord.ext import commands

from bot.cogs.achievements import AchievementsCog
from bot.cogs.attendance import AttendanceCog
from bot.cogs.adjustments import AdjustmentsCog
from bot.cogs.evaluations import EvaluationsCog
from bot.cogs.excuses import ExcusesCog
from bot.cogs.help import HelpCog
from bot.cogs.members import MembersCog
from bot.cogs.officer_reviews import OfficerReviewsCog
from bot.cogs.reports import ReportsCog
from bot.cogs.seasons import SeasonsCog
from bot.cogs.settings import SettingsCog
from bot.cogs.setup import SetupCog
from bot.cogs.voice_tracking import VoiceTrackingCog
from bot.config import Settings, load_settings
from bot.db.database import Database
from bot.repositories.adjustment_repository import AdjustmentRepository
from bot.repositories.attendance_repository import AttendanceRepository
from bot.repositories.audit_repository import AuditRepository
from bot.repositories.evaluation_repository import EvaluationRepository
from bot.repositories.excuse_repository import ExcuseRepository
from bot.repositories.guild_repository import GuildRepository
from bot.repositories.member_repository import MemberRepository
from bot.repositories.report_repository import ReportRepository
from bot.repositories.score_repository import ScoreRepository
from bot.repositories.session_repository import SessionRepository
from bot.repositories.stage_a_repository import StageARepository
from bot.repositories.stage_c_repository import StageCRepository
from bot.scheduler.attendance_loop import AttendanceScheduler
from bot.scheduler.backup_loop import BackupScheduler
from bot.services.adjustment_service import AdjustmentService
from bot.services.admin_service import AdminService
from bot.services.attendance_service import AttendanceService
from bot.services.backup_service import BackupService
from bot.services.evaluation_service import EvaluationService
from bot.services.excuse_service import ExcuseService
from bot.services.guild_service import GuildService
from bot.services.member_service import MemberService
from bot.services.report_service import ReportService
from bot.services.session_service import SessionService
from bot.services.stage_c_service import (
    AchievementService,
    OfficerReviewService,
    SeasonService,
)
from bot.services.streak_service import StreakService
from bot.services.voice_verification_service import VoiceVerificationService
from bot.utils.discord_messages import info_embed


logger = logging.getLogger(__name__)


class AttendanceBot(commands.Bot):
    """Discord bot client with attendance services wired into cogs."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        attendance_scheduler: AttendanceScheduler,
        backup_scheduler: BackupScheduler,
        cogs: list[commands.Cog],
    ) -> None:
        """Create the Discord client and keep runtime dependencies for shutdown."""

        intents = discord.Intents.default()
        intents.voice_states = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )
        self.settings = settings
        self.database = database
        self.attendance_scheduler = attendance_scheduler
        self.backup_scheduler = backup_scheduler
        self._configured_cogs = cogs

    async def setup_hook(self) -> None:
        """Initialize the database, register cogs, and start schedulers."""

        await self.database.initialize()
        logger.info("Database initialized: %s", self.settings.db_path)

        for cog in self._configured_cogs:
            await self.add_cog(cog)

        development_guild = discord.Object(id=self.settings.development_guild_id)
        self.tree.copy_global_to(guild=development_guild)
        synced_commands = await self.tree.sync(guild=development_guild)
        logger.info(
            "Synced %d slash commands to development guild %s.",
            len(synced_commands),
            self.settings.development_guild_id,
        )

        self.attendance_scheduler.bot = self
        await self.attendance_scheduler.recover_overdue_sessions(
            datetime.now(timezone.utc)
        )
        self.attendance_scheduler.start()
        self.backup_scheduler.start()

    async def close(self) -> None:
        """Stop background loops before closing the Discord connection."""

        logger.info("Discord bot shutdown requested.")
        self.attendance_scheduler.stop()
        self.backup_scheduler.stop()
        await super().close()
        logger.info("Discord bot closed.")


def create_bot(settings: Settings) -> AttendanceBot:
    """Build repositories, services, schedulers, cogs, and the Discord client."""

    database = Database(settings.db_path)

    guild_repository = GuildRepository(database=database)
    member_repository = MemberRepository(database=database)
    session_repository = SessionRepository(database=database)
    attendance_repository = AttendanceRepository(database=database)
    score_repository = ScoreRepository(database=database)
    audit_repository = AuditRepository(database=database)
    report_repository = ReportRepository(database=database)
    excuse_repository = ExcuseRepository(database=database)
    evaluation_repository = EvaluationRepository(database=database)
    stage_a_repository = StageARepository(database=database)
    adjustment_repository = AdjustmentRepository(database=database)
    stage_c_repository = StageCRepository(database=database)

    guild_service = GuildService(
        repository=guild_repository,
        settings=settings,
    )
    member_service = MemberService(repository=member_repository)
    streak_service = StreakService(score_repository=score_repository)

    session_service = SessionService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        excuse_repository=excuse_repository,
        stage_a_repository=stage_a_repository,
    )

    voice_verification_service = VoiceVerificationService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        score_repository=score_repository,
        stage_a_repository=stage_a_repository,
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
        voice_verification_service=voice_verification_service,
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

    adjustment_service = AdjustmentService(
        guild_repository=guild_repository,
        member_repository=member_repository,
        session_repository=session_repository,
        attendance_repository=attendance_repository,
        excuse_repository=excuse_repository,
        score_repository=score_repository,
        audit_repository=audit_repository,
        adjustment_repository=adjustment_repository,
    )

    season_service = SeasonService(
        guild_repository=guild_repository,
        repository=stage_c_repository,
    )
    achievement_service = AchievementService(
        member_repository=member_repository,
        repository=stage_c_repository,
        season_service=season_service,
    )
    officer_review_service = OfficerReviewService(
        guild_repository=guild_repository,
        repository=stage_c_repository,
        season_service=season_service,
    )

    attendance_scheduler = AttendanceScheduler(
        guild_service=guild_service,
        session_service=session_service,
        voice_verification_service=voice_verification_service,
    )
    backup_scheduler = BackupScheduler(
        backup_service=BackupService(
            database=database,
            backup_directory=settings.db_path.parent.parent / "backups",
        )
    )

    cogs: list[commands.Cog] = [
        HelpCog(),
        SetupCog(guild_service=guild_service),
        MembersCog(
            guild_service=guild_service,
            member_service=member_service,
        ),
        AttendanceCog(
            attendance_service=attendance_service,
            guild_service=guild_service,
        ),
        ReportsCog(report_service=report_service),
        ExcusesCog(
            excuse_service=excuse_service,
            guild_service=guild_service,
        ),
        EvaluationsCog(
            evaluation_service=evaluation_service,
            guild_service=guild_service,
        ),
        SettingsCog(
            admin_service=admin_service,
            guild_service=guild_service,
        ),
        AdjustmentsCog(
            adjustment_service=adjustment_service,
            guild_service=guild_service,
        ),
        VoiceTrackingCog(
            voice_verification_service=voice_verification_service,
        ),
        AchievementsCog(
            guild_service=guild_service,
            achievement_service=achievement_service,
            enable_season_awards=settings.enable_seasons,
        ),
    ]

    if settings.enable_seasons:
        cogs.extend(
            [
                SeasonsCog(
                    guild_service=guild_service,
                    season_service=season_service,
                ),
                OfficerReviewsCog(
                    guild_service=guild_service,
                    officer_review_service=officer_review_service,
                ),
            ]
        )
    else:
        logger.info("Season and officer-review commands are disabled.")

    bot = AttendanceBot(
        settings=settings,
        database=database,
        attendance_scheduler=attendance_scheduler,
        backup_scheduler=backup_scheduler,
        cogs=cogs,
    )
    register_events(bot)
    register_commands(bot)
    return bot


def register_events(bot: AttendanceBot) -> None:
    """Register lifecycle and error logging events."""

    @bot.event
    async def on_ready() -> None:
        if bot.user is None:
            logger.warning("Connected, but bot user information is unavailable.")
            return
        logger.info("Discord login complete: %s (%s)", bot.user, bot.user.id)
        print(f"Discord 연결 완료: {bot.user} ({bot.user.id})")

    @bot.event
    async def on_disconnect() -> None:
        logger.warning("Discord connection lost.")

    @bot.event
    async def on_resumed() -> None:
        logger.info("Discord connection resumed.")

    @bot.event
    async def on_command_error(
        context: commands.Context,
        error: commands.CommandError,
    ) -> None:
        logger.exception(
            "Prefix command failed: command=%s author=%s",
            getattr(context.command, "qualified_name", None),
            getattr(context.author, "id", None),
            exc_info=error,
        )

    async def on_app_command_error(
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        logger.exception(
            "Slash command failed: command=%s user=%s",
            getattr(interaction.command, "qualified_name", None),
            getattr(interaction.user, "id", None),
            exc_info=error,
        )

    bot.tree.on_error = on_app_command_error


def register_commands(bot: AttendanceBot) -> None:
    """Register simple built-in slash commands."""

    @bot.tree.command(
        name="핑",
        description="봇의 연결 상태와 응답 속도를 확인합니다.",
    )
    async def ping(interaction: discord.Interaction) -> None:
        latency_ms = round(bot.latency * 1000)
        await interaction.response.send_message(
            embed=info_embed(
                title="봇 상태",
                description="정상 작동 중입니다.",
                fields=(("응답 속도", f"{latency_ms}ms", True),),
            ),
            ephemeral=True,
        )


def run(settings: Settings) -> None:
    """Run the Discord bot with validated settings."""

    bot = create_bot(settings)
    bot.run(settings.discord_token)


def main() -> None:
    """Run from a Python development environment."""

    run(load_settings())


if __name__ == "__main__":
    main()
