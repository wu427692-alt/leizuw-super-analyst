"""Tiny in-process TTL cache for expensive read-only dashboards."""
import threading
import time
from collections import OrderedDict
from src.request_identity import get_current_user_id

_CACHE = OrderedDict()
_LOCK = threading.Lock()


def cached(key, ttl, fn):
    scoped_key = (get_current_user_id(), key)
    now = time.monotonic()
    with _LOCK:
        item = _CACHE.get(scoped_key)
        if item and now - item[0] < ttl:
            _CACHE.move_to_end(scoped_key)
            return item[1]
    value = fn()
    with _LOCK:
        _CACHE[scoped_key] = (time.monotonic(), value)
        _CACHE.move_to_end(scoped_key)
        while len(_CACHE) > 128:
            _CACHE.popitem(last=False)
    return value


def invalidate(prefix=None):
    with _LOCK:
        if prefix is None:
            _CACHE.clear()
            return
        for key in [k for k in _CACHE if k[1].startswith(prefix)]:
            _CACHE.pop(key, None)
