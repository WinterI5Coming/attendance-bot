"""출석 대상 대원의 등록, 제외, 조회 규칙을 담당한다."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import aiosqlite

from bot.repositories.member_repository import MemberRepository


logger = logging.getLogger(__name__)

MIN_DEACTIVATION_REASON_LENGTH = 2
MAX_DEACTIVATION_REASON_LENGTH = 200


class BotRegistrationError(ValueError):
    """봇 계정을 대원으로 등록하려 할 때 발생하는 도메인 오류."""


class InvalidDeactivationReasonError(ValueError):
    """제외 사유가 길이 제약(2자 이상 200자 이하)을 만족하지 않을 때 발생한다."""


class MemberRegistrationOutcome(Enum):
    """`/대원등록` 처리 결과의 종류."""

    CREATED = "created"
    REACTIVATED = "reactivated"
    ALREADY_ACTIVE = "already_active"


class MemberDeactivationOutcome(Enum):
    """`/대원제외` 처리 결과의 종류."""

    DEACTIVATED = "deactivated"
    NOT_FOUND = "not_found"
    ALREADY_INACTIVE = "already_inactive"


@dataclass(frozen=True)
class MemberRegistrationResult:
    """대원 등록 처리 결과."""

    outcome: MemberRegistrationOutcome
    member_id: int


@dataclass(frozen=True)
class MemberDeactivationResult:
    """대원 제외 처리 결과."""

    outcome: MemberDeactivationOutcome


class MemberService:
    """출석 대상 대원의 등록, 제외, 조회 규칙을 담당한다."""

    def __init__(self, repository: MemberRepository) -> None:
        """Service 의존성을 초기화한다.

        Args:
            repository:
                members 테이블 접근을 담당하는 Repository.
        """

        self.repository = repository

    async def register_member(
        self,
        *,
        guild_id: int,
        discord_id: int,
        display_name: str,
        created_by_discord_id: int,
        is_bot: bool,
    ) -> MemberRegistrationResult:
        """대상 사용자를 활성 대원으로 등록하거나 재활성화한다.

        처음 등록하는 사용자는 새 행을 생성하고, 과거에 제외된
        사용자는 기존 행을 재사용해 재활성화한다. 이미 활성 대원인
        경우 DB를 변경하지 않는다.

        Args:
            guild_id:
                Discord 서버 ID.
            discord_id:
                등록할 대상 사용자의 Discord ID.
            display_name:
                등록 시점의 최신 Discord 표시 이름.
            created_by_discord_id:
                등록 명령을 실행한 사용자의 Discord ID.
            is_bot:
                대상 사용자가 Discord 봇 계정인지 여부.

        Returns:
            처리 결과와 대상 members 행의 id.

        Raises:
            BotRegistrationError:
                대상 사용자가 봇 계정인 경우.
        """

        if is_bot:
            raise BotRegistrationError(
                "봇 계정은 출석 대원으로 등록할 수 없습니다."
            )

        guild_id_text = str(guild_id)
        discord_id_text = str(discord_id)
        now = datetime.now(timezone.utc).isoformat()

        existing = await self.repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=discord_id_text,
        )

        if existing is None:
            try:
                member_id = await self.repository.create(
                    guild_id=guild_id_text,
                    discord_id=discord_id_text,
                    display_name=display_name,
                    created_by_discord_id=str(created_by_discord_id),
                    now=now,
                )
            except aiosqlite.IntegrityError:
                # 동시에 같은 사용자를 등록하는 요청이 들어와 다른
                # 요청이 먼저 행을 만든 경우. UNIQUE(guild_id,
                # (guild_id, discord_id) 제약으로 INSERT가 실패하므로, 현재
                # 행을 다시 조회해 그 상태에 맞는 결과를 반환한다.
                existing_after_race = await self.repository.get_by_discord_id(
                    guild_id=guild_id_text,
                    discord_id=discord_id_text,
                )

                assert existing_after_race is not None

                if existing_after_race["is_active"]:
                    return MemberRegistrationResult(
                        outcome=MemberRegistrationOutcome.ALREADY_ACTIVE,
                        member_id=existing_after_race["id"],
                    )

                member_id = await self.repository.reactivate(
                    guild_id=guild_id_text,
                    discord_id=discord_id_text,
                    display_name=display_name,
                    now=now,
                )

                return MemberRegistrationResult(
                    outcome=MemberRegistrationOutcome.REACTIVATED,
                    member_id=member_id,
                )

            return MemberRegistrationResult(
                outcome=MemberRegistrationOutcome.CREATED,
                member_id=member_id,
            )

        if existing["is_active"]:
            return MemberRegistrationResult(
                outcome=MemberRegistrationOutcome.ALREADY_ACTIVE,
                member_id=existing["id"],
            )

        member_id = await self.repository.reactivate(
            guild_id=guild_id_text,
            discord_id=discord_id_text,
            display_name=display_name,
            now=now,
        )

        return MemberRegistrationResult(
            outcome=MemberRegistrationOutcome.REACTIVATED,
            member_id=member_id,
        )

    async def deactivate_member(
        self,
        *,
        guild_id: int,
        discord_id: int,
        display_name: str,
        reason: str,
        actor_discord_id: int,
    ) -> MemberDeactivationResult:
        """대상 사용자를 이후 출석 대상에서 제외한다.

        members 행을 물리 삭제하지 않고 `is_active`만 0으로 바꾼다.
        `audit_logs` 테이블이 아직 없으므로 처리 결과를 애플리케이션
        로그에 남긴다.

        Args:
            guild_id:
                Discord 서버 ID.
            discord_id:
                제외할 대상 사용자의 Discord ID.
            display_name:
                제외 시점의 최신 Discord 표시 이름.
            reason:
                제외 사유 원문(공백 포함 가능).
            actor_discord_id:
                제외 명령을 실행한 사용자의 Discord ID.

        Returns:
            처리 결과.

        Raises:
            InvalidDeactivationReasonError:
                공백 제거 후 사유가 2자 미만이거나 200자를 초과하는 경우.
        """

        cleaned_reason = reason.strip()

        if len(cleaned_reason) < MIN_DEACTIVATION_REASON_LENGTH:
            raise InvalidDeactivationReasonError(
                "제외 사유를 두 글자 이상 입력해주세요."
            )

        if len(cleaned_reason) > MAX_DEACTIVATION_REASON_LENGTH:
            raise InvalidDeactivationReasonError(
                "제외 사유는 200자 이하로 입력해주세요."
            )

        guild_id_text = str(guild_id)
        discord_id_text = str(discord_id)

        existing = await self.repository.get_by_discord_id(
            guild_id=guild_id_text,
            discord_id=discord_id_text,
        )

        if existing is None:
            logger.info(
                "대원 제외 시도(미등록 사용자): "
                "guild_id=%s actor_id=%s target_id=%s",
                guild_id,
                actor_discord_id,
                discord_id,
            )
            return MemberDeactivationResult(
                outcome=MemberDeactivationOutcome.NOT_FOUND,
            )

        if not existing["is_active"]:
            return MemberDeactivationResult(
                outcome=MemberDeactivationOutcome.ALREADY_INACTIVE,
            )

        now = datetime.now(timezone.utc).isoformat()

        await self.repository.deactivate(
            guild_id=guild_id_text,
            discord_id=discord_id_text,
            display_name=display_name,
            now=now,
        )

        # 감사 로그(audit_logs) 테이블이 구현되기 전까지 감사에 필요한 최소
        # 정보(작업자, 대상자, 서버, 사유)를 애플리케이션 로그로 남긴다.
        logger.info(
            "대원 제외 처리 완료: "
            "guild_id=%s actor_id=%s target_id=%s reason=%s",
            guild_id,
            actor_discord_id,
            discord_id,
            cleaned_reason,
        )

        return MemberDeactivationResult(
            outcome=MemberDeactivationOutcome.DEACTIVATED,
        )

    async def list_active_members(
        self,
        *,
        guild_id: int,
    ) -> list[dict[str, Any]]:
        """현재 서버의 활성 대원 목록을 조회한다.

        Args:
            guild_id:
                Discord 서버 ID.

        Returns:
            `display_name` 오름차순으로 정렬된 활성 대원 목록.
        """

        return await self.repository.list_active(
            guild_id=str(guild_id),
        )
