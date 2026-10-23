"""Ограничение частоты запросов в памяти процесса (один воркер, без Redis)."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Скользящее окно: не больше ``limit`` событий за ``window`` секунд на ключ."""

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                self._prune(now)
            return True

    def _prune(self, now: float) -> None:
        stale = [
            key for key, hits in self._hits.items() if not hits or now - hits[-1] >= self.window
        ]
        for key in stale:
            del self._hits[key]
