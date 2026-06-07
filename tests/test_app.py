"""Snapshot tests for the TUI, driven by fixed fixture data (no real timew)."""

from __future__ import annotations

import asyncio

import pytest

from textual.widgets import RadioButton, SelectionList

from timetui import timew
from timetui.app import TAGS_GAP, TAGS_MAX, TimewApp
from timetui.models import Interval
from timetui.screens import ColumnsScreen, ReportScreen, TextReportScreen, VimRadioSet

# Fixed, all-completed intervals -> deterministic durations/totals for snapshots.
RAW = [
    {"id": 8, "start": "20260308T090000Z", "end": "20260308T094500Z",
     "tags": ["LA", "new"], "annotation": "deploy auth service"},
    {"id": 7, "start": "20260311T120000Z", "end": "20260311T123000Z",
     "tags": ["LA", "new"], "annotation": "ship newsletter"},
    {"id": 6, "start": "20260309T210000Z", "end": "20260309T223000Z",
     "tags": ["LA", "paid"], "annotation": "crypto work"},
    {"id": 5, "start": "20260310T180000Z", "end": "20260310T190000Z",
     "tags": ["LA", "paid"], "annotation": "mailerlite integration"},
    {"id": 4, "start": "20260311T020000Z", "end": "20260311T030000Z",
     "tags": ["b"], "annotation": "build pipeline"},
    {"id": 3, "start": "20260312T050000Z", "end": "20260312T053000Z",
     "tags": ["new"], "annotation": "add favicon"},
    {"id": 2, "start": "20260312T060000Z", "end": "20260312T070000Z",
     "tags": ["LA", "paid"], "annotation": "mailerlite sync finished"},
    {"id": 1, "start": "20260313T160000Z", "end": "20260313T161500Z",
     "tags": ["LA"], "annotation": "image regression"},
]


class SnapApp(TimewApp):
    SHOW_CLOCK = False  # keep snapshots deterministic


@pytest.fixture
def fixed_intervals(monkeypatch):
    fixtures = [Interval.from_export(r) for r in RAW]
    monkeypatch.setattr(timew, "load_intervals", lambda: list(fixtures))
    return fixtures


def test_snapshot_default(snap_compare, fixed_intervals):
    assert snap_compare(SnapApp(), terminal_size=(120, 30))


def test_snapshot_filtered(snap_compare, fixed_intervals):
    assert snap_compare(
        SnapApp(), terminal_size=(120, 30), press=["slash", *list("mailerlite")]
    )


def test_snapshot_and_filter(snap_compare, fixed_intervals):
    # 'la new' is tag-AND: only intervals tagged BOTH LA and new (never LA+paid);
    # the breakdown totals 'LA + new' together.
    assert snap_compare(
        SnapApp(), terminal_size=(120, 30), press=["slash", *list("la new")]
    )


def test_snapshot_tag_remove(snap_compare, fixed_intervals):
    # open the tag-removal picker on a 2-tag interval (row 1 = LA + paid)
    assert snap_compare(SnapApp(), terminal_size=(120, 30), press=["j", "T"])


def test_snapshot_wrap(snap_compare, fixed_intervals):
    # 'w' wraps annotations to multi-line rows
    assert snap_compare(SnapApp(), terminal_size=(120, 30), press=["w"])


def test_snapshot_sidebar_hidden(snap_compare, fixed_intervals):
    # 'f' hides the sidebar; the table (and annotation column) take full width
    assert snap_compare(SnapApp(), terminal_size=(120, 30), press=["f"])


def test_snapshot_selection(snap_compare, fixed_intervals):
    # space selects rows (highlighted like the cursor); status shows the count
    assert snap_compare(
        SnapApp(), terminal_size=(120, 30), press=["space", "j", "space"]
    )


def test_snapshot_report_dialog(snap_compare, fixed_intervals):
    # 'R' opens the report-generation dialog (style / format / path / open toggle).
    # With no selection it covers the whole filtered view (8 entries here).
    assert snap_compare(SnapApp(), terminal_size=(120, 30), press=["R"])


def test_snapshot_columns_dialog(snap_compare, fixed_intervals):
    # 'C' opens the show/hide-columns picker; ID starts unchecked (hidden).
    assert snap_compare(SnapApp(), terminal_size=(120, 30), press=["C"])


