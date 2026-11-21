import asyncio

from paper_orderbook_stream import PaperOrderBookHub, build_depth_delta


def snapshot(bids, asks):
    return {
        "source": "zencrypto_paper",
        "symbol": "BTC/USDT",
        "bids": [{"price": price, "quantity": quantity} for price, quantity in bids],
        "asks": [{"price": price, "quantity": quantity} for price, quantity in asks],
        "metrics": {"best_bid": bids[0][0] if bids else None},
    }


def test_depth_delta_only_contains_changed_levels_and_zero_removes():
    previous = snapshot([("100", "2"), ("99", "1")], [("101", "3")])
    current = snapshot([("100", "5"), ("98", "4")], [("102", "1")])
    delta = build_depth_delta(previous, current)

    assert delta["type"] == "depth_delta"
    assert delta["source"] == "zencrypto_paper"
    assert delta["bids"] == [["100", "5"], ["99", "0"], ["98", "4"]]
    assert delta["asks"] == [["101", "0"], ["102", "1"]]
    assert delta["metrics"] == current["metrics"]


def test_paper_orderbook_hub_shares_polling_and_suppresses_unchanged_snapshots():
    async def scenario():
        calls = 0
        changed = asyncio.Event()

        async def fetch(symbol):
            nonlocal calls
            calls += 1
            return {"symbol": symbol, "bids": [], "asks": [], "version": calls}

        hub = PaperOrderBookHub(fetch, interval_seconds=0.25)
        first = await hub.subscribe("BTC/USDT")
        second = await hub.subscribe("BTC/USDT")
        one = await asyncio.wait_for(first.get(), timeout=1)
        two = await asyncio.wait_for(second.get(), timeout=1)
        assert one == two
        assert calls == 1

        await hub.unsubscribe("BTC/USDT", first)
        await hub.unsubscribe("BTC/USDT", second)
        await asyncio.sleep(0)
        assert "BTC/USDT" not in hub._tasks
        await hub.close()

    asyncio.run(scenario())


def test_paper_orderbook_hub_requires_safe_poll_interval():
    try:
        PaperOrderBookHub(lambda symbol: None, interval_seconds=0.1)
    except ValueError as exc:
        assert "0.25 seconds" in str(exc)
    else:
        raise AssertionError("Unsafe polling interval should be rejected.")
