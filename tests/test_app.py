"""Snapshot tests for the TUI, driven by fixed fixture data (no real timew)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from textual.widgets import (
    Checkbox,
    DataTable,
    Input,
    RadioButton,
    SelectionList,
    Static,
)

from timetui import invoices, report, timew
from timetui import app as app_module
from timetui.app import TAGS_GAP, TAGS_MAX, TimewApp
from timetui.invoices import Invoice, Payment
from timetui.models import Interval
from timetui.screens import (
    ColumnsScreen,
    InvoicesScreen,
    PaymentScreen,
    ReportScreen,
    TextReportScreen,
    VimRadioSet,
)

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
    # Pin "today" so the report dialog's suggested invoice ID (…-{year}-001)
    # stays deterministic in snapshots regardless of the real date.
    monkeypatch.setattr(app_module, "_today", lambda: date(2026, 3, 13))
    return fixtures


@pytest.fixture
def rated_intervals(monkeypatch, fixed_intervals):
    """Fixed intervals plus a configured $200/h rate (no real config file read)."""
    monkeypatch.setattr(
        report,
        "load_brand_config",
        lambda *a, **k: report.BrandConfig(rate=200.0, currency="USD"),
    )
    return fixed_intervals


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


# --------------------------------------------------------------------------- #
# Hourly rate -> live dollar amounts (status Σ + sidebar tag-set breakdown)
# --------------------------------------------------------------------------- #
def test_snapshot_rate_amounts(snap_compare, rated_intervals):
    # With an hourly rate configured, the sidebar shows a $ figure per tag-set and
    # the status bar shows the grand-total $ next to the Σ summation.
    assert snap_compare(SnapApp(), terminal_size=(120, 30))


def test_rate_shows_dollar_amounts(rated_intervals):
    """A configured rate adds $ totals to the status Σ and the tag-set breakdown."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            status = str(app.query_one("#status", Static).content)
            breakdown = str(app.query_one("#breakdown", Static).content)
            # All 8 fixtures total 6.5h; at $200/h that is $1,300.00 next to Σ.
            assert "$1,300.00" in status
            # The LA + paid tag-set is 3.5h -> $700.00 in the sidebar.
            assert "$700.00" in breakdown

    asyncio.run(scenario())


def test_rate_selection_shows_selected_amount(rated_intervals):
    """Selecting rows shows the selection's $ total next to the selected Σ."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("space")  # select the newest row (15m -> 0.25h)
            await pilot.pause()
            status = str(app.query_one("#status", Static).content)
            assert "selected" in status
            assert "$50.00" in status  # 0.25h × $200/h

    asyncio.run(scenario())


def test_no_rate_means_no_dollar_amounts(fixed_intervals):
    """Without a configured rate (the default), no $ amounts appear anywhere."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app.brand.rate == 0.0
            assert "$" not in str(app.query_one("#status", Static).content)
            assert "$" not in str(app.query_one("#breakdown", Static).content)

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# Invoice ledger (record from the report dialog, browse/pay with I)
# --------------------------------------------------------------------------- #
@pytest.fixture
def ledger_fixture():
    """A fixed ledger (paid / partial / unpaid) written to the isolated path."""
    ledger = [
        Invoice(id="LA-2026-001", date="2026-01-15", hours=6.5, rate=200.0,
                amount=1300.0,
                payments=[Payment(date="2026-01-20", amount=500.0,
                                  note="wire ref 123")]),
        Invoice(id="B-2026-001", date="2026-02-01", hours=2.0, rate=150.0,
                amount=300.0,
                payments=[Payment(date="2026-02-10", amount=300.0)]),
        Invoice(id="LA-2026-002", date="2026-03-01", hours=4.0, rate=200.0,
                amount=800.0),
    ]
    invoices.save_ledger(invoices.ledger_path(), ledger)
    return ledger


def test_snapshot_invoices_screen(snap_compare, fixed_intervals, ledger_fixture):
    # 'I' opens the ledger: per-invoice amount/paid/balance/status, Σ summary,
    # and the highlighted invoice's payment history below.
    assert snap_compare(SnapApp(), terminal_size=(120, 35), press=["I"])


