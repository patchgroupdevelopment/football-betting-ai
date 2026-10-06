"""Time helpers.

Storage convention: the database holds naive datetimes in UTC. Conversion to
the user's timezone (Asia/Baku by default) happens only for display.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Current time as a naive UTC datetime (the storage convention)."""
    return datetime.now(UTC).replace(tzinfo=None)


def from_timestamp(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC).replace(tzinfo=None)


def parse_iso_to_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string into naive UTC; naive input is assumed to be UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def to_local(dt_utc: datetime, tz: ZoneInfo) -> datetime:
    return dt_utc.replace(tzinfo=UTC).astimezone(tz)


def local_now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def local_today(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()


def local_day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC [start, end) of a calendar day in ``tz``, as naive datetimes."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return (
        start.astimezone(UTC).replace(tzinfo=None),
        end.astimezone(UTC).replace(tzinfo=None),
    )


def analysis_window(day: date, tz: ZoneInfo, cutoff: str) -> tuple[datetime, datetime]:
    """UTC [start, end) covered by one day's analysis: the local day plus the night until the next run.

    With the daily run at 08:00 the 5 October report also covers matches kicking
    off before 08:00 on 6 October (late MLS / South American games), which would
    otherwise fall between two reports and never be analysed.
    """
    hour, minute = parse_hhmm(cutoff)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time(hour, minute), tzinfo=tz)
    return (
        start.astimezone(UTC).replace(tzinfo=None),
        end.astimezone(UTC).replace(tzinfo=None),
    )


def parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)
