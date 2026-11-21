"""Conservative process-local guardrails for the paper-order API.

For multi-worker or multi-instance deployments, replace this with a shared
Redis/database limiter. This is not a production abuse-prevention boundary.
"""
from collections import defaultdict, deque
from decimal import Decimal
import threading
import time


class PaperOrderLimitExceeded(ValueError):
    def __init__(self, message, retry_after=1):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


class PaperOrderGuard:
    def __init__(self, max_notional="100000", max_requests=30, window_seconds=60):
        self.max_notional = Decimal(str(max_notional))
        self.max_requests = int(max_requests)
        self.window_seconds = int(window_seconds)
        if not self.max_notional.is_finite() or self.max_notional <= 0:
            raise ValueError("max_notional must be finite and positive.")
        if self.max_requests < 1 or self.window_seconds < 1:
            raise ValueError("rate limit and window must be positive.")
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key, quantity, price, now=None):
        notional = Decimal(str(quantity)) * Decimal(str(price))
        if notional > self.max_notional:
            raise PaperOrderLimitExceeded(
                f"Paper order notional exceeds the configured maximum of {self.max_notional}.",
                retry_after=1,
            )

        current = time.monotonic() if now is None else float(now)
        cutoff = current - self.window_seconds
        with self._lock:
            events = self._events[str(key)]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.max_requests:
                retry_after = self.window_seconds - (current - events[0])
                raise PaperOrderLimitExceeded(
                    "Paper order rate limit exceeded. Try again later.", retry_after
                )
            events.append(current)
