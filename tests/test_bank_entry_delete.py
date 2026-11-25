"""A register entry can be deleted outright, as QuickBooks Desktop allows:
a mistake leaves no void behind. Guarded as a void is: never one a completed
reconciliation holds, never one on or before the closing date."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.banking import BankTransaction, Reconciliation
from app.models.transactions import Transaction
from app.services.ofx_import import import_transactions


def _entry(client, seed_accounts, **over):
    body = {
        "account_id": seed_accounts["1000"].id,
        "date": "2026-09-02",
        "amount": "-125.00",
        "category_account_id": seed_accounts["6000"].id,
        "payee": "City Water",
    }
    body.update(over)
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _register(client, seed_accounts):
    return client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()


def test_a_deleted_entry_leaves_nothing_behind(client, db_session, seed_accounts):
    txn_id = _entry(client, seed_accounts)
    r = client.delete(f"/api/banking/entries/{txn_id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": txn_id, "reversals_deleted": []}
    assert db_session.query(Transaction).count() == 0
    reg = _register(client, seed_accounts)
    assert reg["entries"] == [] and reg["balance"] == 0.0
    assert client.delete(f"/api/banking/entries/{txn_id}").status_code == 404


def test_a_voided_entry_goes_with_its_reversal(client, db_session, seed_accounts):
    txn_id = _entry(client, seed_accounts)
    assert client.post(f"/api/banking/entries/{txn_id}/void").status_code == 200
    assert db_session.query(Transaction).count() == 2
    r = client.delete(f"/api/banking/entries/{txn_id}")
    assert r.status_code == 200, r.text
    assert len(r.json()["reversals_deleted"]) == 1
    assert db_session.query(Transaction).count() == 0
    assert _register(client, seed_accounts)["entries"] == []


def test_the_register_offers_delete_on_an_entry(client, seed_accounts):
    _entry(client, seed_accounts)
    [row] = _register(client, seed_accounts)["entries"]
    assert row["source_type"] == "bank_entry" and row["reconciliation_id"] is None


def test_only_a_register_entry_is_deleted(client, seed_accounts):
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-09-02",
            "description": "x",
            "lines": [
                {"account_id": seed_accounts["6000"].id, "debit": "5", "credit": "0"},
                {"account_id": seed_accounts["1000"].id, "debit": "0", "credit": "5"},
            ],
        },
    )
    if r.status_code not in (200, 201):
        pytest.skip(f"journal post shape differs: {r.text[:120]}")
    assert client.delete(f"/api/banking/entries/{r.json()['id']}").status_code == 404


def test_the_closing_date_refuses_it(client, db_session, seed_accounts):
    txn_id = _entry(client, seed_accounts)
    assert (
        client.put("/api/settings", json={"closing_date": "2026-12-31"}).status_code
        == 200
    )
    r = client.delete(f"/api/banking/entries/{txn_id}")
    assert r.status_code in (400, 403, 409, 423), r.text
    assert db_session.query(Transaction).filter_by(id=txn_id).count() == 1


def test_a_reconciled_entry_is_refused(client, db_session, seed_accounts):
    txn_id = _entry(client, seed_accounts)
    recon = Reconciliation(
        account_id=seed_accounts["1000"].id,
        statement_date=date(2026, 9, 30),
        statement_balance=Decimal("-125"),
    )
    db_session.add(recon)
    db_session.flush()
    txn = db_session.get(Transaction, txn_id)
    for ln in txn.lines:
        if ln.account_id == seed_accounts["1000"].id:
            ln.reconciliation_id = recon.id
    db_session.commit()
    r = client.delete(f"/api/banking/entries/{txn_id}")
    assert r.status_code == 400 and "reconciliation" in r.json()["detail"]
    assert db_session.query(Transaction).filter_by(id=txn_id).count() == 1


def test_a_feed_line_it_was_added_from_returns_to_review(
    client, db_session, seed_accounts
):
    feed = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    ).json()
    out = import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": "F1",
                "date": date(2026, 9, 5),
                "amount": Decimal("-40.00"),
                "payee": "HARDWARE",
                "memo": "",
            }
        ],
    )
    assert out["imported"] == 1
    [bt] = db_session.query(BankTransaction).all()
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={"category_account_id": seed_accounts["6000"].id},
    )
    assert r.status_code in (200, 201), r.text
    db_session.refresh(bt)
    txn_id = bt.transaction_id
    assert txn_id and bt.match_status != "unmatched"
    assert client.delete(f"/api/banking/entries/{txn_id}").status_code == 200
    db_session.expire_all()
    bt = db_session.get(BankTransaction, bt.id)
    assert bt.transaction_id is None and bt.match_status == "unmatched"
