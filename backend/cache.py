"""
TTL cache — in-memory key/value store with lazy expiry.

Cache key: (func_name, positional_args, sorted_kwargs_tuple)
Cache key intentionally excludes `limit` so different limit values share
the same cached leaderboard result (slicing happens at the API layer).
"""
from __future__ import annotations

import time
from functools import wraps
from typing import Any

from backend.config import settings

CACHE_TTL: int = settings.cache_ttl

_cache: dict[tuple, tuple[Any, float]] = {}


def ttl_cache(ttl: int = CACHE_TTL):
    """
    Decorator that caches a function's return value for `ttl` seconds.

    - HIT  → return cached result (O(1), no I/O)
    - MISS → compute, store with monotonic timestamp, return result
    - TTL expiry is lazy: stale entries are evicted on next access
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (func.__name__, args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            if key in _cache:
                cached_result, cached_at = _cache[key]
                if now - cached_at < ttl:
                    return cached_result
            result = func(*args, **kwargs)
            _cache[key] = (result, now)
            return result
        return wrapper
    return decorator
