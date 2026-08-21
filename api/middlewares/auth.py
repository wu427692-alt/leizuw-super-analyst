# -*- coding: utf-8 -*-
"""Authentication boundary for approved users and the operator console."""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.auth import COOKIE_NAME, is_auth_enabled, verify_session
from src.auth import get_client_ip
from src.services.user_account_service import (
    USER_AUTO_LOGIN_SUPPRESSION_COOKIE,
    USER_COOKIE_NAME,
    UserAccountService,
    user_access_enabled,
)
from src.request_identity import reset_current_user_id, set_current_user_id

logger = logging.getLogger(__name__)

EXEMPT_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/status",
    "/api/health",
    "/api/v1/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/user-auth/status",
    "/api/v1/user-auth/register",
    "/api/v1/user-auth/login",
    "/api/v1/user-auth/logout",
})

ADMIN_PREFIXES = (
    "/api/v1/system",
    "/api/v1/usage",
    "/api/v1/user-auth/admin",
)

ADMIN_EXACT_PATHS = frozenset({
    "/api/v1/auth/settings",
    "/api/v1/auth/change-password",
    "/api/v1/auth/logout",
    "/api/v1/alphasift/install",
    "/api/v1/essay-quant/precompute/run",
    "/api/v1/essay-radar/backfill",
    "/api/v1/essay-radar/backfill-count",
    "/api/v1/essay-radar/retry-failed",
    "/api/v1/essay-radar/worker/start",
    "/api/v1/essay-radar/worker/stop",
    "/api/v1/essay-radar/daily-reports/run",
    "/api/v1/essay-radar/daily-reports/worker/start",
    "/api/v1/essay-radar/daily-reports/worker/stop",
    "/api/v1/financial-data/zsxq/sync",
    "/api/v1/financial-data/zsxq/sync/history",
    "/api/v1/financial-data/zsxq/sync/worker/start",
    "/api/v1/financial-data/zsxq/sync/worker/stop",
    "/api/v1/investment-monitor/watchdog/audit",
    "/api/v1/investment-monitor/sync",
    "/api/v1/investment-monitor/worker/start",
    "/api/v1/investment-monitor/worker/stop",
    "/api/v1/investment-monitor/dragon-tiger/sync",
    "/api/v1/investment-monitor/announcements/sync",
    "/api/v1/investment-monitor/cloud/snapshot",
    "/api/v1/investment-monitor/cloud/worker/start",
    "/api/v1/investment-monitor/cloud/worker/stop",
    "/api/v1/stocks/market-data/refresh",
})


def _requires_admin(method: str, path: str) -> bool:
    """Return whether an API request changes or exposes operator state."""
    normalized = path.rstrip("/") or "/"
    if normalized.startswith(ADMIN_PREFIXES):
        return True
    if normalized in ADMIN_EXACT_PATHS:
        return True

    upper_method = method.upper()
    if upper_method != "GET" and upper_method != "HEAD":
        if normalized.startswith("/api/v1/intelligence/sources"):
            return True
        if normalized.startswith("/api/v1/investment-monitor/sources"):
            return True
        if normalized.startswith("/api/v1/investment-monitor/cloud/"):
            return True
    return False


def _path_exempt(path: str) -> bool:
    """Check if path is exempt from auth."""
    normalized = path.rstrip("/") or "/"
    return normalized in EXEMPT_PATHS


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce two independent boundaries: approved users and administrators."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ):
        async def call_with_identity(user_id: int | None):
            token = set_current_user_id(user_id)
            try:
                return await call_next(request)
            finally:
                reset_current_user_id(token)

        path = request.url.path
        if _path_exempt(path):
            return await call_with_identity(None)

        if not path.startswith("/api/v1/"):
            return await call_with_identity(None)

        admin_cookie = request.cookies.get(COOKIE_NAME)
        is_admin = bool(admin_cookie and verify_session(admin_cookie)) if is_auth_enabled() else False

        if _requires_admin(request.method, path):
            if not is_auth_enabled():
                request.state.user_id = 0
                request.state.user_name = "legacy"
                request.state.auth_method = "disabled"
                return await call_with_identity(0)
            if not is_admin:
                return JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "message": "Administrator login required"},
                )
            request.state.user_id = 0
            request.state.user_name = "管理员"
            request.state.auth_method = "admin"
            return await call_with_identity(0)

        if not user_access_enabled():
            request.state.user_id = 0
            request.state.user_name = "legacy"
            request.state.auth_method = "disabled"
            return await call_with_identity(0)

        if is_admin:
            request.state.user_id = 0
            request.state.user_name = "管理员"
            request.state.auth_method = "admin"
            return await call_with_identity(0)

        service = UserAccountService()
        user = service.account_for_session(request.cookies.get(USER_COOKIE_NAME, ""))
        method = "session"
        if user is None and not request.cookies.get(USER_AUTO_LOGIN_SUPPRESSION_COOKIE):
            user = service.account_for_ip(get_client_ip(request))
            method = "trusted_ip"
        if user is None:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "user_login_required",
                    "message": "请先登录，或提交访问申请等待管理员审核",
                },
            )

        request.state.user_id = int(user.id)
        request.state.user_name = user.display_name
        request.state.auth_method = method
        return await call_with_identity(int(user.id))


def add_auth_middleware(app):
    """Add runtime-configured front-office and administrator authentication."""
    app.add_middleware(AuthMiddleware)
