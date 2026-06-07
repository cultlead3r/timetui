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
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_brand_config(tmp_path_factory, monkeypatch):
    missing = tmp_path_factory.mktemp("noconfig") / "config.toml"
    monkeypatch.setenv("TIMETUI_CONFIG", str(missing))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("TIMEWARRIORDB", raising=False)
