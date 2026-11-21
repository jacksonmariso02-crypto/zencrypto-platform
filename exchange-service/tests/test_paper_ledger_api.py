from fastapi.testclient import TestClient

import main
from paper_ledger import PaperLedger


def auth():
    return {"Authorization": "Bearer paper-test-token"}


def test_paper_ledger_api_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "ADMIN_TOKEN", "paper-test-token")
    monkeypatch.setattr(main, "PAPER_LEDGER", PaperLedger(str(tmp_path / "api-ledger.sqlite3")))
    client = TestClient(main.app)

    created = client.post(
        "/api/v1/paper/accounts", headers=auth(), json={"account_id": "api_user_01"}
    )
    assert created.status_code == 200

    credited = client.post(
        "/api/v1/paper/accounts/api_user_01/credit",
        headers=auth(),
        json={"asset": "USDT", "amount": "100", "idempotency_key": "api-credit-0001"},
    )
    assert credited.status_code == 200

    locked = client.post(
        "/api/v1/paper/accounts/api_user_01/bucket-transfer",
        headers=auth(),
        json={
            "asset": "USDT", "amount": "30", "from_bucket": "available",
            "to_bucket": "locked", "idempotency_key": "api-lock-000001",
            "memo": "Test reservation",
        },
    )
    assert locked.status_code == 200

    balances = client.get(
        "/api/v1/paper/accounts/api_user_01/balances", headers=auth()
    )
    assert balances.status_code == 200
    assert balances.json() == [{"asset": "USDT", "available": "70", "locked": "30"}]

    entries = client.get(
        "/api/v1/paper/accounts/api_user_01/ledger", headers=auth()
    )
    assert entries.status_code == 200
    assert len(entries.json()) == 3  # credit line plus both sides of the bucket transfer

    insufficient = client.post(
        "/api/v1/paper/accounts/api_user_01/bucket-transfer",
        headers=auth(),
        json={
            "asset": "USDT", "amount": "100", "from_bucket": "available",
            "to_bucket": "locked", "idempotency_key": "api-lock-fail-0001",
        },
    )
    assert insufficient.status_code == 409


def test_paper_ledger_api_rejects_unauthorized_and_unknown_account(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "ADMIN_TOKEN", "paper-test-token")
    monkeypatch.setattr(main, "PAPER_LEDGER", PaperLedger(str(tmp_path / "api-ledger-2.sqlite3")))
    client = TestClient(main.app)

    unauthorized = client.get("/api/v1/paper/accounts/missing_01/balances")
    assert unauthorized.status_code == 401

    missing = client.get(
        "/api/v1/paper/accounts/missing_01/balances", headers=auth()
    )
    assert missing.status_code == 404
