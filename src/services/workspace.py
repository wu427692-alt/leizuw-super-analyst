"""Request-scoped workspace id for generated files."""
import threading

_state = threading.local()


def set_workspace(value):
    _state.value = str(value or "").strip() or None


def current_workspace():
    return getattr(_state, "value", None)
