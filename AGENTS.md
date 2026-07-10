# AGENTS.md

Textual TUI for Time Warrior (`timew`). Python >=3.11, managed with `uv`, src-layout
(`src/timetui/`). Entry point: `timetui.app:main`. See `README.md` for keybindings and
the tag-vs-text filtering semantics.

## Commands
- Run app: `uv run timetui`
- All tests: `uv run pytest`
- Single test: `uv run pytest tests/test_timew.py::test_args_track_range`
- Update UI snapshots after *intentional* UI changes: `uv run pytest --snapshot-update`
- Textual devtools / hot reload: `uv run textual run --dev timetui.app:TimewApp`

No linter or type-checker is configured (the `.ruff_cache` line in `.gitignore` is
incidental — ruff is not a dependency). Don't add one unprompted.

## Critical: tests must never touch the real `timew` database
Invoking `timew` mutates the user's real time-tracking data. Every test isolates it:
- Data tests monkeypatch `timew.execute` / `timew.load_intervals` (`tests/test_timew.py`).
- Snapshot tests monkeypatch `timew.load_intervals` with fixed fixtures (`tests/test_app.py`).

Never add a test that shells out to real `timew`.

## Data layer (`timew.py`, `models.py`)
- `args_*` functions are **pure** — they return the exact `timew` argv and are unit-tested
  without executing anything. When adding a new `timew` command, add an `args_*` builder
  plus a pure test; only `execute()` / `load_intervals()` actually shell out.
- `execute(confirm=True)` pipes `"yes\n"` to stdin so timew's destructive prompts never
  hang the non-interactive TUI (our own UI gates dangerous actions).
- The Time Warrior db dir is set via `TIMEWARRIORDB`: `execute` passes `env=build_env(TIMEW_DB,
  os.environ)`. `TIMEW_DB` (module global) is resolved once in `app.main` from the
  `--timew-dir` flag and `report.load_timew_db()` (config `[timew] db_path`) via the pure
  `resolve_timew_db` (CLI > config > inherited env). Keep `resolve_timew_db`/`build_env` pure
  (test in `tests/test_timew.py`); `None` means "inherit env unchanged". Tests clear
  `TIMEWARRIORDB` (autouse `conftest` fixture) so it can't leak.
- `app.main` also calls the impure `ensure_db_dir(TIMEW_DB)` (mkdir parents/exist_ok) before
  launching: timew prompts `Create new config in DIR?` for a *missing* db dir, which hangs the
  non-interactive `export` (no piped stdin); creating the dir first makes timew initialize it
  silently. No-op when `TIMEW_DB is None`. Test it only against `tmp_path` (never invoke timew).

## Report layer (`report.py`)
- Same pure/impure split as `timew.py`: `render_report_html()` is **pure** (builds the
  invoice HTML from `Interval`s, local time, oldest-first, html-escaped) and is unit-tested
  directly; only `write_report()` touches disk / shells out. Add report logic to the pure
  function and keep its test in `tests/test_report.py`.
- PDF is rendered by headless Chromium via Playwright (`_html_to_pdf`, launched with
  `channel="chromium-headless-shell"`). The `playwright` package is a dependency; the slim
  headless-shell browser is **auto-downloaded on first export**, one-time ~90 MB, unless
  `TIMETUI_NO_BROWSER_DOWNLOAD` is set: the app shows a `DownloadScreen` progress bar driven by
  `install_chromium(progress=...)` (which streams `playwright install`); `_launch_chromium` →
  `_install_chromium` is a silent lazy fallback for non-UI callers. A missing package or a failed
  download raises `ReportError`; `chromium_available()` is a filesystem marker check (no launch).
  Tests must never launch *or download* a browser: force the lazy `playwright.sync_api` import to
  fail (`monkeypatch.setitem(sys.modules, "playwright.sync_api", None)`) and keep new logic in the
  pure seams (`_chromium_install_argv`, `_is_missing_browser_error`, `_parse_download_progress`).
  The cyberpunk PDF frame is the invoice's own `.invoice` border (an in-flow box), **not** a
  fixed overlay — Chromium paints the invoice background over fixed elements when printing.

## Invoice ledger (`invoices.py`)
- Same pure/impure split: the `Invoice`/`Payment` model, `dumps`/`loads`, the ID scheme
  (`derive_client`, `next_invoice_id` — `{Client}-{year}-{seq:03d}`, max+1 never count+1)
  and `resolve_ledger_dir` are pure (`tests/test_invoices.py`); only `ledger_path` /
  `load_ledger` / `save_ledger` touch env/disk. `save_ledger` writes temp-then-`os.replace`.
- The ledger (`invoices.json`) lives **next to the timew db**. An autouse `conftest` fixture
  monkeypatches `invoices.ledger_path` to a temp path — the report dialog reads the ledger on
  every `R` press, so without it tests would read the developer's real ledger. Keep it.
- Invoice IDs double as timew tags on the covered intervals. Recording an invoice
  (`app._record_invoice`) issues two tag-only timew calls (tag `invoiced`+ID, untag `new`);
  tag ops never reorder intervals so @ids stay valid between the calls. Ledger save failures
  abort *before* the retag so tags never claim an unrecorded invoice.
- Interval tags mirror the lifecycle `new -> invoiced -> paid` (all three are `WORKFLOW_TAGS`,
  excluded from client derivation). Crossing the paid boundary is detected by the pure
  `paid_transitions(before, after)` and applied by `app._sync_paid_tags`, which finds intervals
  **by the invoice-ID tag** (never stale @ids) and swaps `invoiced`<->`paid` per invoice (two
  atomic tag-only calls). Same ordering rule: ledger save first, retag after.
- The report-dialog snapshot embeds a suggested ID containing the current year — tests pin
  `app._today` (module-level helper) for determinism; don't call `date.today()` directly in
  `app.py`.

## Datetime conventions (easy to get wrong)
- `timew export` emits UTC basic-ISO (`YYYYMMDDThhmmssZ`); parse/format with
  `parse_timew_utc` / `format_timew_utc`. `Interval.start`/`end` are tz-aware UTC.
- `args_*` builders pass **local naive** ISO via `fmt_dt` — timew reads a naive timestamp
  as local time. Do not pass UTC strings to mutation commands.

## App quirks (`app.py`)
- Time Warrior **renumbers `@id`s** on every change, so ids are not stable. The multi-select
  set (`self.selected`) and cursor-restore-after-reload are keyed by interval **`start`
  datetime**, not id. Preserve this when editing reload/selection logic.
- Edit actions operate on the multi-selection if one exists, else the cursor row
  (`_targets()`); multi-interval edits use the `args_*_many` builders, issued as one atomic
  `timew` call.

## Snapshot tests
- Uses `pytest-textual-snapshot`; snapshots live in `tests/__snapshots__/test_app/*.raw`.
- `SnapApp` sets `SHOW_CLOCK = False` and fixtures use only completed intervals, so
  durations/totals are deterministic. Keep new snapshot fixtures clock-free.
