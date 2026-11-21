"""Shared polling fan-out for durable ZenCrypto paper order-book snapshots."""
import asyncio


class PaperOrderBookHub:
    def __init__(self, fetch_snapshot, interval_seconds=0.5, queue_size=20):
        if interval_seconds < 0.25:
            raise ValueError("Paper order-book polling interval cannot be below 0.25 seconds.")
        if queue_size < 1:
            raise ValueError("queue_size must be positive.")
        self.fetch_snapshot = fetch_snapshot
        self.interval_seconds = interval_seconds
        self.queue_size = queue_size
        self._lock = asyncio.Lock()
        self._subscribers = {}
        self._tasks = {}

    async def subscribe(self, symbol):
        queue = asyncio.Queue(maxsize=self.queue_size)
        async with self._lock:
            self._subscribers.setdefault(symbol, set()).add(queue)
            if symbol not in self._tasks:
                self._tasks[symbol] = asyncio.create_task(self._run(symbol))
        return queue

    async def unsubscribe(self, symbol, queue):
        async with self._lock:
            subscribers = self._subscribers.get(symbol)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(symbol, None)
                task = self._tasks.pop(symbol, None)
                if task:
                    task.cancel()

    async def _publish(self, symbol, payload):
        async with self._lock:
            queues = tuple(self._subscribers.get(symbol, ()))
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def _run(self, symbol):
        previous = None
        try:
            while True:
                try:
                    snapshot = await self.fetch_snapshot(symbol)
                    if snapshot != previous:
                        await self._publish(symbol, snapshot)
                        previous = snapshot
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self._publish(symbol, {
                        "type": "error",
                        "message": "Paper order book is temporarily unavailable.",
                    })
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            return

    async def close(self):
        async with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
            self._subscribers.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def build_depth_delta(previous, current):
    """Return changed [price, quantity] levels; quantity zero means delete the level."""
    def level_map(snapshot, side):
        return {str(level["price"]): str(level["quantity"]) for level in snapshot.get(side, [])}

    changes = {}
    for side in ("bids", "asks"):
        old = level_map(previous, side)
        new = level_map(current, side)
        changes[side] = [
            [price, new.get(price, "0")]
            for price in sorted(set(old) | set(new), key=lambda value: __import__("decimal").Decimal(value), reverse=(side == "bids"))
            if old.get(price) != new.get(price)
        ]
    return {
        "type": "depth_delta",
        "source": "zencrypto_paper",
        "symbol": current["symbol"],
        "bids": changes["bids"],
        "asks": changes["asks"],
        "metrics": current.get("metrics", {}),
    }