def test_report_dialog_suggests_client_invoice_id(rated_intervals):
    """The R dialog pre-fills {Client}-{year}-{seq} from the targets' shared tag."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Narrow to the LA+new intervals -> unambiguous client 'LA'.
            await pilot.press("slash", *list("la new"), "enter")
            await pilot.pause()
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            inp = app.screen.query_one("#invoice-input", Input)
            assert inp.value == "LA-2026-001"  # _today pinned to 2026-03-13
            assert inp.disabled  # editable only once "Record invoice" is checked

    asyncio.run(scenario())


def test_report_records_invoice_and_retags(rated_intervals, tmp_path, monkeypatch):
    """Recording an invoice saves the ledger snapshot and retags the intervals:
    one atomic tag (invoiced + the invoice ID), one atomic untag of 'new'."""
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[list[str]] = []
    monkeypatch.setattr(
        timew, "execute", lambda args, **kw: calls.append(list(args)) or ""
    )

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("slash", *list("la new"), "enter")
            await pilot.pause()
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            screen = app.screen
            screen.query_one("#open-checkbox", Checkbox).value = False
            screen.query_one("#invoice-checkbox", Checkbox).value = True
            await pilot.pause()
            assert not screen.query_one("#invoice-input", Input).disabled
            screen.action_save()
            for _ in range(40):
                await pilot.pause(0.05)
                if len(calls) >= 2:
                    break
            # targets oldest-first: @8 (Mar 8) then @7 (Mar 11), both LA+new
            assert calls[0] == ["tag", "@8", "@7", "invoiced", "LA-2026-001"]
            assert calls[1] == ["untag", "@8", "@7", "new"]
            ledger = invoices.load_ledger(invoices.ledger_path())
            assert [inv.id for inv in ledger] == ["LA-2026-001"]
            assert ledger[0].date == "2026-03-13"  # pinned _today
            assert ledger[0].hours == pytest.approx(1.25)  # 45m + 30m
            assert ledger[0].rate == 200.0
            assert ledger[0].amount == pytest.approx(250.0)
            assert ledger[0].status == "unpaid"
            assert (tmp_path / "timetui-report.html").exists()

    asyncio.run(scenario())


def test_backfill_invoice_from_already_invoiced_intervals(tmp_path, monkeypatch):
    """Recording works for intervals already tagged `invoiced` (backfilling an
    invoice sent outside the normal workflow): the client is still derived (the
    `invoiced` workflow tag is ignored), re-tagging `invoiced` is a harmless
    timew no-op (verified against a sandbox db), and with no `new` tag present
    only ONE timew call is issued."""
    raw = [
        {"id": 2, "start": "20260308T090000Z", "end": "20260308T100000Z",
         "tags": ["LA", "invoiced"], "annotation": "already billed work"},
        {"id": 1, "start": "20260311T120000Z", "end": "20260311T123000Z",
         "tags": ["LA", "invoiced"], "annotation": "more billed work"},
    ]
    monkeypatch.setattr(
        timew, "load_intervals", lambda: [Interval.from_export(r) for r in raw]
    )
    monkeypatch.setattr(app_module, "_today", lambda: date(2026, 3, 13))
    monkeypatch.setattr(
        report,
        "load_brand_config",
        lambda *a, **k: report.BrandConfig(rate=200.0, currency="USD"),
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[list[str]] = []
    monkeypatch.setattr(
        timew, "execute", lambda args, **kw: calls.append(list(args)) or ""
    )

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            screen = app.screen
            # `invoiced` is a workflow tag -> the client 'LA' is still derived
            assert screen.query_one("#invoice-input", Input).value == "LA-2026-001"
            screen.query_one("#open-checkbox", Checkbox).value = False
            screen.query_one("#invoice-checkbox", Checkbox).value = True
            await pilot.pause()
            screen.action_save()
            for _ in range(40):
                await pilot.pause(0.05)
                if calls:
                    break
            # one atomic tag call only — nothing carries `new`, so no untag
            assert calls == [["tag", "@2", "@1", "invoiced", "LA-2026-001"]]
            ledger = invoices.load_ledger(invoices.ledger_path())
            assert [inv.id for inv in ledger] == ["LA-2026-001"]
            assert ledger[0].hours == pytest.approx(1.5)  # 1h + 30m
            assert ledger[0].amount == pytest.approx(300.0)

    asyncio.run(scenario())


def test_invoice_payment_flow_persists(fixed_intervals):
    """I -> p records a partial payment; closing the screen saves the ledger."""
    path = invoices.ledger_path()
    invoices.save_ledger(
        path,
        [Invoice(id="LA-2026-001", date="2026-01-15", hours=5.0, rate=200.0,
                 amount=1000.0)],
    )

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("I")
            for _ in range(40):
                await pilot.pause(0.05)
                if isinstance(app.screen, InvoicesScreen):
                    break
            assert isinstance(app.screen, InvoicesScreen)
            await pilot.press("p")
            for _ in range(40):
                await pilot.pause(0.05)
                if isinstance(app.screen, PaymentScreen):
                    break
            assert isinstance(app.screen, PaymentScreen)
            # amount pre-fills with the full balance; pay only part of it
            assert app.screen.query_one("#amount-input", Input).value == "1000.00"
            app.screen.query_one("#amount-input", Input).value = "400"
            app.screen.query_one("#note-input", Input).value = "wire ref 9"
            app.screen.action_save()
            for _ in range(40):
                await pilot.pause(0.05)
                if isinstance(app.screen, InvoicesScreen):
                    break
            # regression: the refreshed columns must fit the new, wider cells
            # ('$0.00' -> '$400.00', 'unpaid' -> 'partial') — stale auto widths
            # used to truncate them.
            table = app.screen.query_one("#invoices-table", DataTable)
            widths = {
                str(col.label): col.width for col in table.columns.values()
            }
            assert widths["Paid"] >= len("$400.00")
            assert widths["Status"] >= len("partial")
            await pilot.press("q")  # close -> the app saves the changed ledger
            for _ in range(40):
                await pilot.pause(0.05)
                saved = invoices.load_ledger(path)
                if saved and saved[0].payments:
                    break
            saved = invoices.load_ledger(path)
            assert saved[0].paid == 400.0
            assert saved[0].balance == 600.0
            assert saved[0].status == "partial"
            assert saved[0].payments[0].note == "wire ref 9"

    asyncio.run(scenario())


def test_report_dialog_rejects_taken_invoice_id(rated_intervals, ledger_fixture):
    """An invoice ID already in the ledger is rejected (never reused)."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            screen = app.screen
            screen.query_one("#invoice-checkbox", Checkbox).value = True
            await pilot.pause()
            screen.query_one("#invoice-input", Input).value = "LA-2026-001"
            screen.action_save()
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)  # not dismissed
            err = str(screen.query_one("#error", Static).content)
            assert "already exists" in err

    asyncio.run(scenario())


def test_report_dialog_prefills_rate_from_config(rated_intervals):
    """The configured rate pre-fills the report dialog's Hourly rate input."""

    async def scenario() -> None:
        app = SnapApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            assert app.screen.query_one("#rate-input", Input).value == "200"

    asyncio.run(scenario())
