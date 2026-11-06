"""Shared pytest fixtures.

Beyond the project rule that tests never touch the real ``timew`` database, they
must also never read the developer's real ``~/.config/timetui/config.toml`` —
otherwise report output would depend on whatever branding happens to be on this
machine. This autouse fixture points the brand-config loader at a
guaranteed-missing path for every test, so it falls back to the neutral
``DEFAULT_BRAND``; tests that exercise the loader set their own
``TIMETUI_CONFIG`` / ``path`` explicitly.

It also clears ``TIMEWARRIORDB`` so a value in the developer's shell can never
leak into env-building assertions (and as a safety net, so nothing could point a
stray real ``timew`` call at the real database).

The invoice ledger gets the same isolation: ``invoices.ledger_path`` normally
resolves next to the real Time Warrior db, and the report dialog reads the
ledger on every ``R`` press — so every test gets a fresh temp ledger path.
Tests that need ledger contents write them via ``invoices.save_ledger``.

Local time is pinned to ``America/Chicago`` for the whole run: the app renders
local time, and the snapshot baselines in ``tests/__snapshots__`` were recorded
at -05:00, so on a machine in any other zone every snapshot would fail. It is
set at import (before pytest imports any test module), so module-level
fixtures see it too. Re-record snapshots under this pin, never a local zone.
"""

from __future__ import annotations

import os
import time

import pytest

from timetui import invoices

os.environ["TZ"] = "America/Chicago"
if hasattr(time, "tzset"):  # POSIX only; Windows ignores TZ for local time
    time.tzset()


@pytest.fixture(autouse=True)
def _isolate_brand_config(tmp_path_factory, monkeypatch):
    missing = tmp_path_factory.mktemp("noconfig") / "config.toml"
    monkeypatch.setenv("TIMETUI_CONFIG", str(missing))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("TIMEWARRIORDB", raising=False)


@pytest.fixture(autouse=True)
def _isolate_invoice_ledger(tmp_path_factory, monkeypatch):
    ledger = tmp_path_factory.mktemp("ledger") / invoices.LEDGER_FILENAME
    monkeypatch.setattr(invoices, "ledger_path", lambda: ledger)
    return ledger
