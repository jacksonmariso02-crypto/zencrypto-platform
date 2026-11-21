from decimal import Decimal

import pytest

from paper_ledger import (
    InsufficientPaperFunds,
    LedgerIdempotencyConflict,
    PaperLedger,
)


@pytest.fixture
def ledger(tmp_path):
    return PaperLedger(str(tmp_path / "ledger.sqlite3"))


def test_credit_creates_balanced_double_entry_and_exact_balance(ledger):
    ledger.create_account("alice_001")
    credit = ledger.credit_paper_funds("alice_001", "USDT", "100.00000001", "credit-usdt-0001")

    balances = ledger.get_balances("alice_001")
    assert balances == [{"asset": "USDT", "available": "100.00000001", "locked": "0"}]

    entries = ledger.list_entries("alice_001")
    assert len(entries) == 1
    assert entries[0]["account_code"] == "user:alice_001:available"
    assert entries[0]["credit"] == "100.00000001"

    import sqlite3
    db = sqlite3.connect(ledger.db_path)
    rows = db.execute(
        "SELECT debit, credit FROM ledger_entries WHERE transaction_id=?",
        (credit["transaction_id"],),
    ).fetchall()
    db.close()
    assert sum(Decimal(x[0]) for x in rows) == sum(Decimal(x[1]) for x in rows)


def test_credit_is_idempotent_and_conflicting_reuse_is_rejected(ledger):
    ledger.create_account("alice_001")
    first = ledger.credit_paper_funds("alice_001", "USDT", "50", "credit-retry-0001")
    replay = ledger.credit_paper_funds("alice_001", "USDT", "50", "credit-retry-0001")
    assert replay["transaction_id"] == first["transaction_id"]
    assert ledger.get_balances("alice_001")[0]["available"] == "50"

    with pytest.raises(LedgerIdempotencyConflict):
        ledger.credit_paper_funds("alice_001", "USDT", "51", "credit-retry-0001")


def test_bucket_transfer_locks_and_releases_funds(ledger):
    ledger.create_account("bob_002")
    ledger.credit_paper_funds("bob_002", "USDT", "125.50", "bob-credit-0001")

    locked = ledger.transfer(
        "bob_002", "USDT", "25.25", "available", "locked",
        "bob-lock-000001", "Reserve for open paper order",
    )
    replay = ledger.transfer(
        "bob_002", "USDT", "25.25", "available", "locked",
        "bob-lock-000001", "Reserve for open paper order",
    )
    assert replay["transaction_id"] == locked["transaction_id"]
    assert ledger.get_balances("bob_002") == [
        {"asset": "USDT", "available": "100.25", "locked": "25.25"}
    ]

    ledger.transfer(
        "bob_002", "USDT", "25.25", "locked", "available",
        "bob-unlock-0001", "Release cancelled order reserve",
    )
    assert ledger.get_balances("bob_002") == [
        {"asset": "USDT", "available": "125.50", "locked": "0.00"}
    ]


def test_insufficient_funds_do_not_create_ledger_transaction(ledger):
    ledger.create_account("carol_003")
    ledger.credit_paper_funds("carol_003", "BTC", "0.01", "carol-credit-001")
    with pytest.raises(InsufficientPaperFunds):
        ledger.transfer(
            "carol_003", "BTC", "0.02", "available", "locked",
            "carol-lock-000001",
        )
    assert ledger.get_balances("carol_003") == [
        {"asset": "BTC", "available": "0.01", "locked": "0"}
    ]
    assert len(ledger.list_entries("carol_003")) == 1


def test_accounts_are_isolated_and_invalid_inputs_rejected(ledger):
    ledger.create_account("dave_004")
    ledger.create_account("erin_005")
    ledger.credit_paper_funds("dave_004", "USDT", "10", "dave-credit-0001")
    assert ledger.get_balances("erin_005") == []

    with pytest.raises(ValueError):
        ledger.credit_paper_funds("dave_004", "USDT", "NaN", "dave-credit-0002")
    with pytest.raises(ValueError):
        ledger.transfer(
            "dave_004", "USDT", "1", "available", "available", "bad-transfer-001"
        )
