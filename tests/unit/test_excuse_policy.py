"""Tests for excuse deadline policy calculations."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from bot.services.excuse_policy import ExcusePolicy, ExcusePolicyService


def test_deadline_is_previous_day_2300_in_guild_timezone():
    service = ExcusePolicyService()
    policy = ExcusePolicy(
        timezone_name="Asia/Seoul",
        deadline_time="23:00",
        deadline_days_before=1,
    )

    deadline = service.calculate_deadline(date(2026, 7, 2), policy)

    assert deadline == datetime(2026, 7, 1, 23, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def test_can_submit_before_but_not_at_or_after_deadline():
    service = ExcusePolicyService()
    policy = ExcusePolicy(
        timezone_name="Asia/Seoul",
        deadline_time="23:00",
        deadline_days_before=1,
    )
    target_date = date(2026, 7, 2)

    before, _ = service.can_submit(
        now=datetime(2026, 7, 1, 13, 59, 59, tzinfo=timezone.utc),
        target_date=target_date,
        policy=policy,
    )
    exact, _ = service.can_submit(
        now=datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc),
        target_date=target_date,
        policy=policy,
    )
    after, _ = service.can_submit(
        now=datetime(2026, 7, 1, 14, 0, 1, tzinfo=timezone.utc),
        target_date=target_date,
        policy=policy,
    )

    assert before is True
    assert exact is False
    assert after is False


def test_deadline_handles_year_boundary():
    service = ExcusePolicyService()
    policy = ExcusePolicy(
        timezone_name="Asia/Seoul",
        deadline_time="23:00",
        deadline_days_before=1,
    )

    deadline = service.calculate_deadline(date(2027, 1, 1), policy)

    assert deadline == datetime(2026, 12, 31, 23, 0, tzinfo=ZoneInfo("Asia/Seoul"))
