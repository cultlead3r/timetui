"""Unit tests for the invoice ledger (model, ID scheme, serialization, IO).

The IO tests only ever touch ``tmp_path`` — the ledger normally lives next to
the real Time Warrior database, which tests must never read or write (see
``conftest._isolate_invoice_ledger``).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from timetui import invoices
from timetui.invoices import Invoice, Payment


def make_invoice(**overrides) -> Invoice:
    base = dict(
        id="LA-2026-001", date="2026-07-10", hours=6.5, rate=200.0,
        amount=1300.0, currency="USD",
    )
    base.update(overrides)
    return Invoice(**base)


# --------------------------------------------------------------------------- #
# Balance / status math
# --------------------------------------------------------------------------- #
def test_unpaid_invoice():
    inv = make_invoice()
    assert inv.paid == 0.0
    assert inv.balance == 1300.0
    assert inv.status == "unpaid"


def test_partial_payment():
    inv = make_invoice(payments=[Payment(date="2026-07-12", amount=500.0)])
    assert inv.paid == 500.0
    assert inv.balance == 800.0
    assert inv.status == "partial"


def test_multiple_payments_accumulate():
    inv = make_invoice(payments=[
        Payment(date="2026-07-12", amount=500.0),
        Payment(date="2026-08-01", amount=800.0, note="wire ref 123"),
    ])
    assert inv.balance == 0.0
    assert inv.status == "paid"


def test_overpaid_is_paid():
    inv = make_invoice(payments=[Payment(date="2026-07-12", amount=1500.0)])
    assert inv.balance == -200.0
    assert inv.status == "paid"


def test_float_dust_within_epsilon_counts_as_paid():
    # hours × rate float dust must never leave a fully-paid invoice "partial"
    inv = make_invoice(payments=[Payment(date="2026-07-12", amount=1299.999)])
    assert inv.status == "paid"


def test_negative_payment_refund_reopens_balance():
    inv = make_invoice(payments=[
        Payment(date="2026-07-12", amount=1300.0),
        Payment(date="2026-07-20", amount=-300.0, note="refund"),
    ])
    assert inv.balance == 300.0
    assert inv.status == "partial"


def test_zero_amount_invoice_is_paid():
    assert make_invoice(amount=0.0).status == "paid"


# --------------------------------------------------------------------------- #
# Client derivation
# --------------------------------------------------------------------------- #
def test_derive_client_single_common_tag():
    assert invoices.derive_client([["LA", "new"], ["new", "LA"]]) == "LA"


def test_derive_client_drops_workflow_tags_case_insensitively():
    assert invoices.derive_client([["Kush", "New"], ["Kush", "invoiced"]]) == "Kush"


def test_derive_client_mixed_clients_is_ambiguous():
    assert invoices.derive_client([["LA", "new"], ["B", "new"]]) == ""


def test_derive_client_multiple_candidates_is_ambiguous():
    assert invoices.derive_client([["LA", "crypto"], ["LA", "crypto"]]) == ""


def test_derive_client_ignores_existing_invoice_id_tags():
    tag_sets = [["LA", "LA-2026-001"], ["LA", "LA-2026-001"]]
    assert invoices.derive_client(tag_sets, taken_ids={"LA-2026-001"}) == "LA"


def test_derive_client_empty_inputs():
    assert invoices.derive_client([]) == ""
    assert invoices.derive_client([["new"], ["new"]]) == ""


def test_derive_client_drops_expense_bookkeeping_tags():
    # An expense-only report must still guess the client: the `expense` marker
    # and per-item `cost:` tags are never client candidates even when common to
    # every set (a single expense, or several with the same amount).
    tag_sets = [["LA", "expense", "cost:450.00", "new"],
                ["LA", "expense", "cost:450.00", "new"]]
    assert invoices.derive_client(tag_sets) == "LA"
    # The marker is dropped case-insensitively, like the workflow tags.
    assert invoices.derive_client([["LA", "Expense"], ["LA", "Expense"]]) == "LA"


def test_derive_client_drops_paid_workflow_tag():
    # backfilling already-settled work ({LA, paid}) still derives the client
    assert invoices.derive_client([["LA", "paid"], ["LA", "paid"]]) == "LA"


# --------------------------------------------------------------------------- #
# Paid-status transitions (drives the invoiced <-> paid tag swap)
# --------------------------------------------------------------------------- #
def paid_invoice(**overrides) -> Invoice:
    return make_invoice(
        payments=[Payment(date="2026-07-12", amount=1300.0)], **overrides
    )


def test_paid_transitions_newly_paid():
    before = {"LA-2026-001": "partial"}
    newly_paid, reopened = invoices.paid_transitions(before, [paid_invoice()])
    assert newly_paid == ["LA-2026-001"]
    assert reopened == []


def test_paid_transitions_reopened_by_refund():
    before = {"LA-2026-001": "paid"}
    refunded = make_invoice(payments=[
        Payment(date="2026-07-12", amount=1300.0),
        Payment(date="2026-07-20", amount=-300.0, note="refund"),
    ])
    newly_paid, reopened = invoices.paid_transitions(before, [refunded])
    assert newly_paid == []
    assert reopened == ["LA-2026-001"]


def test_paid_transitions_no_change():
    # staying paid / staying partial crosses no boundary
    before = {"LA-2026-001": "paid", "B-2026-001": "partial"}
    partial = make_invoice(
        id="B-2026-001", payments=[Payment(date="2026-07-12", amount=100.0)]
    )
    assert invoices.paid_transitions(before, [paid_invoice(), partial]) == ([], [])


def test_paid_transitions_ignores_deleted_and_added_invoices():
    # deleted from the ledger -> tags untouched; unknown-before ids are skipped
    before = {"LA-2026-001": "paid"}
    added = paid_invoice(id="B-2026-001")  # not in `before`
    assert invoices.paid_transitions(before, [added]) == ([], [])
    assert invoices.paid_transitions(before, []) == ([], [])


def test_paid_transitions_orders_by_after():
    before = {"A-2026-001": "unpaid", "B-2026-001": "unpaid"}
    after = [paid_invoice(id="B-2026-001"), paid_invoice(id="A-2026-001")]
    newly_paid, _ = invoices.paid_transitions(before, after)
    assert newly_paid == ["B-2026-001", "A-2026-001"]


# --------------------------------------------------------------------------- #
# Invoice-ID generation
# --------------------------------------------------------------------------- #
TODAY = date(2026, 7, 10)


def test_next_invoice_id_empty_ledger():
    assert invoices.next_invoice_id([], "LA", TODAY) == "LA-2026-001"


def test_next_invoice_id_increments_per_client_and_year():
    ledger = [
        make_invoice(id="LA-2026-001"),
        make_invoice(id="LA-2026-002"),
        make_invoice(id="B-2026-007"),      # other client: ignored
        make_invoice(id="LA-2025-009"),     # other year: ignored
    ]
    assert invoices.next_invoice_id(ledger, "LA", TODAY) == "LA-2026-003"
    assert invoices.next_invoice_id(ledger, "B", TODAY) == "B-2026-008"
    assert invoices.next_invoice_id(ledger, "Kush", TODAY) == "Kush-2026-001"


def test_next_invoice_id_takes_max_not_count():
    # a deleted invoice must never cause the next ID to collide
    ledger = [make_invoice(id="LA-2026-001"), make_invoice(id="LA-2026-005")]
    assert invoices.next_invoice_id(ledger, "LA", TODAY) == "LA-2026-006"


def test_next_invoice_id_ignores_edited_nonmatching_ids():
    ledger = [make_invoice(id="LA-2026-003b"), make_invoice(id="whatever")]
    assert invoices.next_invoice_id(ledger, "LA", TODAY) == "LA-2026-001"


def test_next_invoice_id_empty_client_yields_stub():
    assert invoices.next_invoice_id([], "", TODAY) == "-2026-001"


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def test_json_roundtrip():
    ledger = [
        make_invoice(payments=[Payment(date="2026-07-12", amount=500.0, note="wire")]),
        make_invoice(id="B-2026-001", currency="EUR", payments=[]),
    ]
    assert invoices.loads(invoices.dumps(ledger)) == ledger


def test_loads_skips_bad_entries():
    text = (
        '{"invoices": ["nope", {"no": "id"}, '
        '{"id": "LA-2026-001", "date": "2026-07-10", "hours": 1, '
        '"rate": 200, "amount": 200}]}'
    )
    ledger = invoices.loads(text)
    assert [inv.id for inv in ledger] == ["LA-2026-001"]
    assert ledger[0].payments == []
    assert ledger[0].currency == "USD"


def test_loads_non_object_root_is_empty():
    assert invoices.loads("[1, 2]") == []


# --------------------------------------------------------------------------- #
# Ledger IO (tmp_path only)
# --------------------------------------------------------------------------- #
def test_load_ledger_missing_file(tmp_path):
    assert invoices.load_ledger(tmp_path / "missing.json") == []


def test_load_ledger_corrupt_file(tmp_path):
    bad = tmp_path / "invoices.json"
    bad.write_text("{not json", encoding="utf-8")
    assert invoices.load_ledger(bad) == []


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "sub" / "invoices.json"  # parent dir is created
    ledger = [make_invoice(payments=[Payment(date="2026-07-12", amount=500.0)])]
    invoices.save_ledger(path, ledger)
    assert invoices.load_ledger(path) == ledger
    assert not path.with_name(path.name + ".tmp").exists()  # temp file renamed away


def test_save_ledger_overwrites_atomically(tmp_path):
    path = tmp_path / "invoices.json"
    invoices.save_ledger(path, [make_invoice()])
    invoices.save_ledger(path, [make_invoice(), make_invoice(id="LA-2026-002")])
    assert len(invoices.load_ledger(path)) == 2


# --------------------------------------------------------------------------- #
# Ledger location resolution (pure)
# --------------------------------------------------------------------------- #
HOME = Path("/home/u")


def test_resolve_ledger_dir_explicit_db_wins():
    got = invoices.resolve_ledger_dir("~/clients/acme/tw", "/env/tw", HOME, True)
    assert got == Path("~/clients/acme/tw").expanduser()


def test_resolve_ledger_dir_env_fallback():
    assert invoices.resolve_ledger_dir(None, "/env/tw", HOME, True) == Path("/env/tw")


def test_resolve_ledger_dir_legacy_default():
    got = invoices.resolve_ledger_dir(None, None, HOME, True)
    assert got == HOME / ".timewarrior"


def test_resolve_ledger_dir_xdg_default():
    got = invoices.resolve_ledger_dir(None, None, HOME, False)
    assert got == HOME / ".local" / "share" / "timewarrior"
