"""Tiny in-process TTL cache for expensive read-only dashboards."""
import threading
import time

_CACHE = {}
_LOCK = threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with _LOCK:
        item = _CACHE.get(key)
        if item and now - item[0] < ttl:
            return item[1]
    value = fn()
    with _LOCK:
        _CACHE[key] = (now, value)
    return value


def invalidate(prefix=None):
    with _LOCK:
        if prefix is None:
            _CACHE.clear()
            return
        for key in [k for k in _CACHE if k.startswith(prefix)]:
            _CACHE.pop(key, None)
