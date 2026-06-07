"""Modal dialogs used by the timetui app."""

from __future__ import annotations

from datetime import datetime, timedelta

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    ProgressBar,
    RadioButton,
    RadioSet,
    SelectionList,
    Static,
)

from .models import Interval

LOCAL_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)


def parse_local(value: str) -> datetime:
    """Parse a user-entered local date/time (naive) in a few common formats."""
    value = value.strip()
    for fmt in LOCAL_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Bad date/time: {value!r}  (use YYYY-MM-DD HH:MM)")


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No confirmation. Returns True/False."""

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "No"),
    ]

    def __init__(self, prompt: str, *, confirm_label: str = "Yes") -> None:
        super().__init__()
        self._prompt = prompt
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._prompt, id="dialog-question")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self._confirm_label, variant="error", id="confirm")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TextPromptScreen(ModalScreen[str | None]):
    """Single-line text prompt. Returns the string, or None if cancelled."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, initial: str = "", *, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._initial = initial
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._title, classes="dialog-title")
            yield Input(
                value=self._initial, placeholder=self._placeholder, id="prompt-input"
            )
            yield Static("enter: save     esc: cancel", classes="hint")

    def on_mount(self) -> None:
        inp = self.query_one(Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TimeEditScreen(ModalScreen[dict | None]):
    """Edit start/end of an interval. Returns {'start': dt, 'end': dt} of *changed*
    fields only (naive local datetimes), or None if nothing changed / cancelled."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, interval: Interval) -> None:
        super().__init__()
        self.interval = interval
        self.start_naive = interval.start_local.replace(tzinfo=None)
        end_local = interval.end_local
        self.end_naive = end_local.replace(tzinfo=None) if end_local else None

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"Modify times — @{self.interval.id}", classes="dialog-title")
            yield Label("Start  (YYYY-MM-DD HH:MM:SS)")
            yield Input(
                value=self.start_naive.strftime("%Y-%m-%d %H:%M:%S"), id="start-input"
            )
            yield Label("End" + ("   (active — disabled)" if self.end_naive is None else ""))
            yield Input(
                value=self.end_naive.strftime("%Y-%m-%d %H:%M:%S") if self.end_naive else "",
                id="end-input",
                disabled=self.end_naive is None,
            )
            yield Static("", id="error", classes="error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Save", variant="success", id="save")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#start-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        err = self.query_one("#error", Static)
        try:
            new_start = parse_local(self.query_one("#start-input", Input).value)
        except ValueError as exc:
            err.update(str(exc))
            return

        new_end = None
        if self.end_naive is not None:
            try:
                new_end = parse_local(self.query_one("#end-input", Input).value)
            except ValueError as exc:
                err.update(str(exc))
                return

        effective_end = new_end if new_end is not None else self.end_naive
        if effective_end is not None and new_start >= effective_end:
            err.update("Start must be before end")
            return

        result: dict = {}
        if new_start != self.start_naive:
            result["start"] = new_start
        if new_end is not None and new_end != self.end_naive:
            result["end"] = new_end
        self.dismiss(result or None)


class NewIntervalScreen(ModalScreen[dict | None]):
    """Add a historical interval (timew track). Returns dict or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, default_tags: str = "") -> None:
        super().__init__()
        self._default_tags = default_tags

    def compose(self) -> ComposeResult:
        now = datetime.now().replace(microsecond=0, second=0)
        start_default = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        end_default = now.strftime("%Y-%m-%d %H:%M:%S")
        with Vertical(id="dialog"):
            yield Static("New interval — timew track", classes="dialog-title")
            yield Label("Start")
            yield Input(value=start_default, id="start-input")
            yield Label("End")
            yield Input(value=end_default, id="end-input")
            yield Label("Tags  (space-separated)")
            yield Input(value=self._default_tags, id="tags-input")
            yield Label("Annotation  (optional)")
            yield Input(id="ann-input")
            yield Static("", id="error", classes="error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Add", variant="success", id="save")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#start-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        err = self.query_one("#error", Static)
        try:
            start = parse_local(self.query_one("#start-input", Input).value)
            end = parse_local(self.query_one("#end-input", Input).value)
        except ValueError as exc:
            err.update(str(exc))
            return
        if end <= start:
            err.update("End must be after start")
            return
        self.dismiss(
            {
                "start": start,
                "end": end,
                "tags": self.query_one("#tags-input", Input).value.split(),
                "annotation": self.query_one("#ann-input", Input).value.strip(),
            }
        )


