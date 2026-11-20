import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", str(tmp_path / "zencrypto-test.db"))
    monkeypatch.setenv("APP_SECRET", "test-only-secret-that-is-long-enough-for-tests")
    monkeypatch.setenv("ENABLE_PAPER_TRADING", "false")
    with TestClient(main.app) as test_client:
        yield test_client


def test_health_is_liveness_only(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "X-Content-Type-Options" in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_readiness_fails_closed_without_required_secrets(client, monkeypatch):
    monkeypatch.delenv("ZEROX_API_KEY", raising=False)
    monkeypatch.delenv("APP_SECRET", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    response = client.get("/ready")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["dex_provider_key_configured"] is False
    assert checks["explicit_cors_origins_configured"] is False
    assert checks["legacy_simulation_routes_retired"] is True
    assert checks["mainnet_swaps_disabled_until_release_approval"] is True
    assert checks["persistent_app_secret_configured"] is False


@pytest.mark.parametrize(
    "params",
    [
        {"symbol": "UNKNOWN", "interval": "1h", "limit": 100},
        {"symbol": "BTCUSDT", "interval": "2m", "limit": 100},
        {"symbol": "BTCUSDT", "interval": "1h", "limit": 10},
        {"symbol": "BTCUSDT", "interval": "1h", "limit": 1001},
    ],
)
def test_candle_endpoint_rejects_unsupported_parameters(client, params):
    response = client.get("/market/candles", params=params)
    assert response.status_code == 422


@pytest.mark.parametrize("path,method", [
    ("/auth/login", "post"),
    ("/account/balances", "get"),
    ("/trade/orders", "get"),
    ("/sandbox/faucet", "post"),
    ("/deposits/request", "post"),
    ("/withdrawals/request", "post"),
])
def test_legacy_simulation_routes_are_permanently_retired(client, monkeypatch, path, method):
    monkeypatch.setenv("ENABLE_PAPER_TRADING", "true")
    response = client.post(path, json={}) if method == "post" else client.get(path)
    assert response.status_code == 410
    assert "retired" in response.json()["detail"].lower()


def test_swap_quote_validator_rejects_transaction_chain_mismatch():
    payload = {
        "liquidityAvailable": True,
        "sellToken": "0x" + "1" * 40,
        "buyToken": "0x" + "2" * 40,
        "sellAmount": "100",
        "buyAmount": "90",
        "minBuyAmount": "85",
        "issues": {},
        "transaction": {
            "from": "0x" + "3" * 40,
            "to": "0x" + "4" * 40,
            "data": "0x12345678",
            "value": "0",
            "chainId": 8453,
        },
    }
    with pytest.raises(main.HTTPException) as exc:
        main.validate_dex_quote_response(
            payload,
            chain_id=1,
            sell_token="0x" + "1" * 40,
            buy_token="0x" + "2" * 40,
            sell_amount="100",
            taker="0x" + "3" * 40,
        )
    assert exc.value.status_code == 502
    assert "chain" in exc.value.detail.lower()


def test_swap_quote_validator_accepts_matching_hex_transaction_chain_id():
    payload = {
        "liquidityAvailable": True,
        "sellToken": "0x" + "1" * 40,
        "buyToken": "0x" + "2" * 40,
        "sellAmount": "100",
        "buyAmount": "90",
        "minBuyAmount": "85",
        "issues": {},
        "transaction": {
            "from": "0x" + "3" * 40,
            "to": "0x" + "4" * 40,
            "data": "0x12345678",
            "value": "0",
            "chainId": "0x1",
        },
    }
    result = main.validate_dex_quote_response(
        payload,
        chain_id=1,
        sell_token="0x" + "1" * 40,
        buy_token="0x" + "2" * 40,
        sell_amount="100",
        taker="0x" + "3" * 40,
    )
    assert result is payload


def test_mainnet_swap_quotes_fail_closed_until_release_flag_is_set(client, monkeypatch):
    monkeypatch.delenv("ENABLE_MAINNET_SWAPS", raising=False)
    params = {
        "chainId": 1,
        "sellToken": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "buyToken": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "sellAmount": "10000000000000000",
        "taker": "0x" + "1" * 40,
    }
    response = client.get("/dex/price", params=params)
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"].lower()


def test_mainnet_swap_quote_gate_cannot_be_bypassed_by_paper_flag(client, monkeypatch):
    monkeypatch.setenv("ENABLE_PAPER_TRADING", "true")
    monkeypatch.setenv("ENABLE_MAINNET_SWAPS", "false")
    with pytest.raises(main.HTTPException) as exc:
        main.validate_dex_request(
            1,
            "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "10000000000000000",
            "0x" + "1" * 40,
        )
    assert exc.value.status_code == 503

def test_mainnet_swap_gate_requires_separate_release_approval(client, monkeypatch):
    monkeypatch.setenv("ENABLE_MAINNET_SWAPS", "true")
    monkeypatch.delenv("MAINNET_SWAP_RELEASE_APPROVED", raising=False)
    params = {
        "chainId": 1,
        "sellToken": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "buyToken": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eB48",
        "sellAmount": "10000000000000000",
        "taker": "0x" + "1" * 40,
    }
    response = client.get("/dex/price", params=params)
    assert response.status_code == 503
    assert "release approval" in response.json()["detail"].lower()


def test_mainnet_swap_gate_requires_execution_flag_even_if_release_approved(client, monkeypatch):
    monkeypatch.delenv("ENABLE_MAINNET_SWAPS", raising=False)
    monkeypatch.setenv("MAINNET_SWAP_RELEASE_APPROVED", "true")
    params = {
        "chainId": 1,
        "sellToken": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "buyToken": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eB48",
        "sellAmount": "10000000000000000",
        "taker": "0x" + "1" * 40,
    }
    response = client.get("/dex/price", params=params)
    assert response.status_code == 503


def test_candle_endpoint_normalizes_valid_binance_rows_without_network(client, monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return [
                [1700000000000, "100", "110", "95", "105", "12.5", 1700003599999, "1250", 42],
                ["bad-timestamp", "100", "110", "95", "105", "12.5", 1700003599999, "1250", 42],
                [1700003600000, "100", "110", "95", "105", "12.5", 1700007199999, "1250", 42],
            ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    response = client.get("/market/candles", params={"symbol": "btcusdt", "interval": "1h", "limit": 100})
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "BTCUSDT"
    assert payload["interval"] == "1h"
    assert payload["source"] == "Binance public market data"
    assert payload["provider"] == "Binance Spot REST API"
    assert payload["market"] == "SPOT"
    assert payload["candle_count"] == 2
    assert payload["candles"] == [
        {
            "time": 1700000000,
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 105.0,
            "volume": 12.5,
            "close_time": 1700003599,
            "quote_volume": 1250.0,
            "trades": 42,
            "is_closed": True,
        },
        {
            "time": 1700003600,
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 105.0,
            "volume": 12.5,
            "close_time": 1700007199,
            "quote_volume": 1250.0,
            "trades": 42,
            "is_closed": True,
        }
    ]


def test_chain_history_rejects_invalid_address_before_provider_call(client):
    response = client.get("/chain/history", params={"chainId": 1, "address": "not-an-address"})
    assert response.status_code == 422
    assert "Invalid wallet address" in response.json()["detail"]


def test_chain_history_rejects_unsupported_network(client):
    response = client.get(
        "/chain/history",
        params={"chainId": 11155111, "address": "0x" + "1" * 40},
    )
    assert response.status_code == 422
    assert "Ethereum and Base mainnet" in response.json()["detail"]

@pytest.mark.parametrize("symbol", ["ADAUSDT", "DOGEUSDT", "LTCUSDT", "LINKUSDT", "DOTUSDT"])
def test_candle_endpoint_accepts_expanded_watchlist_symbols(client, monkeypatch, symbol):
    class FakeResponse:
        status_code = 200
        def json(self):
            now_ms = 1_800_000_000_000
            return [
                [now_ms - 7_200_000, "100", "110", "95", "105", "12.5", now_ms - 3_600_001, "1250", 42],
                [now_ms - 3_600_000, "105", "112", "101", "108", "8.5", now_ms + 3_599_999, "918", 31],
            ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(main.time, "time", lambda: 1_800_000_000)
    response = client.get("/market/candles", params={"symbol": symbol, "interval": "1h", "limit": 100})
    assert response.status_code == 200
    rows = response.json()["candles"]
    assert len(rows) == 2
    assert rows[0]["is_closed"] is True
    assert rows[1]["is_closed"] is False


def test_candle_endpoint_supports_additional_real_exchange_intervals(client, monkeypatch):
    class FakeResponse:
        status_code = 200
        def json(self):
            return [
                [1700000000000, "100", "110", "95", "105", "12.5", 1700003599999, "1250", 42],
                [1700003600000, "105", "112", "101", "108", "8.5", 1700007199999, "918", 31],
            ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url, params=None, **kwargs):
            assert params["interval"] == "30m"
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    response = client.get("/market/candles", params={"symbol": "BTCUSDT", "interval": "30m", "limit": 100})
    assert response.status_code == 200
    assert response.json()["interval"] == "30m"
    assert len(response.json()["candles"]) == 2


def test_candle_endpoint_rejects_invalid_ohlcv_rows(client, monkeypatch):
    class FakeResponse:
        status_code = 200
        def json(self):
            return [
                [1700000000000, "100", "90", "95", "105", "12.5", 1700003599999, "1250", 42],
                [1700003600000, "100", "110", "95", "105", "-1", 1700007199999, "1250", 42],
            ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    response = client.get("/market/candles", params={"symbol": "BTCUSDT", "interval": "1h", "limit": 100})
    assert response.status_code == 502
    assert "fewer than two valid candles" in response.json()["detail"]

def test_candle_endpoint_supports_end_time_for_historical_pages(client, monkeypatch):
    class FakeResponse:
        status_code = 200
        def json(self):
            return [
                [1699992800000, "100", "110", "95", "105", "12.5", 1699996399999, "1250", 42],
                [1699996400000, "105", "112", "101", "108", "8.5", 1699999999999, "918", 31],
            ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url, params=None, **kwargs):
            assert params["endTime"] == 1700000000000
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    response = client.get(
        "/market/candles",
        params={"symbol": "BTCUSDT", "interval": "1h", "limit": 100, "end_time": 1700000000000},
    )
    assert response.status_code == 200
    assert response.json()["candles"][0]["time"] < 1700000000


def test_candle_endpoint_rejects_future_end_time(client):
    response = client.get(
        "/market/candles",
        params={"symbol": "BTCUSDT", "interval": "1h", "limit": 100, "end_time": 9999999999999},
    )
    assert response.status_code == 422
    assert "end_time" in response.json()["detail"]
