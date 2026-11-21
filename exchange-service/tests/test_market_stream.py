import asyncio

from market_stream import MarketStreamHub


def test_market_stream_hub_shares_one_poller_between_subscribers():
    async def scenario():
        calls = 0
        delivered = asyncio.Event()

        async def fetch(symbol):
            nonlocal calls
            calls += 1
            return {"symbol": symbol, "last": 100 + calls}

        hub = MarketStreamHub(fetch, interval_seconds=2)
        first = await hub.subscribe("BTC/USDT")
        second = await hub.subscribe("BTC/USDT")
        event_one = await asyncio.wait_for(first.get(), timeout=1)
        event_two = await asyncio.wait_for(second.get(), timeout=1)

        assert event_one["type"] == "market_snapshot"
        assert event_one["data"]["symbol"] == "BTC/USDT"
        assert event_one == event_two
        assert calls == 1

        await hub.unsubscribe("BTC/USDT", first)
        assert not hub._tasks["BTC/USDT"].done()
        await hub.unsubscribe("BTC/USDT", second)
        await asyncio.sleep(0)
        assert "BTC/USDT" not in hub._tasks
        await hub.close()

    asyncio.run(scenario())


def test_market_stream_hub_drops_stale_snapshots_for_slow_clients():
    async def scenario():
        async def fetch(symbol):
            return {"symbol": symbol, "last": 101}

        hub = MarketStreamHub(fetch, interval_seconds=2, queue_size=1)
        queue = await hub.subscribe("ETH/USDT")
        await hub._publish("ETH/USDT", {"type": "snapshot", "sequence": 1})
        await hub._publish("ETH/USDT", {"type": "snapshot", "sequence": 2})
        assert queue.qsize() == 1
        assert (await queue.get())["sequence"] == 2
        await hub.unsubscribe("ETH/USDT", queue)
        await hub.close()

    asyncio.run(scenario())


def test_market_stream_hub_rejects_too_fast_polling():
    try:
        MarketStreamHub(lambda symbol: None, interval_seconds=0.5)
    except ValueError as exc:
        assert "2 seconds" in str(exc)
    else:
        raise AssertionError("Polling intervals below two seconds must be rejected.")
