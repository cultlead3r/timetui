"""Unit tests for the data layer (parsing, durations, mutation arg-builders)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from timetui import timew
from timetui.models import (
    Interval,
    expense_amount,
    format_cost_tag,
    format_duration,
    format_hours_decimal,
    format_timew_utc,
    parse_timew_utc,
    split_billing,
)

SAMPLE = [
    {
        "id": 2,
        "start": "20260309T210000Z",
        "end": "20260309T223000Z",
        "tags": ["LA", "paid"],
        "annotation": "crypto work",
    },
    # Active interval: no "end", no "annotation".
    {"id": 1, "start": "20260310T180000Z", "tags": ["b"]},
]


# --------------------------------------------------------------------------- #
# Parsing / formatting
# --------------------------------------------------------------------------- #
def test_parse_timew_utc_is_tz_aware():
    dt = parse_timew_utc("20260309T213157Z")
    assert dt == datetime(2026, 3, 9, 21, 31, 57, tzinfo=timezone.utc)
    assert dt.tzinfo is not None


def test_timew_utc_roundtrip():
    assert format_timew_utc(parse_timew_utc("20260309T213157Z")) == "20260309T213157Z"


@pytest.mark.parametrize(
    "td,expected",
    [
        (timedelta(hours=1, minutes=24), "1h 24m"),
        (timedelta(minutes=37), "37m"),
        (timedelta(0), "0m"),
        (timedelta(hours=2, minutes=5), "2h 05m"),
    ],
)
def test_format_duration(td, expected):
    assert format_duration(td) == expected


def test_format_hours_decimal():
    assert format_hours_decimal(timedelta(hours=12, minutes=30)) == "12.50h"
    assert format_hours_decimal(timedelta(minutes=90)) == "1.50h"


# --------------------------------------------------------------------------- #
# Interval model
# --------------------------------------------------------------------------- #
def test_from_export_completed():
    iv = Interval.from_export(SAMPLE[0])
    assert iv.id == 2
    assert iv.tags == ["LA", "paid"]
    assert iv.annotation == "crypto work"
    assert not iv.is_active
    assert iv.duration() == timedelta(hours=1, minutes=30)
    assert iv.duration_hours() == pytest.approx(1.5)


def test_from_export_active_without_end_or_annotation():
    iv = Interval.from_export(SAMPLE[1])
    assert iv.is_active
    assert iv.end is None
    assert iv.annotation == ""
    assert iv.tags == ["b"]
    now = datetime(2026, 3, 10, 19, 0, 0, tzinfo=timezone.utc)
    assert iv.duration(now=now) == timedelta(hours=1)


def test_searchable_text_contains_tags_and_annotation():
    text = Interval.from_export(SAMPLE[0]).searchable_text().lower()
    assert "la" in text
    assert "paid" in text
    assert "crypto" in text


def test_searchable_text_marks_active():
    assert "active" in Interval.from_export(SAMPLE[1]).searchable_text()


# --------------------------------------------------------------------------- #
# Expenses (pure helpers: cost: tag parsing + billing split)
# --------------------------------------------------------------------------- #
def test_expense_amount_parses_cost_tags():
    assert expense_amount(["LA", "expense", "cost:450"]) == 450.0
    assert expense_amount(["cost:450.00"]) == 450.0
    # Hand-typed tags may carry a $ and thousands separators.
    assert expense_amount(["cost:$1,250.50"]) == 1250.5


def test_expense_amount_none_without_valid_cost():
    assert expense_amount(["LA", "expense"]) is None  # marker alone isn't a cost
    assert expense_amount([]) is None
    # Malformed / non-positive amounts degrade to "not an expense", never raise.
    assert expense_amount(["cost:abc"]) is None
    assert expense_amount(["cost:0"]) is None
    assert expense_amount(["cost:-5"]) is None
    # The first parseable cost: tag wins over a malformed one.
    assert expense_amount(["cost:abc", "cost:12.34"]) == 12.34


def test_format_cost_tag_roundtrips_canonically():
    assert format_cost_tag(450) == "cost:450.00"
    # No $ / thousands separators: the tag never needs shell quoting.
    assert format_cost_tag(1250.5) == "cost:1250.50"
    assert expense_amount([format_cost_tag(99.9)]) == pytest.approx(99.9)


def test_split_billing_separates_time_from_expenses():
    work = Interval.from_export(SAMPLE[0])  # 1h30m completed
    flight = Interval.from_export(
        {"id": 3, "start": "20260310T080000Z", "end": "20260310T080100Z",
         "tags": ["LA", "expense", "cost:450.00", "new"], "annotation": "Flight"}
    )
    time_total, expenses = split_billing([work, flight])
    # The expense's synthetic minute never bills as time; its cost is fixed.
    assert time_total == timedelta(hours=1, minutes=30)
    assert expenses == 450.0


def test_split_billing_active_interval_measured_to_now():
    active = Interval.from_export(SAMPLE[1])
    now = datetime(2026, 3, 10, 19, 0, 0, tzinfo=timezone.utc)
    time_total, expenses = split_billing([active], now)
    assert time_total == timedelta(hours=1)
    assert expenses == 0.0


# --------------------------------------------------------------------------- #
# load_intervals (execute monkeypatched -> no real timew)
# --------------------------------------------------------------------------- #
def test_load_intervals(monkeypatch):
    monkeypatch.setattr(
        timew, "execute", lambda args, confirm=False: json.dumps(SAMPLE)
    )
    intervals = timew.load_intervals()
    assert [i.id for i in intervals] == [2, 1]
    assert intervals[1].is_active


def test_load_intervals_empty(monkeypatch):
    monkeypatch.setattr(timew, "execute", lambda args, confirm=False: "")
    assert timew.load_intervals() == []


# --------------------------------------------------------------------------- #
# Mutation argument builders (pure; never execute)
# --------------------------------------------------------------------------- #
def test_ref():
    assert timew.ref(42) == "@42"


def test_args_annotate():
    assert timew.args_annotate(5, "fixed bug") == ["annotate", "@5", "fixed bug"]


def test_args_tag_untag():
    assert timew.args_tag(3, ["LA", "paid"]) == ["tag", "@3", "LA", "paid"]
    assert timew.args_untag(3, ["paid"]) == ["untag", "@3", "paid"]


def test_args_modify_uses_local_iso():
    # Naive datetime -> astimezone() keeps wall-clock time in local tz (deterministic).
    dt = datetime(2026, 3, 9, 14, 31, 57)
    assert timew.args_modify_start(7, dt) == [
        "modify",
        "start",
        "@7",
        "2026-03-09T14:31:57",
    ]
    assert timew.args_modify_end(7, dt)[:3] == ["modify", "end", "@7"]


def test_args_delete_start_stop_undo():
    assert timew.args_delete(9) == ["delete", "@9"]
    assert timew.args_start(["LA", "paid"]) == ["start", "LA", "paid"]
    assert timew.args_stop() == ["stop"]
    assert timew.args_undo() == ["undo"]
    assert timew.args_continue(7) == ["continue", "@7"]


def test_refs_and_many_builders():
    assert timew.refs([1, 3, 5]) == ["@1", "@3", "@5"]
    assert timew.args_tag_many([1, 3], ["LA", "paid"]) == [
        "tag", "@1", "@3", "LA", "paid",
    ]
    assert timew.args_untag_many([2, 4], ["paid"]) == ["untag", "@2", "@4", "paid"]
    assert timew.args_delete_many([1, 2, 3]) == ["delete", "@1", "@2", "@3"]


def test_args_track_range():
    start = datetime(2026, 3, 9, 14, 0, 0)
    end = datetime(2026, 3, 9, 15, 30, 0)
    assert timew.args_track(start, end, ["LA"]) == [
        "track",
        "2026-03-09T14:00:00",
        "-",
        "2026-03-09T15:30:00",
        "LA",
    ]


# --------------------------------------------------------------------------- #
# Database directory / subprocess environment (pure helpers)
# --------------------------------------------------------------------------- #
def test_resolve_timew_db_precedence():
    # CLI flag wins over config; config is the fallback; empty/None -> inherit.
    assert timew.resolve_timew_db("/cli/db", "/cfg/db") == "/cli/db"
    assert timew.resolve_timew_db(None, "/cfg/db") == "/cfg/db"
    assert timew.resolve_timew_db("", "/cfg/db") == "/cfg/db"  # empty flag falls through
    assert timew.resolve_timew_db(None, None) is None


def test_build_env_none_inherits():
    # db=None -> None so subprocess inherits the parent env (incl. TIMEWARRIORDB).
    assert timew.build_env(None, {"PATH": "/bin"}) is None


def test_build_env_sets_timewarriordb_without_mutating_base():
    base = {"PATH": "/bin", "TIMEWARRIORDB": "/old"}
    env = timew.build_env("/data/tw", base)
    assert env == {"PATH": "/bin", "TIMEWARRIORDB": "/data/tw"}  # overrides inherited
    assert base == {"PATH": "/bin", "TIMEWARRIORDB": "/old"}  # base untouched


def test_build_env_expands_user():
    env = timew.build_env("~/tw", {})
    assert env is not None
    assert env["TIMEWARRIORDB"] == str(Path("~/tw").expanduser())


def test_ensure_db_dir_none_is_noop():
    # Inherit-the-env case must not touch the filesystem (no exception, no dir).
    assert timew.ensure_db_dir(None) is None


def test_ensure_db_dir_creates_missing_nested_dir(tmp_path):
    target = tmp_path / "nested" / "timewarrior"
    assert not target.exists()
    timew.ensure_db_dir(str(target))
    assert target.is_dir()  # parents=True created the whole chain


def test_ensure_db_dir_is_idempotent(tmp_path):
    target = tmp_path / "tw"
    timew.ensure_db_dir(str(target))
    timew.ensure_db_dir(str(target))  # exist_ok -> no error on a second call
    assert target.is_dir()


# --------------------------------------------------------------------------- #
# execute() wiring (subprocess.run monkeypatched -> no real timew)
# --------------------------------------------------------------------------- #
def _fake_run_recorder(captured: dict):
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    return fake_run


def test_execute_passes_timewarriordb_env(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(timew.subprocess, "run", _fake_run_recorder(captured))
    monkeypatch.setattr(timew, "TIMEW_DB", "/tmp/twdb")
    timew.execute(["export"], confirm=False)
    assert captured["cmd"] == ["timew", "export"]
    assert captured["kwargs"]["env"]["TIMEWARRIORDB"] == "/tmp/twdb"


def test_execute_inherits_env_when_db_unset(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(timew.subprocess, "run", _fake_run_recorder(captured))
    monkeypatch.setattr(timew, "TIMEW_DB", None)
    timew.execute(["export"], confirm=False)
    assert captured["kwargs"]["env"] is None  # inherit the parent environment
