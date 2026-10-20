# -*- coding: utf-8 -*-
"""Public user registration/login and administrator approval endpoints."""

from __future__ import annotations

import os
import re
import secrets
from typing import Literal
from urllib.parse import urlsplit

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
    registration_mode,
    user_access_enabled,
)
from src.services.sms_auth_service import (
    SMS_BROWSER_COOKIE, SmsAuthError, SmsAuthService, sms_login_enabled,
)

router = APIRouter()

_PRIVATE_STATUS_HEADERS = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "Vary": "Cookie, Authorization",
}


class UserCredentialsRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class SmsSendRequest(BaseModel):
    phone: str = Field(min_length=11, max_length=16)
    purpose: Literal["login", "bind"] = "login"


class SmsVerifyRequest(BaseModel):
    phone: str = Field(min_length=11, max_length=16)
    code: str = Field(min_length=6, max_length=6)
    challengeId: str = Field(min_length=32, max_length=64)
    name: str | None = Field(None, max_length=40)


class SmsBindRequest(SmsVerifyRequest):
    currentPassword: str = Field(min_length=1, max_length=256)


def _current_user_payload(user, method: str) -> dict:
    return {**serialize_current_user(user, method), **UserAccountService().phone_status(int(user.id))}


def _check_sms_origin(request: Request) -> None:
    """SMS is browser-bound and requires an explicit same-origin POST."""
    origin = request.headers.get("origin", "")
    try:
        parsed = urlsplit(origin)
        expected_scheme = "https" if _cookie_options(request, max_age=1)["secure"] else "http"
        valid = (parsed.scheme == expected_scheme and parsed.netloc == request.url.netloc
                 and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid or request.headers.get("sec-fetch-site") == "cross-site":
        raise SmsAuthError("invalid_origin", "请在本站页面发起验证码请求", 403)


def _sms_error(exc: UserAccountError) -> JSONResponse:
    status = exc.status if isinstance(exc, SmsAuthError) else 400
    payload = {"error": exc.code, "message": exc.message}
    headers = dict(_PRIVATE_STATUS_HEADERS)
    if isinstance(exc, SmsAuthError) and exc.retry_after is not None:
        payload["retryAfter"] = exc.retry_after
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(payload, status_code=status, headers=headers)


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


def _resolve_request_user(request: Request):
    # The already authenticated operator can inspect the front office without
    # creating a normal user account; private resources remain in owner scope 0.
    admin_cookie = request.cookies.get(ADMIN_COOKIE_NAME)
    if admin_cookie and verify_admin_session(admin_cookie):
        return None, "admin"
    service = UserAccountService()
    row = service.account_for_session(request.cookies.get(USER_COOKIE_NAME, ""))
    if row is not None:
        return row, "session"
    return None, None


@router.get("/status", summary="读取普通用户访问状态")
def status(request: Request):
    enabled = user_access_enabled()
    mode = {"registrationMode": registration_mode(), "smsLoginEnabled": sms_login_enabled()}
    if not enabled:
        return JSONResponse(
            content={"accessEnabled": False, "loggedIn": True, "user": None, "authMethod": "disabled", **mode},
            headers=_PRIVATE_STATUS_HEADERS,
        )
    user, method = _resolve_request_user(request)
    if method == "admin":
        return JSONResponse(
            content={
                "accessEnabled": True,
                "loggedIn": True,
                "user": {"id": 0, "name": "管理员", "status": "approved", "authMethod": "admin"},
                "authMethod": "admin",
                **mode,
            },
            headers=_PRIVATE_STATUS_HEADERS,
        )
    payload = {
        "accessEnabled": True,
        "loggedIn": user is not None,
        "user": _current_user_payload(user, method) if user is not None else None,
        "authMethod": method,
        **mode,
    }
    return JSONResponse(content=payload, headers=_PRIVATE_STATUS_HEADERS)


@router.post("/register", status_code=202, summary="提交姓名与密码注册申请")
def register(request: Request, body: UserCredentialsRequest):
    try:
        user = UserAccountService().register(body.name, body.password, get_client_ip(request))
        return {"success": True, "status": "pending", "message": "申请已提交，等待管理员审核", "user": user}
    except UserAccountError as exc:
        return _error(exc, 409 if exc.code == "name_exists" else 403 if exc.code == "sms_required" else 400)


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
        "user": _current_user_payload(user, "password"),
        "authMethod": "password",
    })
    response.set_cookie(
        USER_COOKIE_NAME,
        create_user_session(int(user.id)),
        **_cookie_options(request, max_age=SESSION_MAX_AGE_DAYS * 86400),
    )
    response.delete_cookie(USER_AUTO_LOGIN_SUPPRESSION_COOKIE, path="/")
    return response


