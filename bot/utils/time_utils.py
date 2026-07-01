"""Timezone and attendance window utility functions."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


HHMM_PATTERN = re.compile(r"^\d{2}:\d{2}$")
WEEKDAY_CODES = (
    "MON",
    "TUE",
    "WED",
    "THU",
    "FRI",
    "SAT",
    "SUN",
)


@dataclass(frozen=True)
class SessionWindow:
    """UTC attendance session window.

    Attributes:
        start_at: Time when on-time attendance opens, converted to UTC.
        late_at: Time when late attendance begins, converted to UTC.
        close_at: Time when attendance closes, converted to UTC.

    All fields are timezone-aware datetimes with ``timezone.utc``. The project
    stores absolute times as UTC ISO 8601 strings, so callers can serialize
    these values with ``isoformat()`` before writing them to SQLite.
    """

    start_at: datetime
    late_at: datetime
    close_at: datetime


def parse_hhmm(value: str) -> time:
    """Parse a strict ``HH:MM`` time string.

    Args:
        value: Time string with exactly two hour digits, a colon, and two
            minute digits.

    Returns:
        A ``datetime.time`` value.

    Raises:
        ValueError: If the input is not a string in strict HH:MM format or if
            the hour/minute values are outside the valid 24-hour clock range.
    """

    if not HHMM_PATTERN.fullmatch(value):
        raise ValueError(f"Time must use HH:MM format: {value!r}")

    hour_text, minute_text = value.split(":")
    hour = int(hour_text)
    minute = int(minute_text)

    try:
        return time(hour=hour, minute=minute)
    except ValueError as exc:
        raise ValueError(f"Invalid HH:MM time: {value!r}") from exc


def build_session_window(
    *,
    attendance_date: date,
    attendance_start: str,
    late_deadline: str,
    close_deadline: str,
    timezone_name: str,
) -> SessionWindow:
    """Build a UTC attendance window from guild-local settings.

    Args:
        attendance_date: Local guild date for the attendance session.
        attendance_start: Strict HH:MM local start time.
        late_deadline: Strict HH:MM local late threshold.
        close_deadline: Strict HH:MM local close threshold.
        timezone_name: IANA timezone name, such as ``Asia/Seoul``.

    Returns:
        A ``SessionWindow`` whose three datetimes are timezone-aware UTC
        instants.

    Raises:
        ValueError: If any time string is invalid, if ``timezone_name`` is not
            available, or if the local times do not satisfy
            attendance_start < late_deadline < close_deadline. Sessions that
            cross midnight are intentionally rejected in this phase.
    """

    start_time = parse_hhmm(attendance_start)
    late_time = parse_hhmm(late_deadline)
    close_time = parse_hhmm(close_deadline)

    if not start_time < late_time < close_time:
        raise ValueError(
            "Attendance times must satisfy attendance_start < late_deadline "
            "< close_deadline."
        )

    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone_name!r}") from exc

    local_start = datetime.combine(
        attendance_date,
        start_time,
        tzinfo=local_timezone,
    )
    local_late = datetime.combine(
        attendance_date,
        late_time,
        tzinfo=local_timezone,
    )
    local_close = datetime.combine(
        attendance_date,
        close_time,
        tzinfo=local_timezone,
    )

    # Convert guild-local boundaries to UTC before storage so every absolute
    # timestamp has one canonical timezone-aware representation.
    return SessionWindow(
        start_at=local_start.astimezone(timezone.utc),
        late_at=local_late.astimezone(timezone.utc),
        close_at=local_close.astimezone(timezone.utc),
    )


def get_server_today(now: datetime, timezone_name: str) -> date:
    """Return the date for ``now`` in a guild's configured timezone.

    Args:
        now: Current absolute time. It must be timezone-aware, usually UTC.
        timezone_name: IANA timezone name stored in ``guild_settings``.

    Returns:
        The guild-local calendar date.

    Raises:
        ValueError: If ``now`` is naive or if the timezone cannot be loaded.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime.")

    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone_name!r}") from exc

    return now.astimezone(local_timezone).date()


def get_weekday_code(value: date) -> str:
    """Return the attendance weekday code for a date.

    Args:
        value: Date to convert.

    Returns:
        One of MON, TUE, WED, THU, FRI, SAT, or SUN.
    """

    return WEEKDAY_CODES[value.weekday()]


def parse_attendance_days(value: str) -> set[str]:
    """Parse a comma-separated attendance day setting.

    Args:
        value: Comma-separated weekday codes from ``guild_settings``.

    Returns:
        A set of normalized weekday codes.
    """

    return {
        day.strip().upper()
        for day in value.split(",")
        if day.strip()
    }


def format_local_hhmm(value: datetime | None, timezone_name: str) -> str | None:
    """Format a UTC datetime for display in the guild timezone.

    Args:
        value: Timezone-aware datetime to display, or ``None``.
        timezone_name: IANA timezone name stored in ``guild_settings``.

    Returns:
        ``HH:MM`` in the guild timezone, or ``None`` when ``value`` is ``None``.

    Raises:
        ValueError: If ``value`` is naive or the timezone cannot be loaded.
    """

    if value is None:
        return None

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("value must be a timezone-aware datetime.")

    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone_name!r}") from exc

    return value.astimezone(local_timezone).strftime("%H:%M")
