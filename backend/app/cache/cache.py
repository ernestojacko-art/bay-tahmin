"""
Cache Layer (spec section 16).

A simple, dependency-free in-memory TTL cache. The interface is small
enough that swapping in a Redis-backed implementation later (for
multi-process deployments) requires no changes to calling code.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    """Thread-safe in-memory cache with per-key TTL."""

    def __init__(self, default_ttl_seconds: int = 900):
        self._store: dict[str, _CacheEntry] = {}
        self._lock = Lock()
        self._default_ttl = default_ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._store[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        with self._lock:
            self._store[key] = _CacheEntry(value=value, expires_at=time.monotonic() + ttl)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def get_or_set_sync(self, key: str, factory: Callable[[], T], ttl_seconds: Optional[int] = None) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = factory()
        self.set(key, value, ttl_seconds)
        return value


# Process-wide singleton caches, one per concern so TTLs / invalidation
# can be tuned independently (spec: "maç analizi, takım güç profili, ...
# tekrar hesaplanmamalıdır").
team_strength_cache = TTLCache(default_ttl_seconds=3600)
match_analysis_cache = TTLCache(default_ttl_seconds=1800)
raw_dataset_cache = TTLCache(default_ttl_seconds=900)
