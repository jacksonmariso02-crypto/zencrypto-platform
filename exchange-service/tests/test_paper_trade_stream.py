from paper_engine import PaperMatchingEngine


def test_incremental_trade_cursor_filters_by_symbol_and_returns_oldest_first(tmp_path):
    engine = PaperMatchingEngine(str(tmp_path / "trade-cursor.sqlite3"))
    engine.place_limit_order("BTC/USDT", "sell", "0.1", "50000", "cursor-btc-ask-01")
    engine.place_limit_order("BTC/USDT", "buy", "0.1", "50001", "cursor-btc-bid-01")
    first = engine.list_trades_after(0, "BTC/USDT", 10)
    assert len(first) == 1
    assert first[0]["symbol"] == "BTC/USDT"
    assert first[0]["id"] > 0
    assert engine.latest_trade_id("BTC/USDT") == first[0]["id"]
    assert engine.list_trades_after(first[0]["id"], "BTC/USDT", 10) == []
    assert engine.latest_trade_id("ETH/USDT") == 0


def test_incremental_trade_cursor_rejects_invalid_bounds(tmp_path):
    engine = PaperMatchingEngine(str(tmp_path / "trade-cursor-bounds.sqlite3"))
    for cursor, limit in [(-1, 10), (0, 0), (0, 501)]:
        try:
            engine.list_trades_after(cursor, limit=limit)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid cursor or limit should be rejected")
