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


def test_tiny_decimal_balance_is_checked_without_float_rounding(ledger):
    ledger.create_account("tiny_006")
    ledger.credit_paper_funds(
        "tiny_006", "BTC", "0.00000000000000000003", "tiny-credit-0001"
    )
    ledger.transfer(
        "tiny_006", "BTC", "0.00000000000000000002",
        "available", "locked", "tiny-lock-000001",
    )
    assert ledger.get_balances("tiny_006") == [
        {
            "asset": "BTC",
            "available": "1E-20",
            "locked": "2E-20",
        }
    ]
    with pytest.raises(InsufficientPaperFunds):
        ledger.transfer(
            "tiny_006", "BTC", "0.0000000000000000000100000000001",
            "available", "locked", "tiny-lock-fail-01",
        )


def test_reconciliation_reports_balanced_transactions_and_nonnegative_buckets(ledger):
    ledger.create_account("audit_001")
    ledger.credit_paper_funds("audit_001", "USDT", "250.75", "audit-credit-001")
    ledger.transfer(
        "audit_001", "USDT", "50", "available", "locked",
        "audit-lock-0001", "Reserve open paper order",
    )
    result = ledger.audit_integrity()
    assert result["status"] == "ok"
    assert all(result["checks"].values())
    assert result["counts"]["transactions"] == 2
    assert result["counts"]["unbalanced_transactions"] == 0
    assert result["counts"]["negative_buckets"] == 0


def test_reconciliation_detects_tampered_unbalanced_transaction(ledger):
    import sqlite3

    ledger.create_account("audit_002")
    credit = ledger.credit_paper_funds(
        "audit_002", "USDT", "100", "audit-credit-002"
    )
    with sqlite3.connect(ledger.db_path) as db:
        db.execute(
            "UPDATE ledger_entries SET credit='99' "
            "WHERE transaction_id=? AND account_code=?",
            (credit["transaction_id"], "user:audit_002:available"),
        )

    result = ledger.audit_integrity()
    assert result["status"] == "attention_required"
    assert result["checks"]["transactions_balanced"] is False
    assert result["counts"]["unbalanced_transactions"] == 1
    assert result["issues"]["unbalanced_transactions"][0]["transaction_id"] == credit["transaction_id"]


def test_reconciliation_detects_negative_user_bucket(ledger):
    import sqlite3

    ledger.create_account("audit_003")
    with sqlite3.connect(ledger.db_path) as db:
        db.execute(
            """INSERT INTO ledger_transactions
               (id,idempotency_key,kind,memo,created_at)
               VALUES('manual-test-tx','manual-negative-001','test','test',1)"""
        )
        db.executemany(
            """INSERT INTO ledger_entries
               (transaction_id,account_code,asset,debit,credit)
               VALUES(?,?,?,?,?)""",
            [
                ("manual-test-tx", "user:audit_003:available", "USDT", "5", "0"),
                ("manual-test-tx", "system:test", "USDT", "0", "5"),
            ],
        )

    result = ledger.audit_integrity()
    assert result["status"] == "attention_required"
    assert result["checks"]["user_buckets_nonnegative"] is False
    assert result["counts"]["negative_buckets"] == 1


def test_daily_asset_snapshot_is_immutable_and_idempotent(ledger):
    ledger.create_account("snap_001")
    ledger.credit_paper_funds("snap_001", "USDT", "100.25", "snap-credit-001")
    first = ledger.capture_daily_snapshots()
    replay = ledger.capture_daily_snapshots()

    assert first["status"] == "captured"
    assert first["created"] == 1
    assert replay["created"] == 0
    assert replay["snapshot_date"] == first["snapshot_date"]

    ledger.transfer(
        "snap_001", "USDT", "25", "available", "locked",
        "snap-lock-0001", "Reserve after initial snapshot",
    )
    history = ledger.list_daily_snapshots("snap_001")
    assert len(history) == 1
    assert history[0]["available"] == "100.25"
    assert history[0]["locked"] == "0"


def test_daily_snapshot_captures_each_account_asset_separately(ledger):
    ledger.create_account("snap_002")
    ledger.create_account("snap_003")
    ledger.credit_paper_funds("snap_002", "USDT", "50", "snap2-usdt-001")
    ledger.credit_paper_funds("snap_002", "BTC", "0.5", "snap2-btc-001")
    ledger.credit_paper_funds("snap_003", "USDT", "75", "snap3-usdt-001")

    result = ledger.capture_daily_snapshots()
    rows = ledger.list_daily_snapshots(limit=20)
    assert result["created"] == 3
    assert {(r["account_id"], r["asset"]) for r in rows} == {
        ("snap_002", "USDT"), ("snap_002", "BTC"), ("snap_003", "USDT")
    }


def test_daily_snapshot_rejects_backdated_or_future_dates(ledger):
    with pytest.raises(ValueError, match="current UTC date"):
        ledger.capture_daily_snapshots("2000-01-01")
    with pytest.raises(ValueError, match="current UTC date"):
        ledger.capture_daily_snapshots("2999-01-01")


def test_snapshot_history_validates_account_and_limit(ledger):
    with pytest.raises(KeyError):
        ledger.list_daily_snapshots("missing_001")
    with pytest.raises(ValueError):
        ledger.list_daily_snapshots(limit=0)
