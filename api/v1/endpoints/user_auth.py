# -*- coding: utf-8 -*-
"""Public user registration/login and administrator approval endpoints."""

from __future__ import annotations

import os

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from src.auth import (
    COOKIE_NAME as ADMIN_COOKIE_NAME,
    check_rate_limit,
    clear_rate_limit,
    get_client_ip,
    record_login_failure,
    verify_session as verify_admin_session,
)
from src.services.user_account_service import (
    SESSION_MAX_AGE_DAYS,
    USER_AUTO_LOGIN_SUPPRESSION_COOKIE,
    USER_COOKIE_NAME,
    UserAccountError,
    UserAccountService,
    create_user_session,
    serialize_current_user,
    user_access_enabled,
)

router = APIRouter()


class UserCredentialsRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


def _cookie_options(request: Request, *, max_age: int) -> dict:
    forwarded_https = (
        os.getenv("TRUST_X_FORWARDED_FOR", "false").lower() == "true"
        and request.headers.get("X-Forwarded-Proto", "").lower() == "https"
    )
    return {
        "httponly": True,
        "secure": forwarded_https or request.url.scheme == "https",
        "samesite": "lax",
        "path": "/",
        "max_age": max_age,
    }


def _error(exc: UserAccountError, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": exc.code, "message": exc.message})


def _resolve_request_user(request: Request, *, allow_ip: bool = True):
    # The already authenticated operator can inspect the front office without
    # creating a normal user account; private resources remain in owner scope 0.
    admin_cookie = request.cookies.get(ADMIN_COOKIE_NAME)
    if admin_cookie and verify_admin_session(admin_cookie):
        return None, "admin"
    service = UserAccountService()
    row = service.account_for_session(request.cookies.get(USER_COOKIE_NAME, ""))
    if row is not None:
        return row, "session"
    if allow_ip and not request.cookies.get(USER_AUTO_LOGIN_SUPPRESSION_COOKIE):
        row = service.account_for_ip(get_client_ip(request))
        if row is not None:
            return row, "trusted_ip"
    return None, None


@router.get("/status", summary="读取普通用户访问状态")
def status(request: Request):
    enabled = user_access_enabled()
    if not enabled:
        return {"accessEnabled": False, "loggedIn": True, "user": None, "authMethod": "disabled"}
    user, method = _resolve_request_user(request)
    if method == "admin":
        return {
            "accessEnabled": True,
            "loggedIn": True,
            "user": {"id": 0, "name": "管理员", "status": "approved", "authMethod": "admin"},
            "authMethod": "admin",
        }
    payload = {
        "accessEnabled": True,
        "loggedIn": user is not None,
        "user": serialize_current_user(user, method) if user is not None else None,
        "authMethod": method,
    }
    if user is None or method != "trusted_ip":
        return payload

    # Turn the one-time trusted-IP lookup into a signed session. This avoids a
    # database lookup on each API call and keeps the chosen identity stable.
    response = JSONResponse(payload)
    response.set_cookie(
        USER_COOKIE_NAME,
        create_user_session(int(user.id)),
        **_cookie_options(request, max_age=SESSION_MAX_AGE_DAYS * 86400),
    )
    return response


@router.post("/register", status_code=202, summary="提交姓名与密码注册申请")
def register(request: Request, body: UserCredentialsRequest):
    try:
        user = UserAccountService().register(body.name, body.password, get_client_ip(request))
        return {"success": True, "status": "pending", "message": "申请已提交，等待管理员审核", "user": user}
    except UserAccountError as exc:
        return _error(exc, 409 if exc.code == "name_exists" else 400)


@router.post("/login", summary="普通用户密码登录并信任当前 IP")
def login(request: Request, body: UserCredentialsRequest):
    client_ip = get_client_ip(request)
    rate_limit_key = f"user:{client_ip}"
    if not check_rate_limit(rate_limit_key):
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "message": "登录失败次数过多，请稍后再试"},
        )
    try:
        user = UserAccountService().login(body.name, body.password, client_ip)
    except UserAccountError as exc:
        record_login_failure(rate_limit_key)
        status_code = 403 if exc.code in {"pending_approval", "account_unavailable"} else 401
        return _error(exc, status_code)
    clear_rate_limit(rate_limit_key)
    response = JSONResponse({
        "success": True,
        "user": serialize_current_user(user, "password"),
        "authMethod": "password",
    })
    response.set_cookie(
        USER_COOKIE_NAME,
        create_user_session(int(user.id)),
        **_cookie_options(request, max_age=SESSION_MAX_AGE_DAYS * 86400),
    )
    response.delete_cookie(USER_AUTO_LOGIN_SUPPRESSION_COOKIE, path="/")
    return response


@router.post("/logout", summary="退出普通用户会话")
def logout(request: Request):
    response = Response(status_code=204)
    response.delete_cookie(USER_COOKIE_NAME, path="/")
    # Without this marker the trusted-IP convenience login would immediately
    # undo logout and make account switching impossible on shared networks.
    response.set_cookie(
        USER_AUTO_LOGIN_SUPPRESSION_COOKIE,
        "1",
        **_cookie_options(request, max_age=86400),
    )
    return response


@router.get("/admin/users", summary="管理员查看用户申请")
def list_users(status: str | None = Query(None)):
    return {"users": UserAccountService().list_accounts(status=status)}


@router.post("/admin/users/{user_id}/approve", summary="管理员批准用户")
def approve_user(user_id: int):
    try:
        return {"user": UserAccountService().set_status(user_id, "approved")}
    except UserAccountError as exc:
        return _error(exc, 404 if exc.code == "not_found" else 400)


@router.post("/admin/users/{user_id}/reject", summary="管理员拒绝用户")
def reject_user(user_id: int):
    try:
        return {"user": UserAccountService().set_status(user_id, "rejected")}
    except UserAccountError as exc:
        return _error(exc, 404 if exc.code == "not_found" else 400)


@router.post("/admin/users/{user_id}/disable", summary="管理员停用用户")
def disable_user(user_id: int):
    try:
        return {"user": UserAccountService().set_status(user_id, "disabled")}
    except UserAccountError as exc:
        return _error(exc, 404 if exc.code == "not_found" else 400)
