import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import main


@pytest.mark.parametrize(
    "path,method",
    [
        ("/auth/register", "post"),
        ("/auth/login", "post"),
        ("/account/balances", "get"),
        ("/trade/orders", "get"),
        ("/sandbox/faucet", "post"),
        ("/sandbox/deposits", "post"),
        ("/sandbox/withdrawal-requests", "post"),
        ("/deposits/request", "post"),
        ("/withdrawals/request", "post"),
        ("/kyc/submit", "post"),
        ("/compliance/status", "get"),
        ("/admin/audit", "get"),
    ],
)
def test_legacy_simulation_and_mock_account_routes_are_retired(tmp_path, monkeypatch, path, method):
    monkeypatch.setattr(main, "DB", str(tmp_path / "retired.db"))
    monkeypatch.setenv("ENABLE_PAPER_TRADING", "true")
    main.init()
    with TestClient(main.app) as client:
        response = getattr(client, method)(path, json={} if method == "post" else None) if method == "post" else getattr(client, method)(path)
    assert response.status_code == 410
    assert "retired" in response.json()["detail"].lower()


def test_dex_quote_proxy_is_disabled_without_server_side_api_key(monkeypatch):
    monkeypatch.setenv("ENABLE_MAINNET_SWAPS", "true")
    monkeypatch.setenv("MAINNET_SWAP_RELEASE_APPROVED", "true")
    monkeypatch.delenv("ZEROX_API_KEY", raising=False)
    with TestClient(main.app) as client:
        response = client.get("/dex/price", params={
            "chainId": 8453,
            "sellToken": "0x4200000000000000000000000000000000000006",
            "buyToken": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "sellAmount": "100000000000000000",
            "taker": "0x0000000000000000000000000000000000000001",
        })
    assert response.status_code == 503
    assert "ZEROX_API_KEY" in response.json()["detail"]


def test_dex_quote_proxy_rejects_unapproved_tokens_before_provider_call(monkeypatch):
    monkeypatch.setenv("ENABLE_MAINNET_SWAPS", "true")
    monkeypatch.setenv("MAINNET_SWAP_RELEASE_APPROVED", "true")
    monkeypatch.setenv("ZEROX_API_KEY", "test-key")
    with TestClient(main.app) as client:
        response = client.get("/dex/price", params={
            "chainId": 8453,
            "sellToken": "0x0000000000000000000000000000000000000001",
            "buyToken": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "sellAmount": "1000000",
            "taker": "0x0000000000000000000000000000000000000001",
        })
    assert response.status_code == 422
    assert "curated WETH/USDC" in response.json()["detail"]


def test_mainnet_swap_flag_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("ENABLE_MAINNET_SWAPS", raising=False)
    with pytest.raises(HTTPException) as exc:
        main.validate_dex_request(
            8453,
            "0x4200000000000000000000000000000000000006",
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "1000000",
            "0x0000000000000000000000000000000000000001",
        )
    assert exc.value.status_code == 503


def test_dex_quote_response_rejects_wrong_asset_and_calldata():
    sell = "0x4200000000000000000000000000000000000006"
    buy = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    taker = "0x0000000000000000000000000000000000000001"
    good = {
        "liquidityAvailable": True,
        "sellToken": sell, "buyToken": buy, "sellAmount": "1000000", "buyAmount": "999", "minBuyAmount": "989", "issues": {},
        "transaction": {"from": taker, "to": "0x0000000000000000000000000000000000000002",
                        "data": "0x12345678", "value": "0"},
    }

    def check(payload):
        return main.validate_dex_quote_response(payload, chain_id=8453, sell_token=sell,
                                                buy_token=buy, sell_amount="1000000", taker=taker)

    assert check(good) == good
    for changed in (
        {"sellToken": buy},
        {"buyToken": sell},
        {"sellAmount": "999999"},
        {"buyAmount": "0"},
        {"minBuyAmount": "1000"},
        {"minBuyAmount": "0"},
        {"issues": {"simulationIncomplete": True}},
        {"issues": {"invalidSourcesPassed": ["BadPool"]}},
        {"issues": {"balance": {"actual": "0", "expected": "1000000"}}},
        {"transaction": {**good["transaction"], "data": "0xgarbage"}},
        {"transaction": {**good["transaction"], "value": "0x1"}},
        {"transaction": {**good["transaction"], "from": "0x0000000000000000000000000000000000000003"}},
        {"issues": {"allowance": {"spender": "0x0000000000000000000000000000000000000004"}}},
    ):
        with pytest.raises(HTTPException):
            check({**good, **changed})


def test_readiness_requires_real_backend_configuration(monkeypatch):
    monkeypatch.setenv("ZEROX_API_KEY", "test-provider-key")
    monkeypatch.setenv("CORS_ORIGINS", "https://zencrypto.example")
    monkeypatch.setenv("ENABLE_MAINNET_SWAPS", "false")
    monkeypatch.setenv("APP_SECRET", "test-secret-that-is-not-for-production")
    with TestClient(main.app) as client:
        live = client.get("/health")
        ready = client.get("/ready")
    assert live.status_code == 200
    assert live.json()["service"] == "zencrypto-noncustodial-api"
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert "not a legal" in ready.json()["notice"]


def test_readiness_fails_closed_without_provider_key(monkeypatch):
    monkeypatch.delenv("ZEROX_API_KEY", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://zencrypto.example")
    monkeypatch.setenv("ENABLE_MAINNET_SWAPS", "false")
    monkeypatch.setenv("APP_SECRET", "test-secret")
    with TestClient(main.app) as client:
        ready = client.get("/ready")
    assert ready.status_code == 503
    assert ready.json()["ready"] is False
    assert ready.json()["checks"]["dex_provider_key_configured"] is False


def test_dex_quote_request_rejects_extra_fields_and_bad_addresses():
    base = {
        "chainId": 8453,
        "sellToken": "0x4200000000000000000000000000000000000006",
        "buyToken": "0x833589fcd6edb6e08f4c7c32d4f71b54bdA02913",
        "sellAmount": "1000000",
        "taker": "0x0000000000000000000000000000000000000001",
    }
    assert main.DexQuoteRequest(**base).chainId == 8453
    with pytest.raises(ValidationError):
        main.DexQuoteRequest(**{**base, "taker": "0x123"})
    with pytest.raises(ValidationError):
        main.DexQuoteRequest(**{**base, "sellAmount": "1e9"})
    with pytest.raises(ValidationError):
        main.DexQuoteRequest(**{**base, "unexpected": "field"})
