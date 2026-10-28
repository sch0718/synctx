"""사용자별 슬라이딩 윈도 속도 제한(프로세스 안 메모리)."""

from __future__ import annotations

import time
from collections import deque
from typing import Callable

WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(self, limit: int, *, clock: Callable[[], float] = time.monotonic, max_keys: int = 10_000):
        self._limit = limit
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def _expire(self, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]

    def allow(self, key: str) -> bool:
        now = self._clock()
        if len(self._hits) >= self._max_keys and key not in self._hits:
            self._expire(now)
            if len(self._hits) >= self._max_keys:
                oldest = min(self._hits, key=lambda k: self._hits[k][-1])
                del self._hits[oldest]
        hits = self._hits.setdefault(key, deque())
        cutoff = now - WINDOW_SECONDS
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
