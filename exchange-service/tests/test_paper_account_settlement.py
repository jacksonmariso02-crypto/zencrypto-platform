from paper_engine import PaperMatchingEngine
from paper_ledger import InsufficientPaperFunds, PaperLedger


def setup(tmp_path):
    path = str(tmp_path / "account-paper.sqlite3")
    return PaperMatchingEngine(path), PaperLedger(path)


def test_account_orders_reserve_and_settle_both_assets_atomically(tmp_path):
    engine, ledger = setup(tmp_path)
    ledger.create_account("buyer_001")
    ledger.create_account("seller_001")
    ledger.credit_paper_funds("buyer_001", "USDT", "1000", "buyer-fund-0001")
    ledger.credit_paper_funds("seller_001", "BTC", "2", "seller-fund-001")

    ask = engine.place_limit_order(
        "BTC/USDT", "sell", "1", "100", "seller-ask-0001", "seller_001"
    )
    assert ask["status"] == "open"
    assert ledger.get_balances("seller_001") == [
        {"asset": "BTC", "available": "1", "locked": "1"}
    ]

    bid = engine.place_limit_order(
        "BTC/USDT", "buy", "1", "110", "buyer-bid-00001", "buyer_001"
    )
    assert bid["status"] == "filled"
    assert ledger.get_balances("buyer_001") == [
        {"asset": "BTC", "available": "1", "locked": "0"},
        {"asset": "USDT", "available": "900", "locked": "0"},
    ]
    assert ledger.get_balances("seller_001") == [
        {"asset": "BTC", "available": "1", "locked": "0"},
        {"asset": "USDT", "available": "100", "locked": "0"},
    ]
    assert len(ledger.list_entries("buyer_001")) == 6
    assert len(ledger.list_entries("seller_001")) == 5


def test_partial_fill_keeps_remainder_reserved_and_cancel_releases_it(tmp_path):
    engine, ledger = setup(tmp_path)
    ledger.create_account("buyer_002")
    ledger.create_account("seller_002")
    ledger.credit_paper_funds("buyer_002", "USDT", "1000", "buyer-fund-0002")
    ledger.credit_paper_funds("seller_002", "BTC", "2", "seller-fund-002")

    ask = engine.place_limit_order(
        "BTC/USDT", "sell", "2", "100", "seller-ask-0002", "seller_002"
    )
    bid = engine.place_limit_order(
        "BTC/USDT", "buy", "0.5", "110", "buyer-bid-00002", "buyer_002"
    )
    assert bid["status"] == "filled"
    remaining_ask = engine.get_order(ask["id"])
    assert remaining_ask["status"] == "partially_filled"
    assert remaining_ask["remaining"] == "1.5"
    assert ledger.get_balances("seller_002") == [
        {"asset": "BTC", "available": "0", "locked": "1.5"},
        {"asset": "USDT", "available": "50.0", "locked": "0"},
    ]
    assert ledger.get_balances("buyer_002") == [
        {"asset": "BTC", "available": "0.5", "locked": "0"},
        {"asset": "USDT", "available": "950.0", "locked": "0.0"},
    ]

    cancelled = engine.cancel_order(ask["id"], "seller_002")
    assert cancelled["status"] == "cancelled"
    assert ledger.get_balances("seller_002") == [
        {"asset": "BTC", "available": "1.5", "locked": "0.0"},
        {"asset": "USDT", "available": "50.0", "locked": "0"},
    ]


def test_insufficient_balance_rejects_order_without_creating_it(tmp_path):
    engine, ledger = setup(tmp_path)
    ledger.create_account("poor_003")
    ledger.credit_paper_funds("poor_003", "USDT", "20", "poor-fund-0001")

    try:
        engine.place_limit_order(
            "BTC/USDT", "buy", "1", "21", "poor-order-0001", "poor_003"
        )
        assert False, "Expected insufficient funds"
    except InsufficientPaperFunds:
        pass

    assert engine.list_orders(account_id="poor_003") == []
    assert ledger.get_balances("poor_003") == [
        {"asset": "USDT", "available": "20", "locked": "0"}
    ]


