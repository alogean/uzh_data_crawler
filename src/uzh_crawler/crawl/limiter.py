from __future__ import annotations

import asyncio
import time


class HostRateLimiter:
    """Space out requests to a single host. A non-positive rate disables the wait."""

    def __init__(self, per_second: float):
        self.per_second = per_second
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def wait(self, host: str) -> None:
        if self.per_second <= 0:
            return
        async with self._guard:
            lock = self._locks.get(host)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[host] = lock
        async with lock:
            interval = 1.0 / self.per_second
            now = time.monotonic()
            slot = self._next.get(host, now)
            delay = slot - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._next[host] = max(slot, now) + interval
