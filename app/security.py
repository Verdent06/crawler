"""Request guards for the public API: shared-secret check and per-client rate limit."""

from __future__ import annotations

import hmac
import math
import os
import threading
import time
from collections import defaultdict, deque
from typing import Callable

DEFAULT_RATE_LIMIT_PER_MINUTE = 10
DEFAULT_MAX_CONCURRENT_SCRAPES = 3
_SWEEP_THRESHOLD = 1024


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return max(0, int(raw)) if raw else default
    except ValueError:
        return default


def api_key_matches(expected: str, provided: str | None) -> bool:
    if provided is None:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        if self.limit <= 0:
            return 0
        now = self.clock()
        with self._lock:
            self._sweep(now)
            hits = self._hits[key]
            self._expire(hits, now)
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            return 0

    def _expire(self, hits: deque[float], now: float) -> None:
        while hits and now - hits[0] >= self.window:
            hits.popleft()

    def _sweep(self, now: float) -> None:
        if len(self._hits) < _SWEEP_THRESHOLD:
            return
        for key in list(self._hits):
            self._expire(self._hits[key], now)
            if not self._hits[key]:
                del self._hits[key]
