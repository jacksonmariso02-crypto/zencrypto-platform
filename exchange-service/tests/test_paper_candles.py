from decimal import Decimal

import pytest

import paper_engine as paper_engine_module
from paper_engine import PaperMatchingEngine


def test_paper_candles_aggregate_fills_with_utc_buckets_and_exact_decimals(tmp_path, monkeypatch):
    engine = PaperMatchingEngine(str(tmp_path / "candles.sqlite3"))
    timestamps = iter([
        60_000_000_000, 61_000_000_000,
        62_000_000_000, 63_000_000_000,
        120_000_000_000, 121_000_000_000,
    ])
    monkeypatch.setattr(paper_engine_module.time, "time_ns", lambda: next(timestamps))

    for index, (price, quantity) in enumerate([
        ("100.01", "0.100001"),
        ("110.02", "0.200002"),
        ("90.03", "0.300003"),
    ]):
        engine.place_limit_order(
            "BTC/USDT", "sell", quantity, price, f"candle-ask-{index:04d}"
        )
        engine.place_limit_order(
            "BTC/USDT", "buy", quantity, "200", f"candle-bid-{index:04d}"
        )

    candles = engine.list_candles("BTC/USDT", "1m", 100)
    assert len(candles) == 2
    assert candles[0] == {
        "timestamp": 60_000,
        "open": "100.01",
        "high": "110.02",
        "low": "100.01",
        "close": "110.02",
        "volume": "0.300003",
    }
    assert candles[1] == {
        "timestamp": 120_000,
        "open": "90.03",
        "high": "90.03",
        "low": "90.03",
        "close": "90.03",
        "volume": "0.300003",
    }


def test_paper_candle_since_and_limit_filters(tmp_path, monkeypatch):
    engine = PaperMatchingEngine(str(tmp_path / "candles-filter.sqlite3"))
    timestamps = iter([
        60_000_000_000, 61_000_000_000,
        120_000_000_000, 121_000_000_000,
        180_000_000_000, 181_000_000_000,
    ])
    monkeypatch.setattr(paper_engine_module.time, "time_ns", lambda: next(timestamps))
    for index, price in enumerate(("100", "110", "120")):
        engine.place_limit_order(
            "BTC/USDT", "sell", "0.1", price, f"filter-ask-{index:04d}"
        )
        engine.place_limit_order(
            "BTC/USDT", "buy", "0.1", "200", f"filter-bid-{index:04d}"
        )

    candles = engine.list_candles("BTC/USDT", "1m", limit=1)
    assert len(candles) == 1
    assert candles[0]["timestamp"] == 180_000
    since = engine.list_candles("BTC/USDT", "1m", limit=10, since=120_000)
    assert [item["timestamp"] for item in since] == [120_000, 180_000]


@pytest.mark.parametrize("timeframe", ["2m", "3h", "1M", ""])
def test_paper_candles_reject_unsupported_timeframes(tmp_path, timeframe):
    engine = PaperMatchingEngine(str(tmp_path / "candles-invalid.sqlite3"))
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        engine.list_candles("BTC/USDT", timeframe)


def test_paper_candles_validate_symbol_limit_and_since(tmp_path):
    engine = PaperMatchingEngine(str(tmp_path / "candles-params.sqlite3"))
    with pytest.raises(ValueError, match="valid symbol"):
        engine.list_candles("BTC USDT")
    with pytest.raises(ValueError, match="between 1 and 1000"):
        engine.list_candles("BTC/USDT", limit=1001)
    with pytest.raises(ValueError, match="non-negative"):
        engine.list_candles("BTC/USDT", since=-1)
