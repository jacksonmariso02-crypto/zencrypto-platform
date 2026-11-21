from paper_engine import PaperMatchingEngine


def test_paper_orderbook_depth_aggregates_all_open_orders_by_price(tmp_path):
    engine = PaperMatchingEngine(str(tmp_path / "depth.sqlite3"))
    orders = [
        ("sell", "0.001", "50010", "depth-ask-001"),
        ("sell", "0.002", "50010", "depth-ask-002"),
        ("sell", "0.003", "50020", "depth-ask-003"),
        ("buy", "0.001", "50000", "depth-bid-001"),
        ("buy", "0.002", "50000", "depth-bid-002"),
        ("buy", "0.003", "49990", "depth-bid-003"),
    ]
    for side, quantity, price, client_id in orders:
        engine.place_limit_order("BTC/USDT", side, quantity, price, client_id)

    depth = engine.orderbook_depth("BTC/USDT", limit=10)
    assert depth["source"] == "zencrypto_paper"
    assert depth["bids"][0]["price"] == "50000"
    assert depth["bids"][0]["quantity"] == "0.003"
    assert depth["bids"][0]["cumulative_quantity"] == "0.003"
    assert depth["asks"][0]["price"] == "50010"
    assert depth["asks"][0]["quantity"] == "0.003"
    assert depth["asks"][0]["cumulative_quantity"] == "0.003"
    assert depth["metrics"]["best_bid"] == "50000"
    assert depth["metrics"]["best_ask"] == "50010"
    assert depth["metrics"]["spread"] == "10"
    assert depth["metrics"]["mid_price"] == "50005"


def test_paper_orderbook_depth_excludes_filled_and_cancelled_orders(tmp_path):
    engine = PaperMatchingEngine(str(tmp_path / "depth-status.sqlite3"))
    engine.place_limit_order("BTC/USDT", "sell", "0.001", "50000", "depth-status-ask")
    engine.place_limit_order("BTC/USDT", "buy", "0.001", "50000", "depth-status-bid")
    engine.place_limit_order("BTC/USDT", "sell", "0.001", "51000", "depth-status-cancel")
    cancelled = engine.list_orders("BTC/USDT")[-1]
    engine.cancel_order(cancelled["id"])

    depth = engine.orderbook_depth("BTC/USDT")
    assert depth["bids"] == []
    assert depth["asks"] == []
    assert depth["metrics"]["best_bid"] is None
    assert depth["metrics"]["best_ask"] is None