@router.post("/sms/send", summary="发送手机号登录或绑定验证码")
def send_sms(request: Request, body: SmsSendRequest):
    try:
        _check_sms_origin(request)
        user_id = None
        if body.purpose == "bind":
            user = UserAccountService().account_for_session(request.cookies.get(USER_COOKIE_NAME, ""))
            if user is None:
                raise SmsAuthError("user_login_required", "请先用原账号密码登录，再绑定手机号", 401)
            user_id = int(user.id)
        browser = request.cookies.get(SMS_BROWSER_COOKIE, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,100}", browser):
            browser = secrets.token_urlsafe(32)
        result = SmsAuthService().send(body.phone, body.purpose, browser, get_client_ip(request), user_id)
        response = JSONResponse(result, headers=_PRIVATE_STATUS_HEADERS)
        response.set_cookie(SMS_BROWSER_COOKIE, browser, **_cookie_options(request, max_age=86400))
        return response
    except UserAccountError as exc:
        return _sms_error(exc)


def _verify_sms(request: Request, body: SmsVerifyRequest, *, purpose: str):
    try:
        _check_sms_origin(request)
        user_id = None
        if purpose == "bind":
            accounts = UserAccountService()
            user = accounts.account_for_session(request.cookies.get(USER_COOKIE_NAME, ""))
            if user is None:
                raise SmsAuthError("user_login_required", "请先用原账号密码登录，再绑定手机号", 401)
            user_id = int(user.id)
            rate_limit_key = f"user:{get_client_ip(request)}"
            if not check_rate_limit(rate_limit_key):
                raise SmsAuthError("rate_limited", "登录失败次数过多，请稍后再试", 429)
            try:
                accounts.verify_current_password(user_id, body.currentPassword)
            except UserAccountError as exc:
                record_login_failure(rate_limit_key)
                raise SmsAuthError(exc.code, exc.message, 401 if exc.code == "invalid_credentials" else 403) from None
            clear_rate_limit(rate_limit_key)
        user = SmsAuthService().verify(body.phone, body.code, body.challengeId,
                    request.cookies.get(SMS_BROWSER_COOKIE, ""), get_client_ip(request),
                    purpose=purpose, user_id=user_id, name=body.name)
        response = JSONResponse({"success": True, "user": _current_user_payload(user, "sms"),
                                 "authMethod": "sms"}, headers=_PRIVATE_STATUS_HEADERS)
        response.set_cookie(USER_COOKIE_NAME, create_user_session(int(user.id)),
                            **_cookie_options(request, max_age=SESSION_MAX_AGE_DAYS * 86400))
        response.delete_cookie(USER_AUTO_LOGIN_SUPPRESSION_COOKIE, path="/")
        return response
    except UserAccountError as exc:
        return _sms_error(exc)


@router.post("/sms/login", summary="手机号验证码登录或自助注册")
def sms_login(request: Request, body: SmsVerifyRequest):
    return _verify_sms(request, body, purpose="login")


@router.post("/sms/bind", summary="为原账号绑定已验证手机号")
def bind_sms(request: Request, body: SmsBindRequest):
    return _verify_sms(request, body, purpose="bind")


@router.post("/logout", summary="退出普通用户会话")
def logout(request: Request):
    response = Response(status_code=204)
    response.delete_cookie(USER_COOKIE_NAME, path="/")
    # Remove the legacy trusted-IP suppression marker as IP addresses are no
    # longer accepted as authentication credentials.
    response.delete_cookie(USER_AUTO_LOGIN_SUPPRESSION_COOKIE, path="/")
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
