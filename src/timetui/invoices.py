"""Invoice ledger: which invoices were sent, and what has been paid on them.

Time Warrior only stores intervals/tags/annotations, so invoices and payments
live in a small JSON ledger (``invoices.json``) kept **next to the Time Warrior
database** — a separate client db automatically gets its own separate ledger,
and it is covered by the same backups.

Split, like ``timew.py`` / ``report.py``, into a **pure** part and an **impure**
part:

* The :class:`Invoice` / :class:`Payment` model, JSON (de)serialization
  (:func:`dumps` / :func:`loads`), the invoice-ID scheme
  (:func:`derive_client` / :func:`next_invoice_id`) and the ledger-directory
  resolution (:func:`resolve_ledger_dir`) never touch the filesystem and are
  unit-tested directly.
* Only :func:`ledger_path`, :func:`load_ledger` and :func:`save_ledger` perform
  IO.

Invoice IDs follow ``{Client}-{year}-{seq}`` (e.g. ``LA-2026-003``): the client
prefix is the tag shared by every invoiced interval, and the sequence is scoped
per client per year. The ID doubles as the timew tag stamped on the covered
intervals, so filtering by it shows exactly that invoice's work.

Interval tags mirror the lifecycle ``new -> invoiced -> paid``: recording an
invoice swaps ``new`` for ``invoiced``, and a payment that settles the balance
swaps ``invoiced`` for ``paid`` (a refund that reopens the balance swaps back
— see :func:`paid_transitions`, driven by ``app.action_invoices``).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# Tags that mark workflow state (new -> invoiced -> paid), not a client —
# never a client prefix candidate.
WORKFLOW_TAGS = frozenset({"new", "invoiced", "paid"})
# Half a cent: float dust from hours × rate never flips paid/partial status.
PAID_EPSILON = 0.005
LEDGER_FILENAME = "invoices.json"


@dataclass
class Payment:
    """One (possibly partial) payment received against an invoice."""

    date: str  # local ISO date, e.g. "2026-07-10"
    amount: float
    note: str = ""


@dataclass
class Invoice:
    """One invoice sent: a snapshot of hours/rate/amount plus its payments.

    ``id`` (e.g. ``LA-2026-003``) is also the timew tag stamped on the covered
    intervals. ``amount`` is the snapshot taken when the invoice was recorded —
    it does not drift if the intervals are edited later.
    """

    id: str
    date: str  # local ISO date the invoice was recorded
    hours: float
    rate: float
    amount: float
    currency: str = "USD"
    payments: list[Payment] = field(default_factory=list)

    @property
    def paid(self) -> float:
        return sum(p.amount for p in self.payments)

    @property
    def balance(self) -> float:
        return self.amount - self.paid

    @property
    def status(self) -> str:
        """``"unpaid"`` / ``"partial"`` / ``"paid"`` (within :data:`PAID_EPSILON`)."""
        if self.balance <= PAID_EPSILON:
            return "paid"
        if self.paid > PAID_EPSILON:
            return "partial"
        return "unpaid"


# --------------------------------------------------------------------------- #
# Invoice-ID scheme (pure)
# --------------------------------------------------------------------------- #
def derive_client(
    tag_sets: Sequence[Sequence[str]], taken_ids: Iterable[str] = ()
) -> str:
    """Guess the client prefix for a new invoice from the covered intervals.

    Takes the tags **common to all** ``tag_sets``, drops workflow tags
    (:data:`WORKFLOW_TAGS`, case-insensitive) and anything that is an existing
    invoice ID (``taken_ids``, e.g. a previous invoice's tag). Exactly one tag
    surviving means an unambiguous client; zero or several means no guess
    (returns ``""`` and the user types the prefix themselves).
    """
    sets = [set(ts) for ts in tag_sets]
    if not sets:
        return ""
    common = set.intersection(*sets)
    taken = set(taken_ids)
    candidates = {
        t for t in common if t.lower() not in WORKFLOW_TAGS and t not in taken
    }
    if len(candidates) == 1:
        return candidates.pop()
    return ""


def next_invoice_id(invoices: Sequence[Invoice], client: str, today: date) -> str:
    """Next free ``{client}-{year}-{seq:03d}`` ID (pure: unit-tested).

    The sequence is scoped per client per year and takes ``max + 1`` over the
    existing IDs matching that scope (never ``count + 1``), so deleting an
    invoice can never produce a duplicate. An empty ``client`` yields e.g.
    ``-2026-001`` — a stub the user completes in the (always editable) ID field.
    """
    prefix = f"{client}-{today.year}-"
    pattern = re.compile(re.escape(prefix) + r"(\d+)$")
    seqs = [int(m.group(1)) for inv in invoices if (m := pattern.match(inv.id))]
    return f"{prefix}{max(seqs, default=0) + 1:03d}"


def paid_transitions(
    before: Mapping[str, str], after: Sequence[Invoice]
) -> tuple[list[str], list[str]]:
    """Invoice IDs whose status crossed the "paid" boundary (pure: unit-tested).

    ``before`` maps invoice ID -> status as snapshotted before an edit session;
    ``after`` is the ledger afterwards. Returns ``(newly_paid, reopened)``:
    IDs that became fully paid (their intervals get the ``invoiced -> paid`` tag
    swap) and IDs that left "paid" (a refund reopened the balance; the swap is
    reversed). Invoices deleted from the ledger appear in neither list —
    deletion never touches interval tags. Order follows ``after``.
    """
    newly_paid: list[str] = []
    reopened: list[str] = []
    for inv in after:
        old = before.get(inv.id)
        if old is None:
            continue  # added during the session (not possible today, but safe)
        if inv.status == "paid" and old != "paid":
            newly_paid.append(inv.id)
        elif inv.status != "paid" and old == "paid":
            reopened.append(inv.id)
    return newly_paid, reopened


# --------------------------------------------------------------------------- #
# Serialization (pure)
# --------------------------------------------------------------------------- #
def dumps(invoices: Sequence[Invoice]) -> str:
    """Serialize the ledger to JSON text (pure inverse of :func:`loads`)."""
    return json.dumps({"invoices": [asdict(inv) for inv in invoices]}, indent=2) + "\n"


def loads(text: str) -> list[Invoice]:
    """Parse ledger JSON text back into :class:`Invoice` objects.

    Entries that are not objects or have no ``id`` are skipped (a partially
    hand-edited ledger loses the bad entry, not everything); malformed JSON or
    malformed field values raise ``ValueError`` / ``TypeError``, which
    :func:`load_ledger` turns into an empty ledger.
    """
    data = json.loads(text)
    raw = data.get("invoices", []) if isinstance(data, dict) else []
    out: list[Invoice] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        payments = [
            Payment(
                date=str(p.get("date", "")),
                amount=float(p.get("amount", 0)),
                note=str(p.get("note", "")),
            )
            for p in (item.get("payments") or [])
            if isinstance(p, dict)
        ]
        out.append(
            Invoice(
                id=str(item["id"]),
                date=str(item.get("date", "")),
                hours=float(item.get("hours", 0)),
                rate=float(item.get("rate", 0)),
                amount=float(item.get("amount", 0)),
                currency=str(item.get("currency", "USD")),
                payments=payments,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Ledger location (pure resolution, thin impure wrapper)
# --------------------------------------------------------------------------- #
def resolve_ledger_dir(
    db: str | None, env_db: str | None, home: Path, legacy_exists: bool
) -> Path:
    """Directory the ledger lives in (pure: unit-tested).

    Mirrors how timew itself finds its database: an explicit db dir
    (``--timew-dir`` / config) wins, then an inherited ``TIMEWARRIORDB``, then
    timew's defaults — ``~/.timewarrior`` when that legacy dir exists
    (``legacy_exists``), else ``~/.local/share/timewarrior``.
    """
    if db:
        return Path(db).expanduser()
    if env_db:
        return Path(env_db).expanduser()
    legacy = home / ".timewarrior"
    return legacy if legacy_exists else home / ".local" / "share" / "timewarrior"


def ledger_path() -> Path:
    """Resolved path of ``invoices.json`` (impure: reads env + checks a dir)."""
    from . import timew

    home = Path.home()
    return (
        resolve_ledger_dir(
            timew.TIMEW_DB,
            os.environ.get("TIMEWARRIORDB"),
            home,
            (home / ".timewarrior").is_dir(),
        )
        / LEDGER_FILENAME
    )


# --------------------------------------------------------------------------- #
# Ledger IO (impure)
# --------------------------------------------------------------------------- #
def load_ledger(path: str | Path) -> list[Invoice]:
    """Read the ledger; a missing or corrupt file yields an empty ledger.

    Never raises — like the brand-config loader, a bad file must not crash the
    app (recording a new invoice would then overwrite the corrupt file with a
    fresh valid ledger).
    """
    try:
        return loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []


def save_ledger(path: str | Path, invoices: Sequence[Invoice]) -> None:
    """Write the ledger atomically (write a temp file, then rename over).

    The rename means a crash mid-write can never truncate an existing ledger.
    Creates the parent directory if needed. Raises ``OSError`` on failure (the
    caller surfaces it; payment data must never vanish silently).
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(dumps(invoices), encoding="utf-8")
    os.replace(tmp, out)
