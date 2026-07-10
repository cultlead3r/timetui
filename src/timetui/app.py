"""timetui - a pretty, billing-focused Textual TUI for Time Warrior."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import textwrap
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.coordinate import Coordinate
from textual.fuzzy import FuzzySearch
from textual.theme import Theme
from textual.widgets import DataTable, Footer, Header, Input, Static

from . import invoices, report, timew
from .models import (
    Interval,
    billing_amount,
    format_amount,
    format_duration,
    format_hours_decimal,
)
from .screens import (
    ColumnsScreen,
    ConfirmScreen,
    DownloadScreen,
    HelpScreen,
    InvoicesScreen,
    NewIntervalScreen,
    ReportScreen,
    TagRemoveScreen,
    TextPromptScreen,
    TextReportScreen,
    TimeEditScreen,
)


def _today() -> date:
    """Today's local date (module-level so tests can pin it for determinism)."""
    return date.today()

# Neon palette (cyberpunk / Posting-ish)
NEON_CYAN = "#22d3ee"
NEON_GREEN = "#34ffb0"
NEON_PINK = "#ff2e97"
NEON_PURPLE = "#c77dff"
NEON_YELLOW = "#fde047"

# The table cell / sidebar text colors are resolved at render time from the
# *active* theme (see `TimewApp._refresh_palette`), so they follow theme switches
# (cyberpunk, monokai, …) instead of being pinned to the neons above. Each role
# maps 1:1 to a cyberpunk neon, so the default theme looks identical:
#   primary   = purple → Tags
#   secondary = cyan   → Start, @id, the Σ total, fuzzy-match highlight
#   accent    = pink   → selection + filter callouts
#   success   = green  → the active interval
#   warning   = yellow → ID

CYBERPUNK = Theme(
    name="cyberpunk",
    primary=NEON_PURPLE,
    secondary=NEON_CYAN,
    accent=NEON_PINK,
    foreground="#e6e6ff",
    background="#0a0a16",
    surface="#15152e",
    panel="#101026",
    success=NEON_GREEN,
    warning=NEON_YELLOW,
    error="#ff4d6d",
    dark=True,
    variables={
        "block-cursor-background": NEON_PINK,
        "block-cursor-foreground": "#0a0a16",
        "input-cursor-background": NEON_PINK,
        "input-selection-background": "#c77dff44",
        "footer-key-foreground": NEON_CYAN,
        "footer-description-foreground": "#e6e6ff",
    },
)


# Table columns. The fixed-width ones are sized here; the Annotation column has
# width=None so it keeps its full content width and scrolls horizontally (h/l).
# Columns can be hidden/shown at runtime (ID is hidden by default) via
# `action_columns`. The Tags column is sized to fit the *longest tag-set currently
# shown* (plus a small TAGS_GAP), capped at TAGS_MAX — so the annotation sits just
# past the widest tags and short tag-sets don't leave a wide gap before it.
Column = namedtuple("Column", "key label width")
COLUMNS = [
    Column("id", "ID", 5),
    Column("start", "Start", 15),
    Column("dur", "Dur", 9),
    Column("tags", "Tags", 24),
    Column("annotation", "Annotation", None),
]
COLUMN_WIDTH = {c.key: c.width for c in COLUMNS}
DEFAULT_HIDDEN = {"id"}  # columns hidden on first launch (toggle with C)
TAGS_MAX = 48  # Tags never wider than this (longer tag-sets truncate past it)
TAGS_GAP = 2  # blank cells kept between the widest tag-set and the annotation
WRAP_MAX_LINES = 4
SELECT_BG = "#6d28d9"  # prominent violet background for multi-selected rows
SELECT_FG = "#f5f3ff"  # near-white text on selected rows


class IntervalsTable(DataTable):
    """DataTable with vim h/l horizontal scrolling and resize-aware annotation width."""

    BINDINGS = [
        Binding("l", "hscroll(1)", "Scroll right", show=False),
        Binding("h", "hscroll(-1)", "Scroll left", show=False),
        # horizontal scrolling is on h/l, not the arrow keys
        Binding("left", "noop", show=False),
        Binding("right", "noop", show=False),
    ]

    def on_resize(self, event) -> None:
        handler = getattr(self.app, "_on_table_resize", None)
        if handler is not None:
            handler()

    def action_hscroll(self, direction: int) -> None:
        self.scroll_relative(x=direction * 8, animate=False)

    def action_noop(self) -> None:
        pass


