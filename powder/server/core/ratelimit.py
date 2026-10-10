"""Failed sign-ins: after too many in a window, further tries are refused until it passes, so nobody on the
network can guess passwords at full speed. Counted per (address, email) and, more loosely, per address,
so signing in to one's own account does not reset the count for guesses at another one."""
from __future__ import annotations

import time
from collections import deque


class FailureLimiter:
    def __init__(self, max_failures: int = 10, max_per_address: int = 30, window_s: float = 300.0,
                 clock=time.monotonic):
        self.max_failures, self.max_per_address, self.window_s = max_failures, max_per_address, window_s
        self.clock = clock
        self._failures: dict[tuple, deque] = {}

    def _count(self, key: tuple) -> int:
        q = self._failures.get(key)
        if q is None:
            return 0
        now = self.clock()
        while q and now - q[0] > self.window_s:
            q.popleft()
        if not q:
            del self._failures[key]
            return 0
        return len(q)

    def blocked(self, address: str, email: str = "") -> bool:
        return (self._count((address, email.lower())) >= self.max_failures
                or self._count((address,)) >= self.max_per_address)

    def failed(self, address: str, email: str = "") -> None:
        now = self.clock()
        for key in ((address, email.lower()), (address,)):
            self._count(key)
            self._failures.setdefault(key, deque()).append(now)

    def succeeded(self, address: str, email: str = "") -> None:
        self._failures.pop((address, email.lower()), None)