def test_account_orders_do_not_match_legacy_shared_orders(tmp_path):
    engine, ledger = setup(tmp_path)
    ledger.create_account("acct_004")
    ledger.credit_paper_funds("acct_004", "USDT", "1000", "acct-fund-0001")
    ledger.credit_paper_funds("acct_004", "BTC", "1", "acct-btc-fund-01")

    legacy_ask = engine.place_limit_order(
        "BTC/USDT", "sell", "1", "100", "legacy-ask-0001"
    )
    account_bid = engine.place_limit_order(
        "BTC/USDT", "buy", "1", "110", "account-bid-0001", "acct_004"
    )
    assert legacy_ask["status"] == "open"
    assert account_bid["status"] == "open"
    assert engine.list_trades("BTC/USDT") == []


def test_sell_taker_settles_at_resting_buy_maker_price(tmp_path):
    engine, ledger = setup(tmp_path)
    ledger.create_account("maker_buyer")
    ledger.create_account("taker_seller")
    ledger.credit_paper_funds("maker_buyer", "USDT", "500", "maker-buyer-fund")
    ledger.credit_paper_funds("taker_seller", "BTC", "1", "taker-seller-fund")

    bid = engine.place_limit_order(
        "BTC/USDT", "buy", "1", "110", "maker-bid-00001", "maker_buyer"
    )
    ask = engine.place_limit_order(
        "BTC/USDT", "sell", "1", "100", "taker-ask-0001", "taker_seller"
    )
    assert ask["status"] == "filled"
    trade = engine.list_trades("BTC/USDT")[0]
    assert trade["price"] == "110"

    assert ledger.get_balances("maker_buyer") == [
        {"asset": "BTC", "available": "1", "locked": "0"},
        {"asset": "USDT", "available": "390", "locked": "0"},
    ]
    assert ledger.get_balances("taker_seller") == [
        {"asset": "USDT", "available": "110", "locked": "0"},
    ]


def test_same_account_crossing_orders_never_self_trade(tmp_path):
    engine, ledger = setup(tmp_path)
    ledger.create_account("self_001")
    ledger.credit_paper_funds("self_001", "BTC", "2", "self-btc-credit-01")
    ledger.credit_paper_funds("self_001", "USDT", "500", "self-usdt-credit-01")

    ask = engine.place_limit_order(
        "BTC/USDT", "sell", "1", "100", "self-ask-00001", "self_001"
    )
    bid = engine.place_limit_order(
        "BTC/USDT", "buy", "1", "110", "self-bid-00001", "self_001"
    )
    assert ask["status"] == "open"
    assert bid["status"] == "open"
    assert engine.list_trades("BTC/USDT") == []
    assert ledger.get_balances("self_001") == [
        {"asset": "BTC", "available": "1", "locked": "1"},
        {"asset": "USDT", "available": "390", "locked": "110"},
    ]


def test_self_orders_are_skipped_but_external_liquidity_can_fill(tmp_path):
    engine, ledger = setup(tmp_path)
    for account in ("self_002", "external_002"):
        ledger.create_account(account)
    ledger.credit_paper_funds("self_002", "BTC", "2", "self-btc-credit-02")
    ledger.credit_paper_funds("self_002", "USDT", "500", "self-usdt-credit-02")
    ledger.credit_paper_funds("external_002", "BTC", "1", "external-btc-fund")

    own_ask = engine.place_limit_order(
        "BTC/USDT", "sell", "1", "90", "self-ask-00002", "self_002"
    )
    other_ask = engine.place_limit_order(
        "BTC/USDT", "sell", "1", "100", "external-ask-02", "external_002"
    )
    bid = engine.place_limit_order(
        "BTC/USDT", "buy", "1", "110", "self-bid-00002", "self_002"
    )
    assert bid["status"] == "filled"
    assert engine.get_order(own_ask["id"])["status"] == "open"
    assert engine.get_order(other_ask["id"])["status"] == "filled"
    trades = engine.list_trades("BTC/USDT")
    assert len(trades) == 1
    assert trades[0]["maker_order_id"] == other_ask["id"]
    assert ledger.get_balances("self_002") == [
        {"asset": "BTC", "available": "2", "locked": "1"},
        {"asset": "USDT", "available": "400", "locked": "0"},
    ]
