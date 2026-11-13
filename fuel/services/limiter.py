"""One OSRM request per second, shared by every Gunicorn worker through Redis."""

from __future__ import annotations

import time

from django.conf import settings
from django.core.cache import cache

LEASE_KEY = "osrm:lease"
LEASE_MS = 1000


class RateLimitTimeout(TimeoutError):
    pass


def acquire_osrm_slot(max_wait: float = 8.0) -> None:
    if settings.REDIS_URL:
        _acquire_redis(max_wait)
        return
    _acquire_cache(max_wait)


def _acquire_redis(max_wait: float) -> None:
    from django_redis import get_redis_connection

    client = get_redis_connection("default")
    deadline = time.monotonic() + max_wait
    while True:
        if client.set(LEASE_KEY, "1", nx=True, px=LEASE_MS):
            return
        remaining = client.pttl(LEASE_KEY)
        wait = 0.05 if remaining is None or remaining < 0 else min(remaining / 1000, 0.2)
        if time.monotonic() + wait > deadline:
            raise RateLimitTimeout("OSRM rate limit")
        time.sleep(wait)


def _acquire_cache(max_wait: float) -> None:
    """Process-local fallback used when REDIS_URL is unset (local dev and tests)."""
    deadline = time.monotonic() + max_wait
    while True:
        now = time.time()
        until = cache.get(LEASE_KEY) or 0
        if now >= until:
            cache.set(LEASE_KEY, now + LEASE_MS / 1000, timeout=2)
            return
        wait = min(until - now, 0.05)
        if time.monotonic() + wait > deadline:
            raise RateLimitTimeout("OSRM rate limit")
        time.sleep(wait)
