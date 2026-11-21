from decimal import Decimal

import pytest

from paper_engine import PaperMatchingEngine
from paper_ledger import PaperLedger


def setup_fee_engine(tmp_path, maker_bps=10, taker_bps=20):
    path = str(tmp_path / "fee-paper.sqlite3")
    return (
        PaperMatchingEngine(path, maker_fee_bps=maker_bps, taker_fee_bps=taker_bps),
        PaperLedger(path),
    )


def test_maker_seller_and_taker_buyer_fees_post_to_platform_ledger(tmp_path):
    engine, ledger = setup_fee_engine(tmp_path)
    ledger.create_account("fee_seller_01")
    ledger.create_account("fee_buyer_01")
    ledger.credit_paper_funds("fee_seller_01", "BTC", "1", "fee-seller-btc-01")
    ledger.credit_paper_funds("fee_buyer_01", "USDT", "500", "fee-buyer-usdt-01")

    ask = engine.place_limit_order(
        "BTC/USDT", "sell", "1", "100", "fee-maker-ask-01", "fee_seller_01"
    )
    bid = engine.place_limit_order(
        "BTC/USDT", "buy", "1", "110", "fee-taker-bid-01", "fee_buyer_01"
    )
    assert bid["status"] == "filled"

    trade = engine.list_trades("BTC/USDT")[0]
    assert trade["maker_fee_amount"] == "0.1"
    assert trade["maker_fee_asset"] == "USDT"
    assert trade["taker_fee_amount"] == "0.002"
    assert trade["taker_fee_asset"] == "BTC"

    assert ledger.get_balances("fee_seller_01") == [
        {"asset": "USDT", "available": "99.9", "locked": "0"}
    ]
    assert ledger.get_balances("fee_buyer_01") == [
        {"asset": "BTC", "available": "0.998", "locked": "0"},
        {"asset": "USDT", "available": "400", "locked": "0"},
    ]
    assert ledger.get_fee_balances() == [
        {"asset": "BTC", "amount": "0.002"},
        {"asset": "USDT", "amount": "0.1"},
    ]


def test_maker_buyer_and_taker_seller_fees_post_in_correct_assets(tmp_path):
    engine, ledger = setup_fee_engine(tmp_path)
    ledger.create_account("fee_maker_buyer")
    ledger.create_account("fee_taker_seller")
    ledger.credit_paper_funds("fee_maker_buyer", "USDT", "500", "maker-buyer-usdt")
    ledger.credit_paper_funds("fee_taker_seller", "BTC", "1", "taker-seller-btc")

    engine.place_limit_order(
        "BTC/USDT", "buy", "1", "110", "fee-maker-bid-01", "fee_maker_buyer"
    )
    ask = engine.place_limit_order(
        "BTC/USDT", "sell", "1", "100", "fee-taker-ask-01", "fee_taker_seller"
    )
    assert ask["status"] == "filled"

    trade = engine.list_trades("BTC/USDT")[0]
    assert trade["maker_fee_amount"] == "0.001"
    assert trade["maker_fee_asset"] == "BTC"
    assert trade["taker_fee_amount"] == "0.22"
    assert trade["taker_fee_asset"] == "USDT"
    assert ledger.get_balances("fee_maker_buyer") == [
        {"asset": "BTC", "available": "0.999", "locked": "0"},
        {"asset": "USDT", "available": "390", "locked": "0"},
    ]
    assert ledger.get_balances("fee_taker_seller") == [
        {"asset": "USDT", "available": "109.78", "locked": "0"}
    ]
    assert ledger.get_fee_balances() == [
        {"asset": "BTC", "amount": "0.001"},
        {"asset": "USDT", "amount": "0.22"},
    ]


def test_zero_fee_rates_preserve_existing_settlement_behavior(tmp_path):
    engine, ledger = setup_fee_engine(tmp_path, maker_bps=0, taker_bps=0)
    ledger.create_account("zero_fee_buyer")
    ledger.create_account("zero_fee_seller")
    ledger.credit_paper_funds("zero_fee_buyer", "USDT", "200", "zero-buyer-fund")
    ledger.credit_paper_funds("zero_fee_seller", "BTC", "1", "zero-seller-fund")

    engine.place_limit_order(
        "BTC/USDT", "sell", "1", "100", "zero-fee-ask-001", "zero_fee_seller"
    )
    engine.place_limit_order(
        "BTC/USDT", "buy", "1", "100", "zero-fee-bid-001", "zero_fee_buyer"
    )
    assert ledger.get_balances("zero_fee_buyer") == [
        {"asset": "BTC", "available": "1", "locked": "0"},
        {"asset": "USDT", "available": "100", "locked": "0"},
    ]
    assert ledger.get_fee_balances() == []
    trade = engine.list_trades("BTC/USDT")[0]
    assert trade["maker_fee_amount"] == "0"
    assert trade["taker_fee_amount"] == "0"


def test_fee_rates_are_bounded_to_ten_percent(tmp_path):
    with pytest.raises(ValueError, match="between 0 and 1000"):
        PaperMatchingEngine(str(tmp_path / "invalid-fee.sqlite3"), maker_fee_bps=1001)
    with pytest.raises(ValueError, match="between 0 and 1000"):
        PaperMatchingEngine(str(tmp_path / "invalid-fee2.sqlite3"), taker_fee_bps=-1)


def test_admin_fee_endpoint_reports_only_platform_fee_account(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import main

    path = str(tmp_path / "fee-api.sqlite3")
    monkeypatch.setattr(main, "ADMIN_TOKEN", "fee-admin-token")
    monkeypatch.setattr(
        main, "PAPER_ENGINE",
        PaperMatchingEngine(path, maker_fee_bps=10, taker_fee_bps=20),
    )
    monkeypatch.setattr(main, "PAPER_LEDGER", PaperLedger(path))
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer fee-admin-token"}

    for account in ("endpoint_seller", "endpoint_buyer"):
        response = client.post(
            "/api/v1/paper/accounts", headers=headers, json={"account_id": account}
        )
        assert response.status_code == 200
    client.post(
        "/api/v1/paper/accounts/endpoint_seller/credit",
        headers=headers,
        json={"asset": "BTC", "amount": "1", "idempotency_key": "endpoint-seller-btc"},
    )
    client.post(
        "/api/v1/paper/accounts/endpoint_buyer/credit",
        headers=headers,
        json={"asset": "USDT", "amount": "200", "idempotency_key": "endpoint-buyer-usdt"},
    )
    client.post(
        "/api/v1/paper/accounts/endpoint_seller/orders",
        headers=headers,
        json={
            "symbol": "BTC/USDT", "side": "sell", "quantity": "1",
            "price": "100", "client_order_id": "endpoint-seller-ask",
        },
    )
    client.post(
        "/api/v1/paper/accounts/endpoint_buyer/orders",
        headers=headers,
        json={
            "symbol": "BTC/USDT", "side": "buy", "quantity": "1",
            "price": "100", "client_order_id": "endpoint-buyer-bid",
        },
    )

    fees = client.get("/api/v1/paper/fees", headers=headers)
    assert fees.status_code == 200
    assert fees.json() == [
        {"asset": "BTC", "amount": "0.002"},
        {"asset": "USDT", "amount": "0.1"},
    ]
    assert client.get("/api/v1/paper/fees").status_code == 401
