from __future__ import annotations

import asyncio
import time


class HostRateLimiter:
    """Token bucket per host: a short burst at once, then a steady rate.

    A host may receive ``burst`` requests without waiting. After that, requests
    are released at ``per_second``. An idle host earns its burst back at the same
    rate, so the long-run average never exceeds ``per_second``. A non-positive
    rate disables the wait.
    """

    def __init__(self, per_second: float, burst: int = 1):
        self.per_second = per_second
        self.burst = max(int(burst), 1)
        self._tokens: dict[str, float] = {}
        self._stamp: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        if self.per_second <= 0:
            return
        # No await between reading and writing the bucket, so no lock is needed.
        now = time.monotonic()
        tokens = self._tokens.get(host, float(self.burst))
        tokens = min(float(self.burst), tokens + (now - self._stamp.get(host, now)) * self.per_second)
        self._stamp[host] = now
        self._tokens[host] = tokens - 1.0
        if tokens < 1.0:
            await asyncio.sleep((1.0 - tokens) / self.per_second)