def test_report_dialog_vim_keys(fixed_intervals):
    """j/k navigate the style/format radio sets (then space commits the choice)."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            style = app.screen.query_one("#style-set", VimRadioSet)
            style.focus()
            await pilot.pause()
            assert str(style.pressed_button.label) == "cyberpunk"
            await pilot.press("j", "space")  # vim-down to 'printer', then commit
            await pilot.pause()
            assert str(style.pressed_button.label) == "printer"
            await pilot.press("k", "space")  # vim-up back to 'cyberpunk', commit
            await pilot.pause()
            assert str(style.pressed_button.label) == "cyberpunk"

    asyncio.run(scenario())


def test_id_column_hidden_by_default_and_toggleable(fixed_intervals):
    """ID is hidden on launch; the 'C' dialog switches it back on."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._col_keys == ["start", "dur", "tags", "annotation"]
            await pilot.press("C")
            for _ in range(40):
                await pilot.pause(0.05)
                if isinstance(app.screen, ColumnsScreen):
                    break
            assert isinstance(app.screen, ColumnsScreen)
            sl = app.screen.query_one(SelectionList)
            sl.select(sl.get_option_at_index(0).value)  # 'id' is the first option
            await pilot.pause()
            app.screen.action_confirm()
            # The rebuild is deferred (call_after_refresh) until the modal closes,
            # so wait for the ID column to actually appear.
            for _ in range(40):
                await pilot.pause(0.05)
                if app._col_keys and app._col_keys[0] == "id":
                    break
            assert app._col_keys == ["id", "start", "dur", "tags", "annotation"]
            assert len(app.table.columns) == 5

    asyncio.run(scenario())


def _expected_tags_width(app) -> int:
    longest = max((len(iv.tags_display) for iv in app.displayed if iv.tags), default=0)
    return min(max(longest, len("Tags")) + TAGS_GAP, TAGS_MAX)


def test_tags_width_fits_longest_tagset(fixed_intervals):
    """Tags fits the widest tag-set in view (+gap), so short tags don't leave a
    big gap before the annotation; it re-fits as the filter changes the view."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._tags_w == _expected_tags_width(app)
            assert app._tags_w < 24  # snug — not a fixed wide column
            # narrowing the view to a shorter tag-set re-fits the column
            await pilot.press("slash", *list("new"))
            await pilot.pause()
            assert app._tags_w == _expected_tags_width(app)

    asyncio.run(scenario())


def test_tags_width_caps_at_max(monkeypatch):
    """A very long tag-set is clamped to TAGS_MAX (and the full set is in Detail)."""
    raw = [{
        "id": 1, "start": "20260313T160000Z", "end": "20260313T161500Z",
        "tags": ["supercalifragilistic", "expialidocious", "verylongtagindeed"],
        "annotation": "x",
    }]
    monkeypatch.setattr(
        timew, "load_intervals", lambda: [Interval.from_export(r) for r in raw]
    )

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert len(app.displayed[0].tags_display) > TAGS_MAX  # sanity
            assert app._tags_w == TAGS_MAX

    asyncio.run(scenario())


def test_theme_switch_updates_palette(fixed_intervals):
    """Cell/sidebar colors follow the active theme (cyberpunk -> monokai)."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._pal["primary"].lower() == "#c77dff"  # cyberpunk purple
            app.theme = "monokai"
            for _ in range(40):
                await pilot.pause(0.02)
                if app._pal["primary"].lower() != "#c77dff":
                    break
            assert app._pal["primary"].lower() == "#ae81ff"  # monokai purple

    asyncio.run(scenario())


def test_text_report_opens_preview(fixed_intervals, tmp_path, monkeypatch):
    """Choosing the 'text' format writes a .txt and shows the in-app preview."""
    monkeypatch.setenv("HOME", str(tmp_path))

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            app.screen.query_one("#format-text", RadioButton).value = True
            await pilot.pause()
            app.screen.action_save()
            for _ in range(40):
                await pilot.pause(0.05)
                if isinstance(app.screen, TextReportScreen):
                    break
            assert isinstance(app.screen, TextReportScreen)
            content = app.screen._content
            # No user config in tests -> neutral default branding.
            assert "YourCompany" in content and "Total" in content
            assert (tmp_path / "timetui-report.txt").exists()

    asyncio.run(scenario())
