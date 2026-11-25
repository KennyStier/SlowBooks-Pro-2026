"""QuickFill: the register row fills a payee's category, class and amount
from that payee's last entry, as QuickBooks Desktop does."""


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


def _last(client, seed_accounts, payee):
    r = client.get(
        "/api/banking/payee-last",
        params={"payee": payee, "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_the_latest_entry_for_the_payee_answers(client, seed_accounts):
    _entry(client, seed_accounts, date="2026-08-02", amount="-100.00")
    newest = _entry(
        client, seed_accounts, date="2026-09-02", amount="-140.50",
        category_account_id=seed_accounts["6000"].id,
    )
    got = _last(client, seed_accounts, "  city WATER ")
    assert got["transaction_id"] == newest
    assert got["category_account_id"] == seed_accounts["6000"].id
    assert got["amount"] == "-140.50"


def test_money_in_keeps_its_sign(client, seed_accounts):
    _entry(
        client, seed_accounts, payee="Grant Fund", amount="500",
        category_account_id=seed_accounts["4000"].id,
    )
    got = _last(client, seed_accounts, "Grant Fund")
    assert got["amount"] == "500.00"
    assert got["category_account_id"] == seed_accounts["4000"].id


def test_an_unknown_payee_answers_nothing(client, seed_accounts):
    _entry(client, seed_accounts)
    assert _last(client, seed_accounts, "Nobody") == {}
    assert _last(client, seed_accounts, "City") == {}  # whole names only


def test_a_voided_entry_is_passed_over(client, seed_accounts):
    older = _entry(client, seed_accounts, date="2026-08-02", amount="-90.00")
    newest = _entry(client, seed_accounts, date="2026-09-02", amount="-999.00")
    assert client.post(f"/api/banking/entries/{newest}/void").status_code == 200
    got = _last(client, seed_accounts, "City Water")
    assert got["transaction_id"] == older and got["amount"] == "-90.00"
