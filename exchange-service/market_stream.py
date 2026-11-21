"""Shared polling fan-out for public external market data WebSocket clients."""
import asyncio
from collections import defaultdict


class MarketStreamHub:
    def __init__(self, fetch_snapshot, interval_seconds=2.0, queue_size=10):
        if interval_seconds < 2:
            raise ValueError("Public market polling interval cannot be below 2 seconds.")
        self.fetch_snapshot = fetch_snapshot
        self.interval_seconds = interval_seconds
        self.queue_size = queue_size
        self._lock = asyncio.Lock()
        self._subscribers = defaultdict(set)
        self._tasks = {}

    async def subscribe(self, symbol):
        queue = asyncio.Queue(maxsize=self.queue_size)
        async with self._lock:
            self._subscribers[symbol].add(queue)
            if symbol not in self._tasks:
                self._tasks[symbol] = asyncio.create_task(self._run_symbol(symbol))
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

    async def _publish(self, symbol, event):
        async with self._lock:
            queues = tuple(self._subscribers.get(symbol, ()))
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _run_symbol(self, symbol):
        try:
            while True:
                try:
                    snapshot = await self.fetch_snapshot(symbol)
                    await self._publish(symbol, {"type": "market_snapshot", "data": snapshot})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Do not expose credentials, request headers, or stack traces to clients.
                    await self._publish(
                        symbol,
                        {"type": "market_error", "message": "External market data is temporarily unavailable."},
                    )
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