class TimewApp(App):
    """Browse, fuzzy-filter and edit Time Warrior intervals."""

    CSS_PATH = Path(__file__).with_name("app.tcss")
    TITLE = "timetui"
    SUB_TITLE = ""
    SHOW_CLOCK = True  # disabled in snapshot tests for determinism

    BINDINGS = [
        Binding("slash", "search", "Search"),
        Binding("escape", "escape", "Back", show=False),
        # vim navigation (only fires when the table is focused)
        Binding("j", "cursor_move(1)", "Down", show=False),
        Binding("k", "cursor_move(-1)", "Up", show=False),
        Binding("G", "cursor_bottom", "Bottom", show=False),
        Binding("ctrl+d", "cursor_page(1)", "½ down", show=False),
        Binding("ctrl+u", "cursor_page(-1)", "½ up", show=False),
        # multi-select
        Binding("space", "toggle_select", "Select"),
        # edit actions
        Binding("a", "annotate", "Annotate"),
        Binding("t", "add_tag", "Tag"),
        Binding("T", "remove_tag", "Untag"),
        Binding("m", "modify_time", "Modify"),
        Binding("o", "new_interval", "New"),
        Binding("s", "start_tracking", "Start"),
        Binding("S", "stop_tracking", "Stop"),
        Binding("c", "continue_interval", "Continue"),
        Binding("u", "undo", "Undo"),
        Binding("R", "report", "Report"),
        Binding("I", "invoices", "Invoices"),
        # view
        Binding("w", "toggle_wrap", "Wrap"),
        Binding("f", "toggle_sidebar", "Sidebar"),
        Binding("C", "columns", "Columns"),
        Binding("r", "reload", "Reload", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.all_intervals: list[Interval] = []
        self.displayed: list[Interval] = []
        self.query: str = ""
        self.fuzzy = FuzzySearch(case_sensitive=False)
        self._pending: str | None = None  # for gg / dd key sequences
        self._all_tags: list[str] = []  # every tag seen in the data (for tag terms)
        self.wrap_annotations: bool = False  # 'w' toggles multi-line rows
        self.show_sidebar: bool = True  # 'f' toggles the right sidebar
        # columns currently shown (session-only; ID hidden by default), toggled
        # with 'C'. `_col_keys` is the ordered subset actually in the table.
        self.visible_columns: set[str] = {
            c.key for c in COLUMNS if c.key not in DEFAULT_HIDDEN
        }
        self._col_keys: list[str] = []
        self._col_key_objs: dict[str, object] = {}  # key str -> textual ColumnKey
        self._tags_w: int = COLUMN_WIDTH["tags"]  # current elastic Tags width
        # role -> hex, resolved from the active theme in `_refresh_palette`;
        # seeded with the cyberpunk defaults so panels never render with an empty
        # palette before the first `_render`.
        self._pal: dict[str, str] = {
            "primary": NEON_PURPLE, "secondary": NEON_CYAN, "accent": NEON_PINK,
            "success": NEON_GREEN, "warning": NEON_YELLOW,
        }
        self._render_ready: bool = False  # guards resize re-render until first layout
        # multi-selected intervals, keyed by their (stable) start datetime
        self.selected: set[datetime] = set()
        # User branding/billing config; the real one is loaded from disk in
        # `on_mount` (the neutral default has rate 0 -> no live dollar amounts).
        self.brand: report.BrandConfig = report.DEFAULT_BRAND

    # ----------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Header(show_clock=self.SHOW_CLOCK)
        yield Input(
            placeholder="Fuzzy filter…  tags · annotation · date · time   (press /)",
            id="search",
        )
        with Horizontal(id="body"):
            yield IntervalsTable(id="table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="sidebar"):
                yield Static(id="detail")
                yield Static(id="breakdown")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(CYBERPUNK)
        self.theme = "cyberpunk"
        # re-render so cell/sidebar colors follow theme switches (^p → Change theme)
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        self.table = self.query_one("#table", DataTable)
        # 0 inter-cell padding so a selected row's highlight fills with no gaps;
        # the annotation overflows and scrolls horizontally with h / l.
        self.table.cell_padding = 0
        self.table.show_horizontal_scrollbar = False
        self._rebuild_columns()
        search = self.query_one("#search", Input)
        search.border_title = "Filter"
        search.display = False  # hidden until activated with /
        self.query_one("#detail", Static).border_title = "Detail"
        self.query_one("#breakdown", Static).border_title = "Totals by tag-set"
        # Load branding/billing config (e.g. the hourly rate) before the first
        # render so live dollar amounts appear immediately when a rate is set.
        self.brand = report.load_brand_config()
        self.reload()
        self.table.focus()
        self.set_interval(1.0, self._tick)
        self._render_ready = True
        # render once more after first layout so the annotation width is correct
        self.call_after_refresh(self._rerender_keep_cursor)

    # ------------------------------------------------------------- data/render
    def reload(self, preserve_start: datetime | None = None) -> None:
        """Re-read intervals from timew and re-render, keeping the filter."""
        if preserve_start is None:
            current = self.current_interval()
            preserve_start = current.start if current else None
        try:
            self.all_intervals = timew.load_intervals()
        except timew.TimewError as exc:
            self.all_intervals = []
            self.notify(exc.message or str(exc), title="timew export failed",
                        severity="error", timeout=10)
        self._all_tags = sorted({t for iv in self.all_intervals for t in iv.tags})
        # drop selections whose intervals no longer exist (e.g. after delete)
        live = {iv.start for iv in self.all_intervals}
        self.selected &= live
        self._render(preserve_start)

    def _refresh_palette(self) -> None:
        """Resolve the role -> hex colors from the *active* theme.

        The raw ``current_theme`` attributes are used first (so the cyberpunk
        theme renders with its exact neons), falling back to the fully-resolved
        ``theme_variables`` (and finally the foreground) for any theme that leaves
        a role undefined. Called at the top of every ``_render`` and on theme
        change, so the table and sidebar follow theme switches.
        """
        theme = self.app.current_theme
        variables = self.app.theme_variables

        def role(name: str) -> str:
            return getattr(theme, name, None) or variables.get(name) or theme.foreground

        self._pal = {
            "primary": role("primary"),
            "secondary": role("secondary"),
            "accent": role("accent"),
            "success": role("success"),
            "warning": role("warning"),
        }

    def _on_theme_changed(self, _theme) -> None:
        """Re-render so cell/sidebar colors pick up the newly-selected theme."""
        self._rerender_keep_cursor()

    def _is_tag_term(self, term: str) -> bool:
        """True if `term` is the prefix of a real tag (so it filters by tag)."""
        tl = term.lower()
        return any(tag.lower().startswith(tl) for tag in self._all_tags)

    def _compute_displayed(
        self, now: datetime
    ) -> list[tuple[Interval, frozenset[int]]]:
        """Apply the current query. Returns (interval, annotation-offsets), newest first.

        The query is split on whitespace and treated as AND (every term must
        match). Each term is classified:
          * tag term  - it is the prefix of a known tag -> the interval must
            carry a tag starting with it (so 'LA new' means tagged LA AND new,
            and will NOT include 'LA paid').
          * text term - anything else -> fuzzy-matched against the annotation,
            date and time (so 'mailerlite' or 'mar 12' still work).
        """
        if not self.query:
            ordered = sorted(self.all_intervals, key=lambda i: i.start, reverse=True)
            return [(iv, frozenset()) for iv in ordered]

        terms = self.query.split()
        tag_terms = [t for t in terms if self._is_tag_term(t)]
        text_terms = [t for t in terms if not self._is_tag_term(t)]

        rows: list[tuple[Interval, frozenset[int]]] = []
        for iv in self.all_intervals:
            if not all(
                any(tag.lower().startswith(tt.lower()) for tag in iv.tags)
                for tt in tag_terms
            ):
                continue
            text = iv.searchable_text(now)
            if not all(self.fuzzy.match(tt, text)[0] > 0 for tt in text_terms):
                continue
            offsets: set[int] = set()
            if iv.annotation and text_terms:
                for tt in text_terms:
                    score, offs = self.fuzzy.match(tt, iv.annotation)
                    if score > 0:
                        offsets.update(offs)
            rows.append((iv, frozenset(offsets)))

        rows.sort(key=lambda r: r[0].start, reverse=True)
        return rows

    def _rebuild_columns(self) -> None:
        """(Re)create the table's columns from `self.visible_columns`, in order."""
        self.table.clear(columns=True)
        self._col_keys = []
        self._col_key_objs = {}
        for col in COLUMNS:
            if col.key not in self.visible_columns:
                continue
            if col.width is not None:
                ckey = self.table.add_column(col.label, width=col.width, key=col.key)
            else:
                ckey = self.table.add_column(col.label, key=col.key)
            self._col_keys.append(col.key)
            self._col_key_objs[col.key] = ckey

    def _render_width(self, key: str) -> int:
        """Rendered width of a fixed column (Tags is elastic; the rest are static)."""
        return self._tags_w if key == "tags" else COLUMN_WIDTH[key]

    def _dynamic_tags_width(self) -> int:
        """Fit the Tags column to the widest tag-set currently shown.

        The column is sized to the longest ``tags_display`` in view (never below
        the "Tags" header) plus a small TAGS_GAP, capped at TAGS_MAX. So the
        annotation sits just past the widest tags — long tag-sets get room while
        short ones don't leave a wide gap — and very long tag-sets truncate at the
        cap (and the full set is always in the Detail pane).
        """
        if "tags" not in self._col_keys:
            return COLUMN_WIDTH["tags"]
        longest = max(
            (len(iv.tags_display) for iv in self.displayed if iv.tags),
            default=0,
        )
        return min(max(longest, len("Tags")) + TAGS_GAP, TAGS_MAX)

    def _apply_tags_width(self) -> None:
        """Push the current elastic Tags width onto the live table column."""
        ckey = self._col_key_objs.get("tags")
        if ckey is None:
            return
        col = self.table.columns.get(ckey)
        if col is not None and col.width != self._tags_w:
            col.width = self._tags_w
            self.table._require_update_dimensions = True

    def _annotation_width(self) -> int:
        """Columns left for the annotation so the table fits (no horizontal scroll)."""
        table_w = self.table.size.width
        if table_w <= 0:
            return 12  # safe fallback before first layout; resize re-renders
        # only the visible fixed-width columns take space (Tags is elastic); each
        # visible column carries cell_padding on both sides; leave a couple of
        # columns for the vertical scrollbar / safety.
        fixed = sum(self._render_width(k) for k in self._col_keys if k != "annotation")
        overhead = fixed + self.table.cell_padding * 2 * len(self._col_keys) + 3
        return max(12, table_w - overhead)

    def _cell_for(
        self, key: str, iv: Interval, offsets: frozenset[int], now: datetime, width: int
    ) -> tuple[Text, int]:
        """Build the (cell, row-height) for one visible column `key`."""
        if key == "id":
            return Text(str(iv.id), style=self._pal["warning"]), 1
        if key == "start":
            return Text(iv.start_local.strftime("%b %d %H:%M"),
                        style=self._pal["secondary"]), 1
        if key == "dur":
            return self._duration_cell(iv, now), 1
        if key == "tags":
            return Text(iv.tags_display, style=self._pal["primary"]), 1
        return self._annotation_cell(iv, offsets, width)  # "annotation"

    def _render(self, preserve_start: datetime | None = None) -> None:
        now = datetime.now(timezone.utc)
        self._refresh_palette()  # pick up the active theme's colors
        rows = self._compute_displayed(now)
        self.displayed = [iv for iv, _ in rows]
        # Size the elastic Tags column first; the annotation width depends on it.
        self._tags_w = self._dynamic_tags_width()
        self._apply_tags_width()
        width = self._annotation_width()

        # width of the annotation column as rendered, so selected rows fill it
        if self.wrap_annotations:
            ann_col_w = width
        else:
            ann_col_w = max(
                (len(iv.annotation) for iv in self.displayed if iv.annotation),
                default=width,
            )
        col_widths = [
            ann_col_w if key == "annotation" else self._render_width(key)
            for key in self._col_keys
        ]
        table = self.table
        table.clear()
        for iv, offsets in rows:
            cells: list[Text] = []
            height = 1
            for key in self._col_keys:
                cell, cell_h = self._cell_for(key, iv, offsets, now, width)
                cells.append(cell)
                height = max(height, cell_h)
            if iv.start in self.selected:
                cells = [
                    self._highlight_cell(cell, w)
                    for cell, w in zip(cells, col_widths)
                ]
            table.add_row(*cells, height=height)

        if table.row_count:
            target = 0
            if preserve_start is not None:
                for index, iv in enumerate(self.displayed):
                    if iv.start == preserve_start:
                        target = index
                        break
            table.move_cursor(row=min(target, table.row_count - 1))

        self._update_status(now)
        self._update_breakdown(now)
        self._update_detail()

    def _rerender_keep_cursor(self) -> None:
        current = self.current_interval()
        self._render(preserve_start=current.start if current else None)

    def _on_table_resize(self) -> None:
        if self._render_ready:
            self._rerender_keep_cursor()

    def _duration_cell(self, iv: Interval, now: datetime) -> Text:
        text = format_duration(iv.duration(now))
        if iv.is_active:
            return Text(f"\u25cf {text}", style=f"bold {self._pal['success']}")
        return Text(text)

    def _annotation_cell(
        self, iv: Interval, offsets: frozenset[int], width: int
    ) -> tuple[Text, int]:
        """Build the annotation cell + its row height, fitted to `width`."""
        if not iv.annotation:
            return Text("\u2014", style="dim"), 1

        if self.wrap_annotations:
            lines = textwrap.wrap(iv.annotation, width=width) or [""]
            if len(lines) > WRAP_MAX_LINES:
                lines = lines[:WRAP_MAX_LINES]
                lines[-1] = lines[-1][: max(1, width - 1)].rstrip() + "\u2026"
            return Text("\n".join(lines)), len(lines)

        # single line, full text; the table scrolls horizontally with h / l
        text = Text(iv.annotation)
        match_style = f"bold {self._pal['secondary']}"
        for offset in offsets:
            if 0 <= offset < len(iv.annotation):
                text.stylize(match_style, offset, offset + 1)
        return text, 1

    @staticmethod
    def _highlight_cell(cell: Text, width: int) -> Text:
        """Pad a cell to the full column width and paint it as a selected row."""
        style = f"{SELECT_FG} on {SELECT_BG}"
        if "\n" in cell.plain:  # wrapped multi-line annotation
            out = Text()
            lines = cell.split("\n")
            for index, line in enumerate(lines):
                if line.cell_len < width:
                    line.pad_right(width - line.cell_len)
                line.stylize(style)
                out.append_text(line)
                if index != len(lines) - 1:
                    out.append("\n", style)
            return out
        if cell.cell_len < width:
            cell.pad_right(width - cell.cell_len)
        cell.stylize(style)
        return cell

    # ------------------------------------------------------------- side panels
    def _amount_markup(self, td: timedelta) -> str:
        """Trailing ``  $X`` money markup for a duration, or '' when no rate is set.

        Returns the configured-rate dollar value of ``td`` (decimal hours × rate)
        in the success color, with a leading separator so it can be appended right
        after a Σ total. Empty string when ``brand.rate`` is unset (rate ≤ 0).
        """
        if self.brand.rate <= 0:
            return ""
        amount = format_amount(billing_amount(td, self.brand.rate))
        return f"  [{self._pal['success']}][b]{amount}[/b][/]"

    def _update_status(self, now: datetime) -> None:
        total = sum((iv.duration(now) for iv in self.displayed), timedelta())
        count = len(self.displayed)
        active = sum(1 for iv in self.displayed if iv.is_active)
        parts = [
            f"[b]{count}[/b] {'entry' if count == 1 else 'entries'}",
            f"[{self._pal['secondary']}]\u03a3 [b]{format_duration(total)}[/b] "
            f"([b]{format_hours_decimal(total)}[/b])[/]" + self._amount_markup(total),
        ]
        if active:
            parts.append(f"[{self._pal['success']}]\u25cf {active} active[/]")
        if self.selected:
            sel = [iv for iv in self.all_intervals if iv.start in self.selected]
            sel_total = sum((iv.duration(now) for iv in sel), timedelta())
            parts.append(
                f"[{self._pal['accent']}][b]{len(self.selected)}[/b] selected "
                f"\u03a3 [b]{format_duration(sel_total)}[/b] "
                f"([b]{format_hours_decimal(sel_total)}[/b])[/]"
                + self._amount_markup(sel_total)
            )
        if self.query:
            parts.append(f"[{self._pal['accent']}]filter[/] [i]{self.query}[/i]")
        self.query_one("#status", Static).update("    ".join(parts))

    def _update_breakdown(self, now: datetime) -> None:
        # Group by the exact tag-set of each interval. When rows are selected the
        # totals cover only the selection; otherwise the whole filtered view.
        # (The grand total of the set lives in the status bar, not here.)
        box = self.query_one("#breakdown", Static)
        if self.selected:
            source = [iv for iv in self.all_intervals if iv.start in self.selected]
            box.border_title = "Totals (selected)"
        else:
            source = self.displayed
            box.border_title = "Totals by tag-set"

        totals: dict[str, timedelta] = {}
        for iv in source:
            key = " + ".join(sorted(iv.tags)) if iv.tags else "(untagged)"
            totals[key] = totals.get(key, timedelta()) + iv.duration(now)

        lines: list[str] = []
        if not totals:
            lines.append("[dim]no entries in view[/dim]")
        else:
            for key, dur in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
                lines.append(f"[{self._pal['primary']}]{key}[/]")
                line = (
                    f"  [b]{format_duration(dur)}[/b]"
                    f"  [dim]{format_hours_decimal(dur)}[/dim]"
                )
                if self.brand.rate > 0:
                    amount = format_amount(billing_amount(dur, self.brand.rate))
                    line += f"  [{self._pal['success']}]{amount}[/]"
                lines.append(line)
        self.query_one("#breakdown", Static).update("\n".join(lines))

    def _update_detail(self) -> None:
        box = self.query_one("#detail", Static)
        iv = self.current_interval()
        if iv is None:
            box.update("[dim]no selection[/dim]")
            return
        now = datetime.now(timezone.utc)
        if iv.end_local:
            end = iv.end_local.strftime("%a %Y-%m-%d %H:%M:%S")
        else:
            end = f"[{self._pal['success']}]\u25cf active[/]"
        dur = iv.duration(now)
        tags = ", ".join(iv.tags) or "[dim]none[/dim]"
        annotation = iv.annotation or "[dim]none[/dim]"
        box.update(
            f"[{self._pal['secondary']}][b]@{iv.id}[/b][/]\n\n"
            f"[b]Start[/b]  {iv.start_local.strftime('%a %Y-%m-%d %H:%M:%S')}\n"
            f"[b]End[/b]    {end}\n"
            f"[b]Dur[/b]    {format_duration(dur)} "
            f"([b]{format_hours_decimal(dur)}[/b])\n"
            f"[{self._pal['primary']}]{tags}[/]\n\n"
            f"[b]Annotation[/b]\n{annotation}"
        )

    # ------------------------------------------------------------------ helpers
    def current_interval(self) -> Interval | None:
        if not self.displayed:
            return None
        row = self.table.cursor_row
        if 0 <= row < len(self.displayed):
            return self.displayed[row]
        return None

    def _tick(self) -> None:
        """Refresh active interval durations once a second."""
        if not any(iv.is_active for iv in self.displayed):
            return
        now = datetime.now(timezone.utc)
        dur_col = self._col_keys.index("dur") if "dur" in self._col_keys else None
        if dur_col is not None:
            for row, iv in enumerate(self.displayed):
                if iv.is_active:
                    self.table.update_cell_at(
                        Coordinate(row, dur_col), self._duration_cell(iv, now)
                    )
        self._update_status(now)
        self._update_detail()

    def _run(self, args, *, preserve_start=None, ok="Done") -> bool:
        """Execute one timew command, surface errors, reload on success."""
        try:
            timew.execute(args)
        except timew.TimewError as exc:
            self.notify(exc.message or str(exc), title="timew error",
                        severity="error", timeout=8)
            self.reload()
            return False
        self.notify(ok, severity="information", timeout=3)
        self.reload(preserve_start=preserve_start)
        return True

    # --------------------------------------------------------------- searching
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.query = event.value.strip()
            self._render(preserve_start=None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            event.stop()
            # Enter commits the filter and returns to the table. An empty filter
            # hides the box again; an active one stays visible to show the query.
            if not self.query:
                event.input.display = False
            self.table.focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail()

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()

    def action_escape(self) -> None:
        # In the search box: first esc clears an active filter, second esc (now
        # empty) hides the box. Otherwise clear the multi-selection, else nothing.
        search = self.query_one("#search", Input)
        if self.focused is search:
            if self.query:
                search.value = ""  # clears the filter (re-renders via on_input_changed)
            else:
                search.display = False
                self.table.focus()
        elif self.selected:
            self.selected.clear()
            self._rerender_keep_cursor()

    # ------------------------------------------------------------- multi-select
    def action_toggle_select(self) -> None:
        iv = self.current_interval()
        if iv is None:
            return
        if iv.start in self.selected:
            self.selected.discard(iv.start)
        else:
            self.selected.add(iv.start)
        row = self.table.cursor_row
        self._rerender_keep_cursor()
        # advance so you can space-space-space down a run of rows
        if row + 1 < self.table.row_count:
            self.table.move_cursor(row=row + 1)

    def _targets(self) -> list[Interval]:
        """Intervals an edit action applies to: the selection, or the cursor row."""
        if self.selected:
            chosen = [iv for iv in self.all_intervals if iv.start in self.selected]
            return sorted(chosen, key=lambda i: i.start, reverse=True)
        iv = self.current_interval()
        return [iv] if iv else []

    # ---------------------------------------------------------------- view toggles
    def action_toggle_wrap(self) -> None:
        self.wrap_annotations = not self.wrap_annotations
        self._rerender_keep_cursor()
        self.notify(
            f"Annotation wrap {'on' if self.wrap_annotations else 'off'}", timeout=2
        )

    def action_toggle_sidebar(self) -> None:
        self.show_sidebar = not self.show_sidebar
        self.query_one("#sidebar").display = self.show_sidebar
        # table width changes -> recompute annotation width after relayout
        self.call_after_refresh(self._rerender_keep_cursor)

    @work
    async def action_columns(self) -> None:
        result = await self.push_screen_wait(
            ColumnsScreen(
                [(c.key, c.label) for c in COLUMNS], self.visible_columns
            )
        )
        if result is None:
            return
        if not result:
            self.notify(
                "Keep at least one column visible", severity="warning", timeout=3
            )
            return
        self.visible_columns = result
        # Defer the rebuild until the modal has finished closing: mutating the
        # table mid-dismiss leaves the revealed table with a stale paint
        # (columns / annotation truncated) until the next input event forces a
        # re-composite. Running after the refresh makes the table the visible
        # top widget when it re-renders.
        self.call_after_refresh(self._apply_columns)

    def _apply_columns(self) -> None:
        self._rebuild_columns()
        self._rerender_keep_cursor()
        self.table.refresh(layout=True)

    # ------------------------------------------------------------- navigation
    def _at_table(self) -> bool:
        return self.focused is self.table

    def action_cursor_move(self, delta: int) -> None:
        if self.table.row_count:
            new = max(0, min(self.table.row_count - 1, self.table.cursor_row + delta))
            self.table.move_cursor(row=new)

    def action_cursor_bottom(self) -> None:
        if self.table.row_count:
            self.table.move_cursor(row=self.table.row_count - 1)

    def action_cursor_page(self, direction: int) -> None:
        page = max(1, self.table.size.height // 2)
        self.action_cursor_move(direction * page)

    def on_key(self, event) -> None:
        """Handle vim 'gg' (top) and 'dd' (delete) two-key sequences."""
        if not self._at_table():
            self._pending = None
            return
        pending, self._pending = self._pending, None
        if pending == "g" and event.key == "g":
            event.stop()
            self.table.move_cursor(row=0)
            return
        if pending == "d" and event.key == "d":
            event.stop()
            self.action_delete()
            return
        if event.key in ("g", "d"):
            self._pending = event.key
            event.stop()

    # ----------------------------------------------------------- edit actions
    @work
    async def action_annotate(self) -> None:
        iv = self.current_interval()
        if iv is None:
            return
        text = await self.push_screen_wait(
            TextPromptScreen(f"Annotation for @{iv.id}", iv.annotation,
                             placeholder="describe this interval")
        )
        if text is None:
            return
        self._run(timew.args_annotate(iv.id, text),
                  preserve_start=iv.start, ok=f"Annotated @{iv.id}")

    @staticmethod
    def _targets_label(targets: list[Interval]) -> str:
        return f"@{targets[0].id}" if len(targets) == 1 else f"{len(targets)} entries"

    @work
    async def action_add_tag(self) -> None:
        targets = self._targets()
        if not targets:
            return
        label = self._targets_label(targets)
        keep = self.current_interval()
        tags = await self.push_screen_wait(
            TextPromptScreen(f"Add tag(s) to {label}", "",
                             placeholder="space-separated, e.g. LA paid")
        )
        if not tags or not tags.split():
            return
        self._run(
            timew.args_tag_many([iv.id for iv in targets], tags.split()),
            preserve_start=keep.start if keep else None,
            ok=f"Tagged {label}",
        )

    @work
    async def action_remove_tag(self) -> None:
        targets = self._targets()
        if not targets:
            return
        tagset = sorted({t for iv in targets for t in iv.tags})
        if not tagset:
            self.notify("selected entries have no tags to remove",
                        severity="warning", timeout=3)
            return
        label = self._targets_label(targets)
        keep = self.current_interval()
        to_remove = await self.push_screen_wait(
            TagRemoveScreen(tagset, f"Remove tags from {label}")
        )
        if not to_remove:
            return
        self._run(
            timew.args_untag_many([iv.id for iv in targets], to_remove),
            preserve_start=keep.start if keep else None,
            ok=f"Removed {', '.join(to_remove)} from {label}",
        )

    @work
    async def action_modify_time(self) -> None:
        iv = self.current_interval()
        if iv is None:
            return
        result = await self.push_screen_wait(TimeEditScreen(iv))
        if not result:
            return
        cmds = []
        if "start" in result:
            cmds.append(timew.args_modify_start(iv.id, result["start"]))
        if "end" in result:
            cmds.append(timew.args_modify_end(iv.id, result["end"]))
        preserve = result["start"].astimezone() if "start" in result else iv.start
        try:
            for args in cmds:
                timew.execute(args)
        except timew.TimewError as exc:
            self.notify(exc.message or str(exc), title="timew error",
                        severity="error", timeout=8)
            self.reload()
            return
        self.notify(f"Modified @{iv.id}", severity="information", timeout=3)
        self.reload(preserve_start=preserve)

    @work
    async def action_delete(self) -> None:
        targets = self._targets()
        if not targets:
            return

        if len(targets) > 1:
            confirmed = await self.push_screen_wait(
                ConfirmScreen(
                    f"Delete {len(targets)} selected entries?\n"
                    "This removes them from Time Warrior (u = undo).",
                    confirm_label="Delete",
                )
            )
            if confirmed:
                self._run(
                    timew.args_delete_many([iv.id for iv in targets]),
                    ok=f"Deleted {len(targets)} entries",
                )
            return

        iv = targets[0]
        row = self.table.cursor_row
        neighbor = None
        if row + 1 < len(self.displayed):
            neighbor = self.displayed[row + 1].start
        elif row - 1 >= 0:
            neighbor = self.displayed[row - 1].start
        confirmed = await self.push_screen_wait(
            ConfirmScreen(
                f"Delete @{iv.id}?\n"
                f"{iv.start_local:%b %d %H:%M} · {iv.tags_display or 'no tags'}\n"
                f"{iv.annotation or '(no annotation)'}",
                confirm_label="Delete",
            )
        )
        if confirmed:
            self._run(timew.args_delete(iv.id),
                      preserve_start=neighbor, ok=f"Deleted @{iv.id}")

    @work
    async def action_start_tracking(self) -> None:
        tags = await self.push_screen_wait(
            TextPromptScreen("Start tracking — tag(s)", "",
                             placeholder="space-separated, optional")
        )
        if tags is None:
            return
        self._run(timew.args_start(tags.split()), ok="Started tracking")

    @work
    async def action_stop_tracking(self) -> None:
        confirmed = await self.push_screen_wait(
            ConfirmScreen("Stop the active interval?", confirm_label="Stop")
        )
        if confirmed:
            self._run(timew.args_stop(), ok="Stopped tracking")

    def action_continue_interval(self) -> None:
        """Resume the highlighted interval: start tracking now with its tags."""
        iv = self.current_interval()
        if iv is None:
            return
        label = iv.tags_display or "no tags"
        if self._run(timew.args_continue(iv.id), ok=f"Continuing @{iv.id} ({label})"):
            if self.table.row_count:
                self.table.move_cursor(row=0)  # jump to the new active interval

    @work
    async def action_new_interval(self) -> None:
        result = await self.push_screen_wait(NewIntervalScreen())
        if not result:
            return
        start_utc = result["start"].astimezone()
        try:
            timew.execute(timew.args_track(result["start"], result["end"], result["tags"]))
            if result["annotation"]:
                self.all_intervals = timew.load_intervals()
                match = next(
                    (iv for iv in self.all_intervals if iv.start == start_utc), None
                )
                if match is not None:
                    timew.execute(timew.args_annotate(match.id, result["annotation"]))
        except timew.TimewError as exc:
            self.notify(exc.message or str(exc), title="timew error",
                        severity="error", timeout=8)
            self.reload()
            return
        self.notify("Added interval", severity="information", timeout=3)
        self.reload(preserve_start=start_utc)

    @work
    async def action_undo(self) -> None:
        confirmed = await self.push_screen_wait(
            ConfirmScreen("Undo the last Time Warrior change?", confirm_label="Undo")
        )
        if confirmed:
            self._run(timew.args_undo(), ok="Undid last change")

    def action_reload(self) -> None:
        self.reload()
        self.notify("Reloaded", severity="information", timeout=2)

    # --------------------------------------------------------------- reporting
    def _report_targets(self) -> list[Interval]:
        """Intervals a report covers: the multi-selection, else the filtered view."""
        if self.selected:
            chosen = [iv for iv in self.all_intervals if iv.start in self.selected]
        else:
            chosen = list(self.displayed)
        return sorted(chosen, key=lambda i: i.start)

    @work
    async def action_report(self) -> None:
        targets = self._report_targets()
        if not targets:
            self.notify("Nothing to report", severity="warning", timeout=3)
            return
        # Suggest an invoice ID from the ledger + the targets' shared client tag
        # (e.g. LA-2026-003); the dialog's ID field is editable either way.
        ledger_file = invoices.ledger_path()
        ledger = invoices.load_ledger(ledger_file)
        taken = {inv.id for inv in ledger}
        client = invoices.derive_client([iv.tags for iv in targets], taken)
        result = await self.push_screen_wait(
            ReportScreen(
                count=len(targets),
                default_rate=self.brand.rate,
                default_invoice_id=invoices.next_invoice_id(ledger, client, _today()),
                taken_ids=taken,
            )
        )
        if not result:
            return
        if result["format"] == "pdf" and not report.chromium_available():
            # First PDF export: download the browser with a visible progress bar
            # before rendering (so failures are surfaced, not swallowed).
            ready = await self.push_screen_wait(DownloadScreen(report.install_chromium))
            if not ready:
                return
        try:
            # Chromium (PDF) and file IO can block briefly; keep the UI responsive.
            out = await asyncio.to_thread(
                report.write_report,
                targets,
                style=result["style"],
                fmt=result["format"],
                path=result["path"],
                rate=result["rate"],
            )
        except report.ReportError as exc:
            self.notify(str(exc), title="Report failed", severity="error", timeout=10)
            return
        except Exception as exc:  # noqa: BLE001 - surface any unexpected failure
            self.notify(str(exc), title="Report failed", severity="error", timeout=10)
            return
        self.notify(f"Wrote {out}", severity="information", timeout=5)
        if result["invoice_id"]:
            self._record_invoice(targets, result["invoice_id"], result["rate"],
                                 ledger, ledger_file)
        if result["format"] == "text":
            # Preview the text invoice in-app (the "console" output).
            await self.push_screen_wait(TextReportScreen(out.read_text(), path=out))
        elif result["open_after"]:
            self._open_file(out)

    def _record_invoice(
        self,
        targets: list[Interval],
        invoice_id: str,
        rate: float,
        ledger: list[invoices.Invoice],
        ledger_file: Path,
    ) -> None:
        """Snapshot an exported report into the ledger and retag its intervals.

        The recorded amount is exactly what the report shows (open intervals
        billed up to now). The retag is two timew calls — one atomic ``tag``
        adding ``invoiced`` + the invoice ID to every target, and one atomic
        ``untag`` dropping ``new`` from the targets that carry it (so ``u``
        undoes it in two steps). Both are tag-only operations, which never
        reorder intervals, so the @ids stay valid between the two calls. A
        failed ledger write aborts before any retag, so the tags never claim an
        invoice that was not recorded.
        """
        now = datetime.now(timezone.utc)
        total = sum((iv.duration(now) for iv in targets), timedelta())
        amount = billing_amount(total, rate)
        ledger.append(
            invoices.Invoice(
                id=invoice_id,
                date=_today().isoformat(),
                hours=total.total_seconds() / 3600.0,
                rate=rate,
                amount=amount,
                currency=self.brand.currency,
            )
        )
        try:
            invoices.save_ledger(ledger_file, ledger)
        except OSError as exc:
            self.notify(f"Could not save invoice ledger: {exc}",
                        title="Invoice not recorded", severity="error", timeout=10)
            return
        keep = self.current_interval()
        ids = [iv.id for iv in targets]
        with_new = [iv.id for iv in targets if "new" in iv.tags]
        try:
            timew.execute(timew.args_tag_many(ids, ["invoiced", invoice_id]))
            if with_new:
                timew.execute(timew.args_untag_many(with_new, ["new"]))
        except timew.TimewError as exc:
            self.notify(exc.message or str(exc), title="timew error",
                        severity="error", timeout=8)
            self.reload()
            return
        self.notify(
            f"Recorded invoice {invoice_id} ({format_amount(amount)}) — "
            f"retagged {len(ids)} {'entry' if len(ids) == 1 else 'entries'}",
            severity="information",
            timeout=5,
        )
        self.reload(preserve_start=keep.start if keep else None)

    @work
    async def action_invoices(self) -> None:
        """Open the invoice ledger; save it back if the screen changed it.

        Payments recorded in the screen can move an invoice across the "paid"
        boundary; the interval tags mirror that (``invoiced`` -> ``paid``, and
        back when a refund reopens the balance). The ledger is saved *first* —
        a failed save skips the retag, so tags never claim a payment state the
        ledger doesn't record.
        """
        ledger_file = invoices.ledger_path()
        ledger = invoices.load_ledger(ledger_file)
        before = {inv.id: inv.status for inv in ledger}
        changed = await self.push_screen_wait(InvoicesScreen(ledger))
        if not changed:
            return
        try:
            invoices.save_ledger(ledger_file, ledger)
        except OSError as exc:
            self.notify(f"Could not save invoice ledger: {exc}",
                        title="Ledger not saved", severity="error", timeout=10)
            return
        self._sync_paid_tags(*invoices.paid_transitions(before, ledger))

    def _sync_paid_tags(self, newly_paid: list[str], reopened: list[str]) -> None:
        """Mirror paid-status transitions onto the covered intervals' tags.

        For each transitioned invoice its intervals are found **by the
        invoice-ID tag** (timew renumbers @ids, the tag is the stable link):
        newly paid -> add ``paid``, drop ``invoiced``; reopened -> the reverse.
        Each swap is two atomic tag-only timew calls (never reorder intervals,
        so @ids stay valid between them; ``u`` twice undoes one swap). Invoices
        whose intervals can't be found (deleted / hand-retagged) only warn —
        the ledger change stands either way.
        """
        if not newly_paid and not reopened:
            return
        keep = self.current_interval()
        swapped = 0
        for invoice_id, add, remove in [
            *((iid, "paid", "invoiced") for iid in newly_paid),
            *((iid, "invoiced", "paid") for iid in reopened),
        ]:
            covered = [iv for iv in self.all_intervals if invoice_id in iv.tags]
            if not covered:
                self.notify(
                    f"No intervals tagged {invoice_id} — tags not updated",
                    severity="warning", timeout=8,
                )
                continue
            with_remove = [iv.id for iv in covered if remove in iv.tags]
            try:
                timew.execute(timew.args_tag_many([iv.id for iv in covered], [add]))
                if with_remove:
                    timew.execute(timew.args_untag_many(with_remove, [remove]))
            except timew.TimewError as exc:
                self.notify(exc.message or str(exc), title="timew error",
                            severity="error", timeout=8)
                self.reload()
                return
            swapped += 1
        if swapped:
            self.notify(
                f"Updated tags for {swapped} invoice{'s' if swapped != 1 else ''}",
                severity="information", timeout=4,
            )
            self.reload(preserve_start=keep.start if keep else None)

    def _open_file(self, path: Path) -> None:
        """Open ``path`` with the platform's default handler (best effort)."""
        if sys.platform == "darwin":
            opener = ["open", str(path)]
        elif sys.platform.startswith("linux"):
            opener = ["xdg-open", str(path)]
        elif sys.platform.startswith("win"):
            opener = ["cmd", "/c", "start", "", str(path)]
        else:
            return
        try:
            subprocess.Popen(
                opener, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as exc:
            self.notify(f"Could not open file: {exc}", severity="warning", timeout=5)

    @work
    async def action_help(self) -> None:
        await self.push_screen_wait(HelpScreen())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="timetui",
        description="A pretty, billing-focused Textual TUI for Time Warrior.",
    )
    parser.add_argument(
        "--timew-dir",
        metavar="DIR",
        help="Time Warrior database directory (sets TIMEWARRIORDB for timew). "
        "Overrides the [timew] db_path in config.toml and any inherited "
        "TIMEWARRIORDB.",
    )
    args = parser.parse_args(argv)
    # Resolve the db dir before the app mounts (and runs its first export):
    # CLI flag > config file > inherited environment.
    timew.TIMEW_DB = timew.resolve_timew_db(args.timew_dir, report.load_timew_db())
    # Create the directory up front: timew prompts (and would hang the TUI's
    # non-interactive export) the first time it sees a *missing* db dir, but
    # initializes silently once the directory exists.
    try:
        timew.ensure_db_dir(timew.TIMEW_DB)
    except OSError as exc:
        sys.exit(f"timetui: cannot use Time Warrior dir {timew.TIMEW_DB!r}: {exc}")
    TimewApp().run()


if __name__ == "__main__":
    main()
