"""Modal dialogs used by the timetui app."""

from __future__ import annotations

from datetime import datetime, timedelta

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    ProgressBar,
    RadioButton,
    RadioSet,
    SelectionList,
    Static,
)

from .invoices import Invoice, Payment
from .models import Interval, format_amount

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


class ExpenseScreen(ModalScreen[dict | None]):
    """Add a fixed expense (a flight, a hotel night, ...).

    Expenses are date-granular — the app stores one as a synthetic 1-minute
    interval at 00:00 of the chosen day, tagged ``expense`` + ``cost:<amount>``
    (see ``models``). Returns ``{"day": datetime, "amount": float, "tags":
    list[str], "description": str}`` (``day`` is local midnight, naive) or
    ``None`` if cancelled.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, default_tags: str = "") -> None:
        super().__init__()
        self._default_tags = default_tags

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("New expense", classes="dialog-title")
            yield Label("Date")
            yield Input(value=datetime.now().strftime("%Y-%m-%d"), id="date-input")
            yield Label("Amount")
            yield Input(placeholder="e.g. 450.00", id="amount-input")
            yield Label("Tags  (space-separated, e.g. the client)")
            yield Input(value=self._default_tags, id="tags-input")
            yield Label("Description  (optional)")
            yield Input(placeholder="e.g. Flight SFO-NRT", id="desc-input")
            yield Static("", id="error", classes="error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Add", variant="success", id="save")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#amount-input", Input).focus()

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
            day = datetime.strptime(
                self.query_one("#date-input", Input).value.strip(), "%Y-%m-%d"
            )
        except ValueError:
            err.update("Bad date (use YYYY-MM-DD)")
            return
        raw = (
            self.query_one("#amount-input", Input)
            .value.strip()
            .lstrip("$")
            .replace(",", "")
        )
        try:
            amount = float(raw)
        except ValueError:
            err.update("Amount must be a number")
            return
        if amount <= 0:
            err.update("Amount must be greater than zero")
            return
        self.dismiss(
            {
                "day": day,
                "amount": amount,
                "tags": self.query_one("#tags-input", Input).value.split(),
                "description": self.query_one("#desc-input", Input).value.strip(),
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

    Returns ``{"style", "format", "rate", "path", "open_after", "invoice_id"}``
    (path kept as typed, e.g. ``~/...``; the caller expands it) or None if
    cancelled. ``invoice_id`` is the ID to record the export as in the invoice
    ledger (see ``invoices.py``), or ``None`` when the "Record invoice" box is
    unchecked. ``default_invoice_id`` pre-fills the (always editable) ID field
    with the app's suggestion; ``taken_ids`` are existing ledger IDs, rejected
    on save so an invoice number is never reused. ``has_expenses`` marks a
    target set containing fixed ``cost:`` expenses — an expense-only invoice is
    legitimate at rate 0, so it relaxes the "recording requires a rate" check.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        count: int,
        default_path: str = "~/timetui-report.html",
        default_rate: float = 0.0,
        default_invoice_id: str = "",
        taken_ids: "frozenset[str] | set[str]" = frozenset(),
        has_expenses: bool = False,
    ) -> None:
        super().__init__()
        self._count = count
        self._default_path = default_path
        self._default_rate = default_rate
        self._default_invoice_id = default_invoice_id
        self._taken_ids = set(taken_ids)
        self._has_expenses = has_expenses

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
            rate_value = f"{self._default_rate:g}" if self._default_rate > 0 else ""
            yield Input(value=rate_value, placeholder="e.g. 100", id="rate-input")
            with Horizontal(id="invoice-row"):
                yield Checkbox("Record invoice", value=False, id="invoice-checkbox")
                yield Input(
                    value=self._default_invoice_id,
                    placeholder="e.g. LA-2026-003",
                    id="invoice-input",
                    disabled=True,
                )
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

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """The invoice-ID field is only editable while "Record invoice" is on."""
        if event.checkbox.id == "invoice-checkbox":
            self.query_one("#invoice-input", Input).disabled = not event.value

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
        invoice_id: str | None = None
        if self.query_one("#invoice-checkbox", Checkbox).value:
            invoice_id = self.query_one("#invoice-input", Input).value.strip()
            if not invoice_id:
                err.update("Invoice ID is required to record an invoice")
                return
            if any(ch.isspace() for ch in invoice_id):
                err.update("Invoice ID must not contain spaces (it becomes a tag)")
                return
            if invoice_id in self._taken_ids:
                err.update(f"Invoice {invoice_id} already exists in the ledger")
                return
            if rate <= 0 and not self._has_expenses:
                err.update("Recording an invoice requires an hourly rate")
                return
        self.dismiss(
            {
                "style": self._selected("style-set"),
                "format": self._selected("format-set"),
                "rate": rate,
                "path": path,
                "open_after": self.query_one("#open-checkbox", Checkbox).value,
                "invoice_id": invoice_id,
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


class PaymentScreen(ModalScreen["dict | None"]):
    """Record one payment against an invoice.

    Returns ``{"amount", "date", "note"}`` (amount ``float``, date a local ISO
    string) or None if cancelled. The amount pre-fills with the outstanding
    balance — the common "paid in full" case is just Enter — and the date with
    today. Negative amounts are allowed (refunds / adjustments); zero is not.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, invoice_id: str, balance: float) -> None:
        super().__init__()
        self._invoice_id = invoice_id
        self._balance = balance

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(
                f"Record payment — {self._invoice_id}", classes="dialog-title"
            )
            yield Label(f"Amount  (balance {format_amount(self._balance)})")
            amount_value = f"{self._balance:.2f}" if self._balance > 0 else ""
            yield Input(value=amount_value, placeholder="e.g. 500", id="amount-input")
            yield Label("Date  (YYYY-MM-DD)")
            yield Input(value=datetime.now().strftime("%Y-%m-%d"), id="date-input")
            yield Label("Note  (optional)")
            yield Input(placeholder="e.g. wire ref 123", id="note-input")
            yield Static("", id="error", classes="error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Record", variant="success", id="save")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self) -> None:
        inp = self.query_one("#amount-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

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
        raw = (
            self.query_one("#amount-input", Input)
            .value.strip()
            .lstrip("$")
            .replace(",", "")
        )
        try:
            amount = float(raw)
        except ValueError:
            err.update("Payment amount must be a number")
            return
        if amount == 0:
            err.update("Payment amount must not be zero")
            return
        date_raw = self.query_one("#date-input", Input).value.strip()
        if not date_raw:
            date_raw = datetime.now().strftime("%Y-%m-%d")
        try:
            datetime.strptime(date_raw, "%Y-%m-%d")
        except ValueError:
            err.update(f"Bad date: {date_raw!r}  (use YYYY-MM-DD)")
            return
        self.dismiss(
            {
                "amount": amount,
                "date": date_raw,
                "note": self.query_one("#note-input", Input).value.strip(),
            }
        )


class InvoicesScreen(ModalScreen[bool]):
    """Browse the invoice ledger: amount / paid / balance / status per invoice,
    with the highlighted invoice's payment history below the table.

    ``p`` records a payment (opens :class:`PaymentScreen`), ``u`` undoes the
    highlighted invoice's most recently *recorded* payment (confirmed — the
    typo fix), ``x`` deletes an invoice (confirmed). The list passed in is
    mutated in place; the screen dismisses ``True`` when it changed, so the app
    knows to save the ledger.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("p", "payment", "Record payment"),
        Binding("u", "undo_payment", "Undo last payment"),
        Binding("x", "delete", "Delete invoice"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    STATUS_STYLE = {"paid": "green", "partial": "yellow", "unpaid": "red"}
    COLUMN_LABELS = ("Invoice", "Date", "Hours", "Amount", "Paid", "Balance", "Status")

    def __init__(self, invoices: "list[Invoice]") -> None:
        super().__init__()
        self._invoices = invoices  # mutated in place; True on dismiss = changed
        self._changed = False
        self._order: list[Invoice] = []  # table rows, newest first

    def compose(self) -> ComposeResult:
        with Vertical(id="invoices-dialog"):
            yield Static("Invoices", classes="dialog-title")
            yield DataTable(
                id="invoices-table", cursor_type="row", zebra_stripes=True
            )
            yield Static(id="invoice-summary")
            yield Static(id="invoice-detail")
            yield Static(
                "p  record payment     u  undo last payment     "
                "x  delete invoice     esc / q  close",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#invoice-detail", Static).border_title = "Payments"
        self._refresh()
        self.query_one("#invoices-table", DataTable).focus()

    def _refresh(self, keep_id: str | None = None) -> None:
        """Rebuild the rows (newest first), keeping the cursor on ``keep_id``.

        Columns are re-created with explicit widths sized to the widest cell
        (never below the header) on every refresh: a DataTable's auto widths are
        computed once and go stale when a refresh replaces cells with wider
        content (e.g. paid ``$0.00`` -> ``$2,010.00``, ``unpaid`` -> ``partial``
        after recording a payment), which visibly truncated values.
        """
        table = self.query_one("#invoices-table", DataTable)
        self._order = sorted(
            self._invoices, key=lambda inv: (inv.date, inv.id), reverse=True
        )
        rows: list[tuple[Text, ...]] = []
        for inv in self._order:
            style = self.STATUS_STYLE.get(inv.status, "")
            rows.append(
                (
                    Text(inv.id, style="bold"),
                    Text(inv.date),
                    Text(f"{inv.hours:.2f}h"),
                    Text(format_amount(inv.amount)),
                    Text(format_amount(inv.paid)),
                    Text(format_amount(inv.balance), style=style),
                    Text(inv.status, style=style),
                )
            )
        table.clear(columns=True)
        for index, label in enumerate(self.COLUMN_LABELS):
            width = max(
                (cells[index].cell_len for cells in rows), default=0
            )
            table.add_column(label, width=max(width, len(label)))
        for cells in rows:
            table.add_row(*cells)
        if table.row_count:
            target = 0
            if keep_id is not None:
                for index, inv in enumerate(self._order):
                    if inv.id == keep_id:
                        target = index
                        break
            table.move_cursor(row=min(target, table.row_count - 1))
        self._update_summary()
        self._update_detail()

    def _current(self) -> "Invoice | None":
        row = self.query_one("#invoices-table", DataTable).cursor_row
        if 0 <= row < len(self._order):
            return self._order[row]
        return None

    def _update_summary(self) -> None:
        invoiced = sum(inv.amount for inv in self._invoices)
        paid = sum(inv.paid for inv in self._invoices)
        outstanding = sum(inv.balance for inv in self._invoices if inv.status != "paid")
        self.query_one("#invoice-summary", Static).update(
            f"\u03a3 invoiced [b]{format_amount(invoiced)}[/b]"
            f"    paid [green]{format_amount(paid)}[/green]"
            f"    outstanding [red][b]{format_amount(outstanding)}[/b][/red]"
        )

    def _update_detail(self) -> None:
        box = self.query_one("#invoice-detail", Static)
        inv = self._current()
        if inv is None:
            box.update(
                "[dim]no invoices yet — press R and check "
                "\u201cRecord invoice\u201d when exporting a report[/dim]"
            )
            return
        style = self.STATUS_STYLE.get(inv.status, "")
        lines = [
            f"[b]{escape(inv.id)}[/b]  {inv.date}  \u2014  {inv.hours:.2f}h "
            f"\u00d7 ${inv.rate:g}/h = {format_amount(inv.amount)} {escape(inv.currency)}"
        ]
        if inv.payments:
            for p in sorted(inv.payments, key=lambda p: p.date):
                note = f"  [dim]{escape(p.note)}[/dim]" if p.note else ""
                lines.append(f"  {p.date}  {format_amount(p.amount)}{note}")
        else:
            lines.append("  [dim]no payments recorded[/dim]")
        lines.append(
            f"balance [b][{style}]{format_amount(inv.balance)}[/{style}][/b] "
            f"([{style}]{inv.status}[/{style}])"
        )
        box.update("\n".join(lines))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail()

    def action_cursor_down(self) -> None:
        self.query_one("#invoices-table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#invoices-table", DataTable).action_cursor_up()

    @work
    async def action_payment(self) -> None:
        inv = self._current()
        if inv is None:
            return
        result = await self.app.push_screen_wait(PaymentScreen(inv.id, inv.balance))
        if not result:
            return
        inv.payments.append(
            Payment(date=result["date"], amount=result["amount"], note=result["note"])
        )
        self._changed = True
        self._refresh(keep_id=inv.id)

    @work
    async def action_undo_payment(self) -> None:
        """Remove the highlighted invoice's most recently recorded payment.

        "Last" is entry order (``payments[-1]``), not payment date — the point
        is undoing a just-mistyped amount. Crossing the paid boundary backwards
        is handled by the normal close flow (``paid_transitions`` reopens the
        invoice and swaps its interval tags ``paid -> invoiced``).
        """
        inv = self._current()
        if inv is None:
            return
        if not inv.payments:
            self.app.notify(
                f"{inv.id} has no payments to undo", severity="warning", timeout=3
            )
            return
        last = inv.payments[-1]
        note = f"  ({escape(last.note)})" if last.note else ""
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(
                f"Undo the last payment on {inv.id}?\n"
                f"{last.date}  {format_amount(last.amount)}{note}",
                confirm_label="Undo",
            )
        )
        if not confirmed:
            return
        inv.payments.pop()
        self._changed = True
        self._refresh(keep_id=inv.id)

    @work
    async def action_delete(self) -> None:
        inv = self._current()
        if inv is None:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(
                f"Delete invoice {inv.id} from the ledger?\n"
                f"{format_amount(inv.amount)}, {len(inv.payments)} payment(s) "
                "recorded. Interval tags are not touched.",
                confirm_label="Delete",
            )
        )
        if not confirmed:
            return
        self._invoices.remove(inv)
        self._changed = True
        self._refresh()

    def action_close(self) -> None:
        self.dismiss(self._changed)


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
  set an hourly rate in config -> live $ amounts per tag-set and at the Σ
  E    add a fixed expense (flight, hotel, ...): stored as a tiny interval
       tagged expense + cost:AMOUNT — billed as $, never as time, and
       itemized on reports (Amount Due = hours x rate + expenses)

[b]Invoices[/b]  (tags mirror the lifecycle: new -> invoiced -> paid)
  R    "Record invoice" in the report dialog snapshots the amount into the
       ledger and retags the intervals (new -> invoiced + the invoice ID)
  I    invoice ledger: amount / paid / balance   p  payment   x  delete
       u  undo the invoice's most recent payment (typo fix)
       the payment that settles a balance retags its intervals
       invoiced -> paid (a reopening refund swaps back)

[b]Edit[/b] (Time Warrior)
  a    annotate            t / T   add / remove tag
  m    modify start/end    o       new interval (track)
  E    add expense         dd      delete (confirm)
  s / S start / stop       c       continue (resume)
  u    undo last change    r       reload
  R    generate report (selection / view)

  q    quit                ?       this help"""

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("timetui — keybindings", classes="dialog-title")
            yield Static(self.HELP, id="help-body")
            yield Static("esc / q / ?  to close", classes="hint")

    def action_close(self) -> None:
        self.dismiss(None)
