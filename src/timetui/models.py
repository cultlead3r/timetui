"""Data model for Time Warrior intervals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

# Time Warrior exports timestamps as UTC in ISO 8601 "basic" form, e.g. 20260309T213157Z
TIMEW_FMT = "%Y%m%dT%H%M%SZ"

# Expenses (a flight, a hotel night, ...) are stored as tiny 1-minute intervals
# — timew rejects zero-length ranges — carrying a marker tag plus the amount
# encoded in a `cost:` tag (e.g. `cost:450.00`). The cost tag is the authority:
# anything with a parseable `cost:` amount bills as a fixed amount, never as
# time; `expense` is the human-facing marker used for filtering and defaults.
EXPENSE_TAG = "expense"
COST_PREFIX = "cost:"


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


def billing_amount(td: timedelta, rate: float) -> float:
    """Money owed for a duration at an hourly ``rate`` (decimal hours × rate)."""
    return td.total_seconds() / 3600.0 * rate


def format_amount(amount: float) -> str:
    """Format a billing amount as dollars, like '$1,234.00' (thousands-separated)."""
    return f"${amount:,.2f}"


def expense_amount(tags: Iterable[str]) -> float | None:
    """The fixed amount encoded in a ``cost:`` tag, or ``None`` (pure: unit-tested).

    ``None`` means "not an expense": no ``cost:`` tag, or one whose value is not
    a positive number (a malformed cost degrades the interval to plain time, it
    never crashes). The first parseable ``cost:`` tag wins. Accepts a leading
    ``$`` and thousands separators (``cost:$1,250.00``).
    """
    for tag in tags:
        if not tag.startswith(COST_PREFIX):
            continue
        raw = tag[len(COST_PREFIX):].lstrip("$").replace(",", "")
        try:
            amount = float(raw)
        except ValueError:
            continue
        if amount > 0:
            return amount
    return None


def format_cost_tag(amount: float) -> str:
    """Canonical ``cost:`` tag for an amount, like ``cost:450.00`` (pure inverse
    of :func:`expense_amount` — no ``$``/thousands separators, so the tag never
    needs quoting on the timew command line)."""
    return f"{COST_PREFIX}{amount:.2f}"


def split_billing(
    intervals: Sequence["Interval"], now: datetime | None = None
) -> tuple[timedelta, float]:
    """Split intervals into billable time and fixed expenses (pure: unit-tested).

    Returns ``(time_total, expenses)``: the summed duration of the *non-expense*
    intervals (an expense's synthetic minute never bills as time) and the summed
    ``cost:`` amounts of the expense ones. The money owed for the set is
    ``billing_amount(time_total, rate) + expenses`` — the single seam shared by
    the report renderers, the invoice snapshot and the TUI totals.
    """
    time_total = timedelta()
    expenses = 0.0
    for iv in intervals:
        cost = expense_amount(iv.tags)
        if cost is not None:
            expenses += cost
        else:
            time_total += iv.duration(now)
    return time_total, expenses


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
