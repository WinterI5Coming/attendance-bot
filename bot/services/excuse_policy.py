"""Excuse deadline policy calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bot.utils.time_utils import parse_hhmm


EXCUSE_TYPE_LABELS = {
    "ABSENCE": "결석",
    "LATE": "지각",
    "EARLY_LEAVE": "조퇴",
}


@dataclass(frozen=True)
class ExcusePolicy:
    """Per-guild excuse request policy."""

    timezone_name: str = "Asia/Seoul"
    deadline_time: str = "23:00"
    deadline_days_before: int = 1
    require_admin_approval: bool = True
    allow_late_request: bool = False


class ExcusePolicyService:
    """Calculate excuse request deadlines with explicit timezones."""

    def from_settings(self, settings: dict) -> ExcusePolicy:
        """Build a policy object from a guild_settings row."""

        return ExcusePolicy(
            timezone_name=settings.get("timezone") or "Asia/Seoul",
            deadline_time=settings.get("excuse_deadline_time") or "23:00",
            deadline_days_before=int(settings.get("excuse_deadline_days_before") or 1),
            require_admin_approval=bool(settings.get("require_excuse_approval", 1)),
            allow_late_request=bool(settings.get("allow_late_excuse", 0)),
        )

    def calculate_deadline(self, target_date: date, policy: ExcusePolicy) -> datetime:
        """Return the timezone-aware local deadline for a target attendance date."""

        timezone = ZoneInfo(policy.timezone_name)
        deadline_time = parse_hhmm(policy.deadline_time)
        deadline_date = target_date - timedelta(days=policy.deadline_days_before)
        return datetime.combine(deadline_date, deadline_time, tzinfo=timezone)

    def can_submit(
        self,
        *,
        now: datetime,
        target_date: date,
        policy: ExcusePolicy,
    ) -> tuple[bool, datetime]:
        """Return whether `now` is before the calculated deadline."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime.")
        deadline = self.calculate_deadline(target_date, policy)
        return now.astimezone(deadline.tzinfo) < deadline, deadline
