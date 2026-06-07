"""Data model for Time Warrior intervals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Time Warrior exports timestamps as UTC in ISO 8601 "basic" form, e.g. 20260309T213157Z
TIMEW_FMT = "%Y%m%dT%H%M%SZ"


def parse_timew_utc(value: str) -> datetime:
    """Parse a Time Warrior UTC timestamp into a timezone-aware datetime."""
    return datetime.strptime(value, TIMEW_FMT).replace(tzinfo=timezone.utc)


def format_timew_utc(dt: datetime) -> str:
    """Format a datetime back into Time Warrior's UTC basic-ISO form."""
    return dt.astimezone(timezone.utc).strftime(TIMEW_FMT)


def format_duration(td: timedelta) -> str:
    """Human duration like '1h 24m' or '37m' (or '0m')."""
    total = int(td.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours:
        return f"{sign}{hours}h {minutes:02d}m"
    return f"{sign}{minutes}m"


def format_hours_decimal(td: timedelta) -> str:
    """Decimal hours for billing, like '12.50h'."""
    return f"{td.total_seconds() / 3600:.2f}h"


@dataclass
class Interval:
    """A single Time Warrior interval."""

    id: int
    start: datetime  # tz-aware, UTC
    end: datetime | None  # tz-aware UTC, or None when the interval is active
    tags: list[str]
    annotation: str

    @classmethod
    def from_export(cls, raw: dict) -> "Interval":
        """Build an Interval from one `timew export` JSON object."""
        end = raw.get("end")
        return cls(
            id=int(raw["id"]),
            start=parse_timew_utc(raw["start"]),
            end=parse_timew_utc(end) if end else None,
            tags=list(raw.get("tags", []) or []),
            annotation=(raw.get("annotation") or ""),
        )

    @property
    def is_active(self) -> bool:
        return self.end is None

    @property
    def start_local(self) -> datetime:
        return self.start.astimezone()

    @property
    def end_local(self) -> datetime | None:
        return self.end.astimezone() if self.end is not None else None

    def duration(self, now: datetime | None = None) -> timedelta:
        """Duration; for active intervals measured up to `now` (default: real now)."""
        end = self.end if self.end is not None else (now or datetime.now(timezone.utc))
        return end - self.start

    def duration_hours(self, now: datetime | None = None) -> float:
        return self.duration(now).total_seconds() / 3600.0

    @property
    def tags_display(self) -> str:
        return ", ".join(self.tags)

    def searchable_text(self, now: datetime | None = None) -> str:
        """Combined text used for fuzzy matching: tags, annotation, date, time, duration."""
        local = self.start_local
        parts = [
            " ".join(self.tags),
            self.annotation,
            local.strftime("%Y-%m-%d %a %b %d"),  # 2026-03-09 Mon Mar 09
            local.strftime("%H:%M"),
            format_duration(self.duration(now)),
        ]
        if self.is_active:
            parts.append("active")
        return " ".join(p for p in parts if p)