class TagRemoveScreen(ModalScreen["list[str] | None"]):
    """Pick which of an interval's existing tags to remove.

    Returns the list of tags to remove, or None if cancelled / nothing chosen.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Remove", priority=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, tags: "list[str]", title: str) -> None:
        super().__init__()
        self._tags = list(tags)
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._title, classes="dialog-title")
            yield SelectionList[str](
                *[(tag, tag) for tag in self._tags], id="tag-list"
            )
            yield Static(
                "space: toggle     enter: remove     esc: cancel", classes="hint"
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Remove", variant="error", id="remove")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self) -> None:
        self.query_one(SelectionList).focus()

    def action_cursor_down(self) -> None:
        self.query_one(SelectionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(SelectionList).action_cursor_up()

    def action_confirm(self) -> None:
        selected = list(self.query_one(SelectionList).selected)
        self.dismiss(selected or None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "remove":
            self.action_confirm()
        else:
            self.dismiss(None)


class ColumnsScreen(ModalScreen["set[str] | None"]):
    """Pick which table columns are visible.

    ``columns`` is an ordered list of ``(key, label)`` pairs; ``visible`` is the
    set of currently-shown keys (used to pre-check the list). Returns the new set
    of visible keys, or None if cancelled.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Apply", priority=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, columns: "list[tuple[str, str]]", visible: "set[str]") -> None:
        super().__init__()
        self._columns = list(columns)
        self._visible = set(visible)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Show / hide columns", classes="dialog-title")
            yield SelectionList[str](
                *[
                    (label, key, key in self._visible)
                    for key, label in self._columns
                ],
                id="tag-list",
            )
            yield Static(
                "space: toggle     enter: apply     esc: cancel", classes="hint"
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Apply", variant="success", id="apply")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self) -> None:
        self.query_one(SelectionList).focus()

    def action_cursor_down(self) -> None:
        self.query_one(SelectionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(SelectionList).action_cursor_up()

    def action_confirm(self) -> None:
        self.dismiss(set(self.query_one(SelectionList).selected))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self.action_confirm()
        else:
            self.dismiss(None)


class VimRadioSet(RadioSet):
    """RadioSet with vim-style j/k navigation (in addition to the arrow keys).

    The bindings only fire while the radio set itself is focused, so they never
    interfere with typing j/k into other widgets (e.g. the output-path Input).
    """

    BINDINGS = [
        Binding("j", "next_button", "Down", show=False),
        Binding("k", "previous_button", "Up", show=False),
    ]


class ReportScreen(ModalScreen["dict | None"]):
    """Configure and trigger report generation.

    Returns ``{"style", "format", "rate", "path", "open_after"}`` (path kept as
    typed, e.g. ``~/...``; the caller expands it) or None if cancelled.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, count: int, default_path: str = "~/timetui-report.html") -> None:
        super().__init__()
        self._count = count
        self._default_path = default_path

    def compose(self) -> ComposeResult:
        label = "entry" if self._count == 1 else "entries"
        with Vertical(id="dialog"):
            yield Static(f"Generate report — {self._count} {label}", classes="dialog-title")
            with Horizontal(id="report-choices"):
                with Vertical(classes="report-col"):
                    yield Label("Style")
                    with VimRadioSet(id="style-set"):
                        yield RadioButton("cyberpunk", value=True, id="style-cyberpunk")
                        yield RadioButton("printer", id="style-printer")
                with Vertical(classes="report-col"):
                    yield Label("Format")
                    with VimRadioSet(id="format-set"):
                        yield RadioButton("html", value=True, id="format-html")
                        yield RadioButton("pdf", id="format-pdf")
                        yield RadioButton("text", id="format-text")
            yield Label("Hourly rate  (blank = no amount)")
            yield Input(placeholder="e.g. 100", id="rate-input")
            yield Label("Output file")
            yield Input(value=self._default_path, id="path-input")
            yield Checkbox("Open when done", value=True, id="open-checkbox")
            yield Static("", id="error", classes="error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Generate", variant="success", id="generate")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#style-set", VimRadioSet).focus()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Flip the output-file extension when the format toggles."""
        if event.radio_set.id != "format-set":
            return
        ext = {"format-pdf": "pdf", "format-text": "txt"}.get(event.pressed.id, "html")
        inp = self.query_one("#path-input", Input)
        base = inp.value.strip()
        for known in (".html", ".pdf", ".txt"):
            if base.endswith(known):
                base = base[: -len(known)]
                break
        inp.value = f"{base}.{ext}"

    def _selected(self, set_id: str) -> str:
        pressed = self.query_one(f"#{set_id}", RadioSet).pressed_button
        return str(pressed.label) if pressed is not None else ""

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "generate":
            self.action_save()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        err = self.query_one("#error", Static)
        path = self.query_one("#path-input", Input).value.strip()
        if not path:
            err.update("Output file path is required")
            return
        rate = 0.0
        rate_raw = self.query_one("#rate-input", Input).value.strip().lstrip("$").replace(",", "")
        if rate_raw:
            try:
                rate = float(rate_raw)
            except ValueError:
                err.update("Hourly rate must be a number")
                return
            if rate < 0:
                err.update("Hourly rate must not be negative")
                return
        self.dismiss(
            {
                "style": self._selected("style-set"),
                "format": self._selected("format-set"),
                "rate": rate,
                "path": path,
                "open_after": self.query_one("#open-checkbox", Checkbox).value,
            }
        )


class DownloadScreen(ModalScreen[bool]):
    """One-time Chromium headless-shell download, with a progress bar + ETA.

    ``installer`` is a blocking callable ``installer(progress)`` where ``progress``
    is ``progress(percent: float, label: str)`` (e.g. ``report.install_chromium``).
    Dismisses ``True`` once the browser is ready, or ``False`` if the download
    failed — the error is shown in place until the user presses Esc.
    """

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("enter", "close", show=False),
        Binding("q", "close", show=False),
    ]

    def __init__(self, installer) -> None:
        super().__init__()
        self._installer = installer
        self._failed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Preparing PDF export", classes="dialog-title")
            yield Static(
                "Downloading the Chromium headless shell — a one-time ~90 MB "
                "setup. Future PDF exports are instant.",
                id="dl-status",
            )
            yield ProgressBar(total=100.0, id="dl-bar")
            yield Static("", id="dl-error", classes="error")
            yield Static("Please wait\u2026", classes="hint", id="dl-hint")

    def on_mount(self) -> None:
        self.query_one("#dl-bar", ProgressBar).update(progress=0)
        self._download()

    @work(thread=True, exclusive=True)
    def _download(self) -> None:
        def on_progress(percent: float, label: str) -> None:
            self.app.call_from_thread(self._set_progress, percent, label)

        try:
            self._installer(on_progress)
        except Exception as exc:  # noqa: BLE001 - surface the real installer error
            self.app.call_from_thread(self._fail, str(exc))
        else:
            self.app.call_from_thread(self.dismiss, True)

    def _set_progress(self, percent: float, label: str) -> None:
        self.query_one("#dl-bar", ProgressBar).update(progress=percent)
        self.query_one("#dl-status", Static).update(label)

    def _fail(self, message: str) -> None:
        self._failed = True
        self.query_one("#dl-status", Static).update("PDF engine setup failed:")
        self.query_one("#dl-error", Static).update(message)
        self.query_one("#dl-hint", Static).update("Press Esc to close.")

    def action_close(self) -> None:
        # Only closeable once the download has failed; success auto-dismisses.
        if self._failed:
            self.dismiss(False)


class TextReportScreen(ModalScreen[None]):
    """Scrollable in-app preview of the plain-text invoice (the console output)."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
        Binding("ctrl+d", "page_down", "½ down", show=False),
        Binding("ctrl+u", "page_up", "½ up", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("G", "scroll_end", "Bottom", show=False),
    ]

    def __init__(self, content: str, path: "object | None" = None) -> None:
        super().__init__()
        self._content = content
        self._path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="text-dialog"):
            yield Static("Invoice — text", classes="dialog-title")
            with VerticalScroll(id="text-report"):
                yield Static(self._content, id="text-report-body")
            hint = "j/k scroll    esc / q  close"
            if self._path is not None:
                hint = f"saved to {self._path}\n{hint}"
            yield Static(hint, classes="hint")

    def on_mount(self) -> None:
        self.query_one("#text-report", VerticalScroll).focus()

    def action_scroll_down(self) -> None:
        self.query_one("#text-report", VerticalScroll).scroll_relative(y=2)

    def action_scroll_up(self) -> None:
        self.query_one("#text-report", VerticalScroll).scroll_relative(y=-2)

    def action_page_down(self) -> None:
        self.query_one("#text-report", VerticalScroll).scroll_page_down()

    def action_page_up(self) -> None:
        self.query_one("#text-report", VerticalScroll).scroll_page_up()

    def action_scroll_home(self) -> None:
        self.query_one("#text-report", VerticalScroll).scroll_home()

    def action_scroll_end(self) -> None:
        self.query_one("#text-report", VerticalScroll).scroll_end()

    def action_close(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Keybinding cheatsheet."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("question_mark", "close", "Close"),
    ]

    HELP = """\
[b]Navigation[/b]
  j / k            down / up
  gg / G           top / bottom
  ctrl+d / ctrl+u  half page down / up
  h / l            scroll annotation left / right
  /                focus search        esc  leave search / clear selection

[b]Select[/b] (for mass edits)
  space            toggle selection of the current row (highlighted)
  esc              clear the selection
  t / T / dd then act on ALL selected rows when a selection exists

[b]View[/b]
  w    wrap annotations (multi-line rows)
  f    toggle sidebar (full-width table)
  C    show / hide columns (ID hidden by default)
  the sidebar Detail shows the selected row's full annotation

[b]Billing[/b]
  Σ in status bar = total of the filtered set (Hh Mm + decimal)
  sidebar shows totals grouped by tag-set

[b]Edit[/b] (Time Warrior)
  a    annotate            t / T   add / remove tag
  m    modify start/end    o       new interval (track)
  dd   delete (confirm)    s / S   start / stop tracking
  c    continue (resume)   u       undo last change
  r    reload              R       generate report (selection / view)

  q    quit                ?       this help"""

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("timetui — keybindings", classes="dialog-title")
            yield Static(self.HELP, id="help-body")
            yield Static("esc / q / ?  to close", classes="hint")

    def action_close(self) -> None:
        self.dismiss(None)
