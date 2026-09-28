"""서비스 결과 → Discord 메시지/Embed 변환과 상호작용 컴포넌트 구성 검증."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.runtime.time_provider import TimeProvider
from bot.services.attendance_service import (
    AttendanceCheckInResult,
    AttendanceCheckInStatus,
    AttendanceStatusMember,
    AttendanceStatusResult,
)
from bot.services.excuse_service import ExcuseResult, ExcuseStatus
from bot.services.report_service import RankingEntry, RankingResult
from bot.services.session_service import SessionPrepareStatus
from bot.ui.attendance_messages import (
    build_check_in_message,
    build_start_announcement_embed,
    build_status_embed,
)
from bot.ui.excuse_messages import build_create_message, build_list_embed
from bot.ui.report_messages import build_ranking_embed
from bot.ui.views.attendance import CHECK_IN_CUSTOM_ID, CheckInView
from bot.ui.views.excuses import (
    EXCUSE_REVIEW_CUSTOM_ID,
    ExcuseFlow,
    ExcuseNoticeView,
    ExcuseRequestModal,
    ExcuseReviewView,
)


def _member(discord_id: str, status: str | None) -> AttendanceStatusMember:
    return AttendanceStatusMember(
        member_id=1,
        discord_id=discord_id,
        display_name=discord_id,
        attendance_record_id=None,
        attendance_status=status,
        checked_at=None,
    )


def test_check_in_message_shows_score_and_local_time():
    result = AttendanceCheckInResult(
        status=AttendanceCheckInStatus.PRESENT,
        score_delta=3,
        total_score=10,
        checked_at="2026-07-02T12:31:00+00:00",
        timezone_name="Asia/Seoul",
    )

    message = build_check_in_message(result)

    assert "정상 출석" in message
    assert "+3" in message
    assert "21:31" in message


def test_status_embed_groups_members_and_counts():
    result = AttendanceStatusResult(
        status=SessionPrepareStatus.READY,
        session={"id": 1},
        attendance_date="2026-07-02",
        present=[_member("1", "PRESENT")],
        late=[_member("2", "LATE")],
        unchecked=[_member("3", None), _member("4", None)],
    )

    embed = build_status_embed(result)

    assert "2026-07-02" in embed.title
    assert "총원 4명" in embed.description
    assert "미체크 2명" in embed.description
    assert [field.name for field in embed.fields] == ["✅ 정상 출석 (1)", "⏰ 지각 (1)", "❔ 미체크 (2)"]


def test_start_announcement_embed_uses_guild_local_times():
    session = {
        "attendance_date": "2026-07-02",
        "late_at": "2026-07-02T12:40:00+00:00",
        "close_at": "2026-07-02T12:45:00+00:00",
        "timezone": "Asia/Seoul",
    }

    embed = build_start_announcement_embed(session)

    assert [field.value for field in embed.fields] == ["21:40", "21:45"]
    assert "출석하기" in embed.description


def test_excuse_create_message_and_list_embed():
    created = ExcuseResult(
        status=ExcuseStatus.CREATED_PENDING,
        request={"target_date": "2026-07-03", "excuse_type": "LATE", "deadline_at": "x"},
    )
    assert "지각" in build_create_message(created)
    assert "승인 대기" in build_create_message(created)

    listed = ExcuseResult(
        status=ExcuseStatus.APPROVED,
        request={
            "rows": [
                {"id": 7, "target_date": "2026-07-03", "excuse_type": "ABSENCE", "status": "PENDING", "discord_id": "1"}
            ]
        },
    )
    embed = build_list_embed(listed)
    assert "#7" in embed.description
    assert "결석" in embed.description
    assert "대기" in embed.description


def test_ranking_embed_lists_entries_in_order():
    result = RankingResult(
        configured=True,
        entries=[
            RankingEntry(rank_no=1, discord_id="1", display_name="a", total_score=20, rank="불꽃 일병", current_streak=3),
            RankingEntry(rank_no=2, discord_id="2", display_name="b", total_score=-5, rank="수치의 폐급", current_streak=0),
        ],
    )

    embed = build_ranking_embed(result)

    assert embed.description.index("🥇") < embed.description.index("🥈")
    assert "+20점" in embed.description
    assert "-5점" in embed.description


def test_check_in_view_is_persistent_and_closes_to_disabled_button():
    view = CheckInView(attendance_service=MagicMock(), time_provider=TimeProvider())

    assert view.is_persistent()
    assert view.children[0].custom_id == CHECK_IN_CUSTOM_ID
    assert not view.children[0].disabled

    closed = view.closed()
    assert closed.children[0].disabled
    assert closed.children[0].custom_id == CHECK_IN_CUSTOM_ID


@pytest.mark.asyncio
async def test_check_in_button_calls_service_and_replies_ephemeral():
    service = MagicMock()
    service.check_in = AsyncMock(
        return_value=AttendanceCheckInResult(
            status=AttendanceCheckInStatus.PRESENT,
            score_delta=3,
            total_score=3,
            checked_at="2026-07-02T12:31:00+00:00",
            timezone_name="Asia/Seoul",
        )
    )
    view = CheckInView(attendance_service=service, time_provider=TimeProvider())
    interaction = MagicMock()
    interaction.guild.id = 111
    interaction.user = MagicMock(spec=discord.User)
    interaction.user.id = 42
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await view.children[0].callback(interaction)

    service.check_in.assert_awaited_once()
    assert service.check_in.await_args.kwargs["guild_id"] == 111
    assert service.check_in.await_args.kwargs["discord_id"] == 42
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    assert "정상 출석" in interaction.followup.send.await_args.args[0]


def _flow() -> ExcuseFlow:
    return ExcuseFlow(excuse_service=MagicMock(), guild_service=MagicMock(), time_provider=TimeProvider())


def test_excuse_request_modal_has_type_select_date_and_reason():
    modal = ExcuseRequestModal(_flow(), default_date="2026-07-03")

    assert isinstance(modal.excuse_type.component, discord.ui.Select)
    assert {option.value for option in modal.excuse_type.component.options} == {"ABSENCE", "LATE", "EARLY_LEAVE"}
    assert modal.target_date.default == "2026-07-03"
    assert modal.reason.max_length == 500


def test_excuse_notice_view_is_persistent():
    view = ExcuseNoticeView(_flow())

    assert view.is_persistent()
    assert view.children[0].custom_id == EXCUSE_REVIEW_CUSTOM_ID


@pytest.mark.asyncio
async def test_excuse_review_select_enables_decision_buttons():
    flow = _flow()
    row = {"id": 7, "target_date": "2026-07-03", "excuse_type": "ABSENCE", "status": "PENDING",
           "discord_id": "1", "display_name": "홍길동", "reason": "병원", "requested_at": "x"}
    flow.excuse_service.get_request = AsyncMock(return_value=row)
    view = ExcuseReviewView(flow, [row])
    assert view.approve_button.disabled and view.reject_button.disabled

    view.select._values = ["7"]
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view.select.callback(interaction)

    assert view.selected_id == 7
    assert not view.approve_button.disabled and not view.reject_button.disabled
    embed = interaction.response.edit_message.await_args.kwargs["embed"]
    assert "#7" in embed.title


@pytest.mark.asyncio
async def test_excuse_flow_approve_refreshes_pending_list():
    flow = _flow()
    flow.excuse_service.approve_request = AsyncMock(
        return_value=ExcuseResult(status=ExcuseStatus.APPROVED, score_delta=2)
    )
    flow.excuse_service.list_requests = AsyncMock(
        return_value=ExcuseResult(status=ExcuseStatus.APPROVED, request={"rows": []})
    )
    flow.can_review = AsyncMock(return_value=True)
    interaction = MagicMock()
    interaction.guild.id = 111
    interaction.user.id = 9
    interaction.response.edit_message = AsyncMock()

    await flow.approve(interaction, 7)

    assert flow.excuse_service.approve_request.await_args.kwargs["excuse_request_id"] == 7
    kwargs = interaction.response.edit_message.await_args.kwargs
    assert "#7" in kwargs["content"] and "승인" in kwargs["content"]
    assert kwargs["view"] is None
    assert isinstance(flow.time_provider.now_utc(), datetime)
    assert flow.time_provider.now_utc().tzinfo is UTC


@pytest.mark.asyncio
async def test_excuse_flow_rechecks_officer_permission_before_deciding():
    """검토 화면을 연 뒤 역할이 회수된 사용자는 승인/거절할 수 없다."""

    flow = _flow()
    flow.excuse_service.approve_request = AsyncMock()
    flow.can_review = AsyncMock(return_value=False)
    interaction = MagicMock()
    interaction.guild.id = 111
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()

    await flow.approve(interaction, 7)

    flow.excuse_service.approve_request.assert_not_awaited()
    assert "간부" in interaction.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_review_view_timeout_disables_components_and_edits_message():
    flow = _flow()
    row = {"id": 7, "target_date": "2026-07-03", "excuse_type": "ABSENCE", "discord_id": "1",
           "display_name": "홍길동"}
    view = ExcuseReviewView(flow, [row])
    view.message = MagicMock()
    view.message.edit = AsyncMock()

    await view.on_timeout()

    assert all(item.disabled for item in view.children)
    assert "만료" in view.message.edit.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_check_in_view_on_error_replies_without_raising():
    view = CheckInView(attendance_service=MagicMock(), time_provider=TimeProvider())
    interaction = MagicMock()
    interaction.response.is_done = MagicMock(return_value=True)
    interaction.followup.send = AsyncMock(side_effect=RuntimeError("expired"))

    await view.on_error(interaction, ValueError("boom"), view.children[0])

    interaction.followup.send.assert_awaited_once()
