"""Request-local identity shared with synchronous service/repository code."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

_current_user_id: ContextVar[Optional[int]] = ContextVar("dsa_current_user_id", default=None)


def set_current_user_id(user_id: Optional[int]) -> Token:
    return _current_user_id.set(user_id)


def reset_current_user_id(token: Token) -> None:
    _current_user_id.reset(token)


def get_current_user_id() -> Optional[int]:
    return _current_user_id.get()


def current_owner_id() -> Optional[str]:
    user_id = get_current_user_id()
    return f"user:{user_id}" if user_id is not None and user_id > 0 else None
