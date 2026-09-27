"""봇 실행에 필요한 저장소, 서비스, 스케줄러, Cog를 조립한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord.ext import commands

from bot.cogs.attendance import AttendanceCog
from bot.cogs.excuses import ExcusesCog
from bot.cogs.help import HelpCog
from bot.cogs.members import MembersCog
from bot.cogs.reports import ReportsCog
from bot.cogs.scores import ScoresCog
from bot.cogs.settings import SettingsCog
from bot.cogs.voice_tracking import VoiceTrackingCog
from bot.config import Settings
from bot.db.database import Database
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
from bot.runtime.time_provider import TimeProvider
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
from bot.services.voice_verification_service import VoiceVerificationService
from bot.ui.views.attendance import CheckInView
from bot.ui.views.excuses import ExcuseFlow, ExcuseNoticeView

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotContainer:
    """
    Discord 클라이언트를 만들 때 필요한 최상위 구성요소 묶음.

    개별 저장소와 서비스는 함수 안에서 조립하고, 봇 클라이언트가 직접
    알아야 하는 데이터베이스, 스케줄러, Cog 목록만 외부로 노출한다.
    """

    database: Database
    attendance_scheduler: AttendanceScheduler
    backup_scheduler: BackupScheduler
    time_provider: TimeProvider
    cogs: list[commands.Cog]
    persistent_views: list[discord.ui.View]


@dataclass(frozen=True)
class RepositorySet:
    """애플리케이션 서비스가 공유하는 데이터 접근 객체 묶음."""

    guild: GuildRepository
    member: MemberRepository
    session: SessionRepository
    attendance: AttendanceRepository
    score: ScoreRepository
    audit: AuditRepository
    report: ReportRepository
    excuse: ExcuseRepository
    evaluation: EvaluationRepository
    stage_a: StageARepository


@dataclass(frozen=True)
class ServiceSet:
    """Discord Cog와 스케줄러가 호출하는 애플리케이션 서비스 묶음."""

    guild: GuildService
    member: MemberService
    session: SessionService
    attendance: AttendanceService
    report: ReportService
    excuse: ExcuseService
    evaluation: EvaluationService
    admin: AdminService
    voice_verification: VoiceVerificationService


def create_bot_container(settings: Settings) -> BotContainer:
    """
    설정을 기준으로 봇 실행에 필요한 최상위 구성요소를 생성한다.

    Args:
        settings: 검증된 실행 환경 설정.

    Returns:
        Discord 클라이언트 생성에 필요한 데이터베이스, 스케줄러, Cog 목록.
    """

    database = Database(settings.db_path)
    time_provider = TimeProvider()
    repositories = create_repositories(database)
    services = create_services(settings=settings, repositories=repositories)
    check_in_view = CheckInView(
        attendance_service=services.attendance,
        time_provider=time_provider,
    )
    excuse_flow = ExcuseFlow(
        excuse_service=services.excuse,
        guild_service=services.guild,
        time_provider=time_provider,
    )
    attendance_scheduler, backup_scheduler = create_schedulers(
        settings=settings,
        database=database,
        services=services,
        time_provider=time_provider,
        check_in_view=check_in_view,
    )
    cogs = create_cogs(
        settings=settings,
        services=services,
        time_provider=time_provider,
        excuse_flow=excuse_flow,
    )

    return BotContainer(
        database=database,
        attendance_scheduler=attendance_scheduler,
        backup_scheduler=backup_scheduler,
        time_provider=time_provider,
        cogs=cogs,
        persistent_views=[check_in_view, ExcuseNoticeView(excuse_flow)],
    )


def create_repositories(database: Database) -> RepositorySet:
    """
    같은 데이터베이스 연결 설정을 공유하는 저장소 객체를 생성한다.

    Args:
        database: SQLite 연결과 마이그레이션을 관리하는 데이터베이스 래퍼.

    Returns:
        기능별 저장소 인스턴스 묶음.
    """

    return RepositorySet(
        guild=GuildRepository(database=database),
        member=MemberRepository(database=database),
        session=SessionRepository(database=database),
        attendance=AttendanceRepository(database=database),
        score=ScoreRepository(database=database),
        audit=AuditRepository(database=database),
        report=ReportRepository(database=database),
        excuse=ExcuseRepository(database=database),
        evaluation=EvaluationRepository(database=database),
        stage_a=StageARepository(database=database),
    )


def create_services(*, settings: Settings, repositories: RepositorySet) -> ServiceSet:
    """
    저장소와 정책 객체를 조합해 애플리케이션 서비스 계층을 만든다.

    Args:
        settings: 전역 실행 설정.
        repositories: 기능별 저장소 객체 묶음.

    Returns:
        Discord Cog와 스케줄러가 사용할 서비스 인스턴스 묶음.
    """

    guild_service = GuildService(
        repository=repositories.guild,
        settings=settings,
    )
    member_service = MemberService(repository=repositories.member)
    streak_service = StreakService(score_repository=repositories.score)

    session_service = SessionService(
        guild_repository=repositories.guild,
        member_repository=repositories.member,
        session_repository=repositories.session,
        attendance_repository=repositories.attendance,
        score_repository=repositories.score,
        excuse_repository=repositories.excuse,
        stage_a_repository=repositories.stage_a,
    )

    voice_verification_service = VoiceVerificationService(
        guild_repository=repositories.guild,
        member_repository=repositories.member,
        session_repository=repositories.session,
        attendance_repository=repositories.attendance,
        score_repository=repositories.score,
        stage_a_repository=repositories.stage_a,
    )

    attendance_service = AttendanceService(
        member_repository=repositories.member,
        session_repository=repositories.session,
        attendance_repository=repositories.attendance,
        score_repository=repositories.score,
        session_service=session_service,
        guild_repository=repositories.guild,
        audit_repository=repositories.audit,
        excuse_repository=repositories.excuse,
        streak_service=streak_service,
        voice_verification_service=voice_verification_service,
    )

    excuse_service = ExcuseService(
        guild_repository=repositories.guild,
        member_repository=repositories.member,
        session_repository=repositories.session,
        attendance_repository=repositories.attendance,
        score_repository=repositories.score,
        excuse_repository=repositories.excuse,
        audit_repository=repositories.audit,
    )

    report_service = ReportService(
        guild_repository=repositories.guild,
        member_repository=repositories.member,
        report_repository=repositories.report,
        score_repository=repositories.score,
        streak_service=streak_service,
        evaluation_repository=repositories.evaluation,
    )

    evaluation_service = EvaluationService(
        member_repository=repositories.member,
        score_repository=repositories.score,
        evaluation_repository=repositories.evaluation,
        audit_repository=repositories.audit,
    )

    admin_service = AdminService(
        guild_repository=repositories.guild,
        session_repository=repositories.session,
        score_repository=repositories.score,
        audit_repository=repositories.audit,
    )

    return ServiceSet(
        guild=guild_service,
        member=member_service,
        session=session_service,
        attendance=attendance_service,
        report=report_service,
        excuse=excuse_service,
        evaluation=evaluation_service,
        admin=admin_service,
        voice_verification=voice_verification_service,
    )


def create_schedulers(
    *,
    settings: Settings,
    database: Database,
    services: ServiceSet,
    time_provider: TimeProvider,
    check_in_view: CheckInView,
) -> tuple[AttendanceScheduler, BackupScheduler]:
    """
    Discord 봇과 함께 동작하는 백그라운드 스케줄러를 생성한다.

    Args:
        settings: 백업 경로를 계산하는 데 필요한 실행 설정.
        database: 백업 서비스가 사용할 데이터베이스 래퍼.
        services: 스케줄러가 호출할 애플리케이션 서비스 묶음.
        time_provider: 스케줄러가 사용할 현재 시각 공급자.

    Returns:
        출석 스케줄러와 백업 스케줄러.
    """

    attendance_scheduler = AttendanceScheduler(
        guild_service=services.guild,
        session_service=services.session,
        voice_verification_service=services.voice_verification,
        time_provider=time_provider,
        check_in_view=check_in_view,
    )
    backup_scheduler = BackupScheduler(
        backup_service=BackupService(
            database=database,
            backup_directory=settings.db_path.parent / "backups",
        ),
        time_provider=time_provider,
    )
    return attendance_scheduler, backup_scheduler


def create_cogs(
    *,
    settings: Settings,
    services: ServiceSet,
    time_provider: TimeProvider,
    excuse_flow: ExcuseFlow,
) -> list[commands.Cog]:
    """
    기능별 Discord Cog를 생성한다.

    Args:
        settings: 실행 환경 설정.
        services: Cog가 호출할 애플리케이션 서비스 묶음.
        time_provider: 모든 Cog가 공유하는 현재 시각 공급자.

    Returns:
        Discord 클라이언트에 등록할 Cog 목록.
    """

    return [
        HelpCog(),
        SettingsCog(
            admin_service=services.admin,
            guild_service=services.guild,
            time_provider=time_provider,
        ),
        MembersCog(
            guild_service=services.guild,
            member_service=services.member,
        ),
        AttendanceCog(
            attendance_service=services.attendance,
            guild_service=services.guild,
            admin_service=services.admin,
            time_provider=time_provider,
        ),
        ReportsCog(report_service=services.report, time_provider=time_provider),
        ExcusesCog(
            excuse_service=services.excuse,
            guild_service=services.guild,
            excuse_flow=excuse_flow,
            time_provider=time_provider,
        ),
        ScoresCog(
            evaluation_service=services.evaluation,
            guild_service=services.guild,
            time_provider=time_provider,
        ),
        VoiceTrackingCog(
            voice_verification_service=services.voice_verification,
            time_provider=time_provider,
        ),
    ]
