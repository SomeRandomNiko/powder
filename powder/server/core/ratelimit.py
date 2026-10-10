"""Failed sign-ins per address: after too many in a window, further tries are refused until it passes,
so nobody on the network can guess passwords at full speed."""
from __future__ import annotations

import time
from collections import defaultdict, deque


class FailureLimiter:
    def __init__(self, max_failures: int = 10, window_s: float = 300.0, clock=time.monotonic):
        self.max_failures, self.window_s, self.clock = max_failures, window_s, clock
        self._failures: dict[str, deque] = defaultdict(deque)

    def _recent(self, key: str) -> deque:
        q, now = self._failures[key], self.clock()
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_failures

    def failed(self, key: str) -> None:
        self._recent(key).append(self.clock())

    def succeeded(self, key: str) -> None:
        self._failures.pop(key, None)
