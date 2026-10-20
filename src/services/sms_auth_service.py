"""Aliyun PNVS SMS authentication with persisted, single-use reservations.

Only provider-generated codes are used. Neither OTPs nor clear phone numbers
are stored. Remote verification never runs while holding the database lock.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import ssl
import time
from urllib.parse import quote

from sqlalchemy import func, select, text, update

from src.services.user_account_service import (
    DISABLED_PASSWORD, UserAccountError, _load_secret, hash_ip, mask_ip,
    normalize_display_name, registration_mode,
)
from src.storage import DatabaseManager, SmsAuthChallenge, UserAccount, UserPhoneIdentity, utc_naive_now

SMS_BROWSER_COOKIE = "dsa_sms_browser"
OTP_TTL = 300
MAX_ATTEMPTS = 5
SEND_INTERVAL = 60
PNVS_HOST = "dypnsapi.aliyuncs.com"
PNVS_VERSION = "2017-05-25"


class SmsAuthError(UserAccountError):
    def __init__(self, code: str, message: str, status: int = 400, retry_after: int | None = None):
        super().__init__(code, message)
        self.status, self.retry_after = status, retry_after


def sms_login_enabled() -> bool:
    enabled = os.getenv("SMS_AUTH_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    return enabled and all(os.getenv(key, "").strip() for key in (
        "ALIYUN_SMS_ACCESS_KEY_ID", "ALIYUN_SMS_ACCESS_KEY_SECRET", "ALIYUN_SMS_SIGN_NAME", "ALIYUN_SMS_TEMPLATE_CODE"))


def normalize_phone(value: str) -> str:
    phone = str(value).strip()
    if phone.startswith("+86"):
        phone = phone[3:]
    if not re.fullmatch(r"1[3-9][0-9]{9}", phone):
        raise SmsAuthError("invalid_phone", "请输入正确的中国大陆手机号")
    return phone


def _digest(kind: str, value: str) -> str:
    return hmac.new(_load_secret(), f"sms:{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def _limit(key: str, default: int) -> int:
    try:
        return max(1, min(10000, int(os.getenv(key, str(default)))))
    except ValueError:
        return default


def sign_rpc_request(host: str, action: str, version: str, params: dict[str, str],
                     access_key_id: str, secret: str, date: str, nonce: str) -> tuple[str, dict[str, str]]:
    """ACS3-HMAC-SHA256, following Aliyun's official fixed signing vector."""
    query = "&".join(f"{quote(k, safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(params.items()))
    payload_hash = hashlib.sha256(b"").hexdigest()
    headers = {"host": host, "x-acs-action": action, "x-acs-version": version,
               "x-acs-date": date, "x-acs-signature-nonce": nonce, "x-acs-content-sha256": payload_hash}
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{key}:{headers[key].strip()}\n" for key in sorted(headers))
    canonical = "\n".join(("POST", "/", query, canonical_headers, signed_headers, payload_hash))
    to_sign = "ACS3-HMAC-SHA256\n" + hashlib.sha256(canonical.encode()).hexdigest()
    signature = hmac.new(secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = f"ACS3-HMAC-SHA256 Credential={access_key_id},SignedHeaders={signed_headers},Signature={signature}"
    headers["Accept"] = "application/json"
    return query, headers


class AliyunSmsProvider:
    """Fixed TLS endpoint; no retries, redirects, environment proxies or logs.

    http.client is intentional: query-based PNVS carries the OTP, and generic
    HTTP client's request logging can otherwise expose it in diagnostic logs.
    """

    def __init__(self):
        if not sms_login_enabled():
            raise SmsAuthError("sms_unavailable", "短信登录暂未开通，请使用原账号密码登录", 503)
        self.key_id = os.environ["ALIYUN_SMS_ACCESS_KEY_ID"].strip()
        self.secret = os.environ["ALIYUN_SMS_ACCESS_KEY_SECRET"].strip()
        self.sign_name = os.environ["ALIYUN_SMS_SIGN_NAME"].strip()
        self.template = os.environ["ALIYUN_SMS_TEMPLATE_CODE"].strip()
        self.bind_template = os.getenv("ALIYUN_SMS_BIND_TEMPLATE_CODE", "").strip() or self.template
        self.scheme = os.getenv("ALIYUN_SMS_SCHEME_NAME", "").strip()
        if len(self.scheme) > 20:
            raise SmsAuthError("sms_unavailable", "短信服务配置需要管理员检查", 503)

    def _call(self, action: str, params: dict) -> dict:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        query, headers = sign_rpc_request(PNVS_HOST, action, PNVS_VERSION, params, self.key_id,
                                         self.secret, date, secrets.token_hex(16))
        connection = http.client.HTTPSConnection(PNVS_HOST, timeout=8, context=ssl.create_default_context())
        try:
            started = time.monotonic()
            connection.request("POST", "/?" + query, body=b"", headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("provider_http_error")
            data = bytearray()
            while chunk := response.read1(4096):
                data.extend(chunk)
                if len(data) > 65536 or time.monotonic() - started > 16:
                    raise ValueError("provider_response_limit")
            payload = json.loads(data)
            if not isinstance(payload, dict) or payload.get("Code") != "OK" or payload.get("Success") is not True:
                raise ValueError("provider_not_successful")
            return payload
        except Exception:
            # Never return/log exception text: it may contain the URL or OTP.
            raise SmsAuthError("sms_provider_unavailable", "短信服务暂时不可用，请稍后重新获取验证码", 503) from None
        finally:
            connection.close()

    def send(self, phone: str, challenge_id: str, scheme: str, purpose: str = "login") -> None:
        if purpose not in {"login", "bind"}:
            raise SmsAuthError("invalid_request", "验证码请求无效")
        params = {"PhoneNumber": phone, "CountryCode": "86", "SignName": self.sign_name,
                  "TemplateCode": self.bind_template if purpose == "bind" else self.template,
                  "TemplateParam": '{"code":"##code##","min":"5"}',
                  "CodeLength": "6", "CodeType": "1", "ValidTime": str(OTP_TTL),
                  "Interval": str(SEND_INTERVAL), "DuplicatePolicy": "1", "ReturnVerifyCode": "false",
                  "AutoRetry": "0", "OutId": challenge_id}
        if scheme:
            params["SchemeName"] = scheme
        self._call("SendSmsVerifyCode", params)

    def verify(self, phone: str, code: str, challenge_id: str, scheme: str) -> bool:
        params = {"PhoneNumber": phone, "CountryCode": "86", "VerifyCode": code,
                  "OutId": challenge_id, "CaseAuthPolicy": "1"}
        if scheme:
            params["SchemeName"] = scheme
        model = self._call("CheckSmsVerifyCode", params).get("Model")
        if not isinstance(model, dict):
            raise SmsAuthError("sms_provider_unavailable", "短信验证服务暂时不可用，请重新获取验证码", 503)
        # A successful API call is not a successful authentication.
        return model.get("VerifyResult") == "PASS" and model.get("OutId") == challenge_id


class SmsAuthService:
    def __init__(self, db: DatabaseManager | None = None, provider=None, clock=time.time):
        self.db = db or DatabaseManager.get_instance()
        self.provider = provider or AliyunSmsProvider()
        self.clock = clock

    @contextmanager
    def _transaction(self):
        with self.db.session_scope() as session:
            # This deployment uses SQLite. Fail closed on another engine until
            # its lock semantics have a separately verified implementation.
            if session.bind.dialect.name != "sqlite":
                raise SmsAuthError("sms_unavailable", "当前数据库暂不支持短信认证", 503)
            session.execute(text("BEGIN IMMEDIATE"))
            yield session

    def send(self, phone: str, purpose: str, browser: str, client_ip: str, user_id: int | None = None) -> dict:
        phone = normalize_phone(phone)
        if purpose not in {"login", "bind"} or not re.fullmatch(r"[A-Za-z0-9_-]{32,100}", browser):
            raise SmsAuthError("invalid_request", "验证码请求无效")
        if purpose == "bind" and not user_id:
            raise SmsAuthError("user_login_required", "请先用原账号密码登录，再绑定手机号", 401)
        now, identifier = self.clock(), secrets.token_urlsafe(32)
        phone_hash, ip_hash = _digest("phone", phone), hash_ip(client_ip)
        scheme = self.provider.scheme
        with self._transaction() as session:
            if purpose == "bind":
                row = session.get(UserAccount, user_id)
                if row is None or row.status != "approved":
                    raise SmsAuthError("account_unavailable", "账号当前不可用", 403)
            recent = SmsAuthChallenge.created_at > now - 86400
            latest = session.scalar(select(func.max(SmsAuthChallenge.created_at)).where(SmsAuthChallenge.phone_hash == phone_hash))
            if latest is not None and now - latest < SEND_INTERVAL:
                raise SmsAuthError("sms_rate_limited", "验证码发送频繁，请稍后再试", 429, max(1, int(SEND_INTERVAL - (now - latest))))
            for condition, key, default in (
                (SmsAuthChallenge.phone_hash == phone_hash, "SMS_AUTH_PHONE_DAILY_LIMIT", 5),
                (SmsAuthChallenge.ip_hash == ip_hash, "SMS_AUTH_IP_DAILY_LIMIT", 20),
                (True, "SMS_AUTH_GLOBAL_DAILY_LIMIT", 100),
            ):
                count = session.scalar(select(func.count()).select_from(SmsAuthChallenge).where(recent, condition))
                if count >= _limit(key, default):
                    raise SmsAuthError("sms_rate_limited", "今日短信请求次数已达上限，请稍后再试", 429, 3600)
            # Only the newest challenge for this phone is active across purposes.
            session.execute(update(SmsAuthChallenge).where(SmsAuthChallenge.phone_hash == phone_hash,
                            SmsAuthChallenge.state.in_(["reserved", "sent", "checking"])).values(state="superseded"))
            session.add(SmsAuthChallenge(id=identifier, phone_hash=phone_hash, ip_hash=ip_hash,
                        browser_hash=_digest("browser", browser), purpose=purpose, user_id=user_id if purpose == "bind" else None,
                        scheme_name=scheme, state="reserved", created_at=now, expires_at=now + OTP_TTL))
        # Reservations count even on failure/timeout: retrying uncertain sends
        # automatically can duplicate fees and defeat multi-worker limits.
        try:
            self.provider.send(phone, identifier, scheme, purpose)
        except Exception:
            with self._transaction() as session:
                session.execute(update(SmsAuthChallenge).where(SmsAuthChallenge.id == identifier,
                                SmsAuthChallenge.state == "reserved").values(state="failed"))
            raise SmsAuthError("sms_provider_unavailable", "短信发送暂未成功，请稍后重新获取验证码", 503) from None
        with self._transaction() as session:
            row = session.get(SmsAuthChallenge, identifier)
            if row.state != "reserved" or row.expires_at <= self.clock():
                raise SmsAuthError("sms_challenge_expired", "验证码请求已过期，请重新获取")
            row.state = "sent"
        return {"challengeId": identifier, "expiresIn": max(0, int(now + OTP_TTL - self.clock())), "retryAfter": SEND_INTERVAL}

    def verify(self, phone: str, code: str, challenge_id: str, browser: str, client_ip: str,
               *, purpose="login", user_id: int | None = None, name: str | None = None) -> UserAccount:
        phone = normalize_phone(phone)
        if not re.fullmatch(r"[0-9]{6}", code) or not re.fullmatch(r"[A-Za-z0-9_-]{32,100}", browser):
            raise SmsAuthError("invalid_code", "请输入6位验证码")
        if name:
            normalize_display_name(name)  # Do not consume a code on invalid UI input.
        lease, now = secrets.token_hex(24), self.clock()
        phone_hash = _digest("phone", phone)
        with self._transaction() as session:
            row = session.get(SmsAuthChallenge, challenge_id)
            if (row is None or row.state != "sent" or row.expires_at <= now or row.attempts >= MAX_ATTEMPTS
                    or row.purpose != purpose or row.user_id != (user_id if purpose == "bind" else None)
                    or not hmac.compare_digest(row.phone_hash, phone_hash)
                    or not hmac.compare_digest(row.browser_hash, _digest("browser", browser))):
                raise SmsAuthError("invalid_challenge", "验证码已失效或不属于当前浏览器，请重新获取", 400)
            row.state, row.lease = "checking", lease
            row.attempts += 1
            scheme = row.scheme_name
        try:
            verified = self.provider.verify(phone, code, challenge_id, scheme)
        except Exception:
            with self._transaction() as session:
                session.execute(update(SmsAuthChallenge).where(SmsAuthChallenge.id == challenge_id,
                                SmsAuthChallenge.lease == lease, SmsAuthChallenge.state == "checking").values(state="failed", lease=None))
            raise SmsAuthError("sms_provider_unavailable", "验证码校验暂未完成，请重新获取验证码", 503) from None
        error = None
        user = None
        with self._transaction() as session:
            row = session.get(SmsAuthChallenge, challenge_id)
            if row.state != "checking" or row.lease != lease or row.expires_at <= self.clock():
                raise SmsAuthError("invalid_challenge", "验证码已失效，请重新获取")
            row.lease = None
            if verified is not True:
                row.state = "failed" if row.attempts >= MAX_ATTEMPTS else "sent"
                error = SmsAuthError("invalid_code", "验证码错误或已失效")
            else:
                row.state, row.consumed_at = "consumed", self.clock()
                try:
                    user = self._resolve_account(session, phone, phone_hash, purpose, user_id, name, client_ip)
                except UserAccountError as exc:
                    error = exc  # Commit consumption even if a binding conflicts.
                if user is not None:
                    session.flush()
                    session.expunge(user)
        if error:
            raise error
        return user

    def _resolve_account(self, session, phone, phone_hash, purpose, user_id, name, client_ip):
        binding = session.get(UserPhoneIdentity, phone_hash)
        if purpose == "bind":
            user = session.get(UserAccount, user_id)
            if not user or user.status != "approved":
                raise SmsAuthError("account_unavailable", "账号当前不可用", 403)
            if binding and binding.user_id != user.id:
                raise SmsAuthError("phone_in_use", "该手机号已绑定其他账号，不会合并账号数据", 409)
            own = session.scalar(select(UserPhoneIdentity).where(UserPhoneIdentity.user_id == user.id))
            if own and own.phone_hash != phone_hash:
                raise SmsAuthError("phone_already_bound", "当前账号已绑定手机号，不能直接更换", 409)
        elif binding:
            user = session.get(UserAccount, binding.user_id)
            if not user or user.status != "approved":
                raise SmsAuthError("account_unavailable", "账号当前不可用，请联系管理员", 403)
        else:
            if registration_mode() != "self_service":
                raise SmsAuthError("registration_requires_approval", "请先注册原账号并完成审核，再绑定手机号", 403)
            display = normalize_display_name(name)[0] if name else f"用户{phone[-4:]}"
            now = utc_naive_now()
            user = UserAccount(display_name=display, normalized_name="sms:" + secrets.token_hex(24),
                               password_salt=DISABLED_PASSWORD, password_hash=DISABLED_PASSWORD, status="approved",
                               registration_ip_hash=hash_ip(client_ip), registration_ip_label=mask_ip(client_ip),
                               approved_at=now, approved_by="verified_sms", created_at=now, updated_at=now)
            session.add(user)
            session.flush()
        if not binding:
            session.add(UserPhoneIdentity(phone_hash=phone_hash, user_id=user.id,
                        phone_label=f"{phone[:3]}****{phone[-4:]}", verified_at=self.clock()))
        user.last_login_at, user.last_login_ip_label = utc_naive_now(), mask_ip(client_ip)
        return user
