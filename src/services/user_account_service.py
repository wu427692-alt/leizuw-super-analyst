# -*- coding: utf-8 -*-
"""Front-office accounts, approval workflow, sessions, and private watchlists."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, desc, select
from sqlalchemy.exc import IntegrityError

from src.storage import DatabaseManager, UserAccount, UserTrustedIp, UserWatchlistItem, utc_naive_now

USER_COOKIE_NAME = "dsa_user_session"
USER_AUTO_LOGIN_SUPPRESSION_COOKIE = "dsa_user_auto_login_suppressed"
SESSION_MAX_AGE_DAYS = 30
USER_SESSION_VERSION = "v2"
PASSWORD_ITERATIONS = 240_000
MIN_PASSWORD_LENGTH = 6
MAX_TRUSTED_IPS = 5
_SPACE_RE = re.compile(r"\s+")
_SECRET_CACHE: dict[Path, bytes] = {}
_SECRET_CACHE_LOCK = threading.Lock()


class UserAccountError(ValueError):
    """Expected account workflow error with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def user_access_enabled() -> bool:
    """Return whether front-office account enforcement is enabled."""
    return os.getenv("USER_ACCESS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def normalize_display_name(value: str) -> tuple[str, str]:
    display = _SPACE_RE.sub(" ", str(value or "").strip())
    if len(display) < 2 or len(display) > 40:
        raise UserAccountError("invalid_name", "姓名需为 2 至 40 个字符")
    normalized = display.casefold()
    return display, normalized


def _validate_password(value: str) -> str:
    password = str(value or "")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserAccountError("invalid_password", f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > 256:
        raise UserAccountError("invalid_password", "密码不能超过 256 位")
    return password


def _hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    real_salt = salt or secrets.token_bytes(24)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), real_salt, PASSWORD_ITERATIONS,
    )
    return (
        base64.urlsafe_b64encode(real_salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def _verify_password(password: str, salt_text: str, hash_text: str) -> bool:
    try:
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_text.encode("ascii"))
    except Exception:
        return False
    computed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS,
    )
    return hmac.compare_digest(computed, expected)


def _secret_path() -> Path:
    db_path = Path(os.getenv("DATABASE_PATH", "./data/stock_analysis.db")).resolve()
    return db_path.parent / ".user_auth_secret"


def _load_secret() -> bytes:
    path = _secret_path()
    with _SECRET_CACHE_LOCK:
        cached = _SECRET_CACHE.get(path)
    if cached is not None:
        return cached
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.read_bytes()
        if len(value) == 32:
            with _SECRET_CACHE_LOCK:
                _SECRET_CACHE[path] = value
            return value
    except FileNotFoundError:
        pass
    value = secrets.token_bytes(32)
    try:
        with path.open("xb") as handle:
            handle.write(value)
        path.chmod(0o600)
        with _SECRET_CACHE_LOCK:
            _SECRET_CACHE[path] = value
        return value
    except FileExistsError:
        existing = path.read_bytes()
        if len(existing) == 32:
            with _SECRET_CACHE_LOCK:
                _SECRET_CACHE[path] = existing
            return existing
        raise RuntimeError("用户认证密钥文件损坏")


def normalize_client_ip(value: str) -> str:
    raw = str(value or "").strip()
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return raw[:128] or "unknown"


def mask_ip(value: str) -> str:
    ip = normalize_client_ip(value)
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return "未知网络"
    if parsed.version == 4:
        parts = ip.split(".")
        return f"{parts[0]}.{parts[1]}.*.*"
    return f"{':'.join(parsed.exploded.split(':')[:3])}:…"


def hash_ip(value: str) -> str:
    return hmac.new(_load_secret(), normalize_client_ip(value).encode("utf-8"), hashlib.sha256).hexdigest()


def create_user_session(user_id: int) -> str:
    issued_at = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    payload = f"{USER_SESSION_VERSION}.{int(user_id)}.{issued_at}.{nonce}"
    signature = hmac.new(_load_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def parse_user_session(value: str) -> Optional[int]:
    parts = str(value or "").split(".")
    if len(parts) != 5 or parts[0] != USER_SESSION_VERSION:
        return None
    payload = ".".join(parts[:4])
    expected = hmac.new(_load_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(parts[4], expected):
        return None
    try:
        user_id, issued_at = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if user_id <= 0 or time.time() - issued_at > SESSION_MAX_AGE_DAYS * 86400:
        return None
    return user_id


class UserAccountService:
    """Transaction boundary for public users and their private data."""

    _session_cache: dict[str, tuple[float, UserAccount]] = {}
    _session_cache_lock = threading.Lock()
    _session_cache_limit = 2_048

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or DatabaseManager.get_instance()

    @staticmethod
    def _serialize_user(row: UserAccount, *, include_private: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": int(row.id),
            "name": row.display_name,
            "status": row.status,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
            "approvedAt": row.approved_at.isoformat() if row.approved_at else None,
            "lastLoginAt": row.last_login_at.isoformat() if row.last_login_at else None,
        }
        if include_private:
            data.update({
                "registrationIp": row.registration_ip_label,
                "lastLoginIp": row.last_login_ip_label,
                "approvedBy": row.approved_by,
            })
        return data

    def register(self, name: str, password: str, client_ip: str) -> dict[str, Any]:
        display, normalized = normalize_display_name(name)
        password = _validate_password(password)
        salt, password_hash = _hash_password(password)
        ip_digest = hash_ip(client_ip)
        now = utc_naive_now()
        try:
            with self.db.session_scope() as session:
                existing = session.execute(
                    select(UserAccount).where(UserAccount.normalized_name == normalized)
                ).scalar_one_or_none()
                if existing and existing.status != "rejected":
                    raise UserAccountError("name_exists", "该姓名已注册，请直接登录或等待审核")
                if existing:
                    existing.display_name = display
                    existing.password_salt = salt
                    existing.password_hash = password_hash
                    existing.status = "pending"
                    existing.registration_ip_hash = ip_digest
                    existing.registration_ip_label = mask_ip(client_ip)
                    existing.approved_at = None
                    existing.approved_by = None
                    existing.updated_at = now
                    row = existing
                else:
                    row = UserAccount(
                        display_name=display,
                        normalized_name=normalized,
                        password_salt=salt,
                        password_hash=password_hash,
                        status="pending",
                        registration_ip_hash=ip_digest,
                        registration_ip_label=mask_ip(client_ip),
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                session.flush()
                result = self._serialize_user(row)
        except IntegrityError as exc:
            raise UserAccountError("name_exists", "该姓名已注册") from exc
        return result

    def login(self, name: str, password: str, client_ip: str) -> UserAccount:
        _, normalized = normalize_display_name(name)
        password = _validate_password(password)
        with self.db.session_scope() as session:
            row = session.execute(
                select(UserAccount).where(UserAccount.normalized_name == normalized)
            ).scalar_one_or_none()
            if row is None or not _verify_password(password, row.password_salt, row.password_hash):
                raise UserAccountError("invalid_credentials", "姓名或密码错误")
            if row.status == "pending":
                raise UserAccountError("pending_approval", "访问申请正在等待管理员审核")
            if row.status != "approved":
                raise UserAccountError("account_unavailable", "账号当前不可用，请联系管理员")
            self._trust_ip_in_session(session, row, client_ip, source="password_login")
            row.last_login_at = utc_naive_now()
            row.last_login_ip_label = mask_ip(client_ip)
            session.flush()
            session.expunge(row)
            return row

    def account_for_session(self, cookie_value: str) -> Optional[UserAccount]:
        user_id = parse_user_session(cookie_value)
        if not user_id:
            return None
        cache_key = hashlib.sha256(cookie_value.encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._session_cache_lock:
            cached = self._session_cache.get(cache_key)
            if cached is not None and cached[0] > now and int(cached[1].id) == user_id:
                return cached[1]
            if cached is not None:
                self._session_cache.pop(cache_key, None)
        with self.db.get_session() as session:
            row = session.get(UserAccount, user_id)
            if row is None or row.status != "approved":
                return None
            session.expunge(row)
        try:
            ttl = max(1.0, min(float(os.getenv("USER_SESSION_CACHE_SECONDS", "20")), 300.0))
        except ValueError:
            ttl = 20.0
        with self._session_cache_lock:
            if len(self._session_cache) >= self._session_cache_limit:
                expired = [key for key, value in self._session_cache.items() if value[0] <= now]
                for key in expired:
                    self._session_cache.pop(key, None)
                while len(self._session_cache) >= self._session_cache_limit:
                    self._session_cache.pop(next(iter(self._session_cache)))
            self._session_cache[cache_key] = (now + ttl, row)
        return row

    @classmethod
    def _invalidate_user_sessions(cls, user_id: int) -> None:
        with cls._session_cache_lock:
            stale_keys = [
                key for key, value in cls._session_cache.items()
                if int(value[1].id) == int(user_id)
            ]
            for key in stale_keys:
                cls._session_cache.pop(key, None)

    def get_account(self, user_id: int) -> Optional[UserAccount]:
        with self.db.get_session() as session:
            row = session.get(UserAccount, int(user_id))
            if row is not None:
                session.expunge(row)
            return row

    def list_accounts(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        with self.db.get_session() as session:
            statement = select(UserAccount)
            if status:
                statement = statement.where(UserAccount.status == status)
            rows = session.execute(statement.order_by(desc(UserAccount.created_at))).scalars().all()
            user_ids = [int(row.id) for row in rows]
            trusted_counts: dict[int, int] = {}
            if user_ids:
                trusted = session.execute(
                    select(UserTrustedIp.user_id).where(UserTrustedIp.user_id.in_(user_ids))
                ).scalars().all()
                for user_id in trusted:
                    trusted_counts[int(user_id)] = trusted_counts.get(int(user_id), 0) + 1
            result = []
            for row in rows:
                item = self._serialize_user(row, include_private=True)
                item["trustedIpCount"] = trusted_counts.get(int(row.id), 0)
                result.append(item)
            return result

    def set_status(self, user_id: int, status: str, client_ip: Optional[str] = None) -> dict[str, Any]:
        if status not in {"approved", "rejected", "disabled"}:
            raise UserAccountError("invalid_status", "不支持的账号状态")
        with self.db.session_scope() as session:
            row = session.get(UserAccount, int(user_id))
            if row is None:
                raise UserAccountError("not_found", "用户不存在")
            row.status = status
            if status == "approved":
                row.approved_at = utc_naive_now()
                row.approved_by = "administrator"
                registration_digest = row.registration_ip_hash
                if registration_digest:
                    trusted = session.execute(
                        select(UserTrustedIp).where(
                            UserTrustedIp.user_id == row.id,
                            UserTrustedIp.ip_hash == registration_digest,
                        )
                    ).scalar_one_or_none()
                    if trusted is None:
                        session.add(UserTrustedIp(
                            user_id=row.id,
                            ip_hash=registration_digest,
                            ip_label=row.registration_ip_label,
                            source="approval",
                        ))
                if client_ip:
                    self._trust_ip_in_session(session, row, client_ip, source="admin_approval")
            session.flush()
            result = self._serialize_user(row, include_private=True)
        self._invalidate_user_sessions(user_id)
        return result

    def _trust_ip_in_session(self, session, user: UserAccount, client_ip: str, *, source: str) -> None:
        digest = hash_ip(client_ip)
        existing = session.execute(
            select(UserTrustedIp).where(
                UserTrustedIp.user_id == user.id,
                UserTrustedIp.ip_hash == digest,
            )
        ).scalar_one_or_none()
        if existing:
            existing.last_seen_at = utc_naive_now()
            return
        trusted = session.execute(
            select(UserTrustedIp)
            .where(UserTrustedIp.user_id == user.id)
            .order_by(UserTrustedIp.last_seen_at)
        ).scalars().all()
        while len(trusted) >= MAX_TRUSTED_IPS:
            session.delete(trusted.pop(0))
        session.add(UserTrustedIp(
            user_id=user.id,
            ip_hash=digest,
            ip_label=mask_ip(client_ip),
            source=source,
        ))

    def list_watchlist(self, user_id: int) -> list[str]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(UserWatchlistItem)
                .where(UserWatchlistItem.user_id == int(user_id))
                .order_by(UserWatchlistItem.created_at, UserWatchlistItem.id)
            ).scalars().all()
            return [row.display_symbol or row.symbol for row in rows]

    def add_watchlist(self, user_id: int, symbol: str, display_symbol: Optional[str] = None) -> list[str]:
        normalized = str(symbol or "").strip().upper()
        with self.db.session_scope() as session:
            existing = session.execute(
                select(UserWatchlistItem).where(
                    UserWatchlistItem.user_id == int(user_id),
                    UserWatchlistItem.symbol == normalized,
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(UserWatchlistItem(
                    user_id=int(user_id), symbol=normalized,
                    display_symbol=(display_symbol or normalized).strip(),
                ))
        return self.list_watchlist(user_id)

    def remove_watchlist(self, user_id: int, symbol: str) -> list[str]:
        normalized = str(symbol or "").strip().upper()
        with self.db.session_scope() as session:
            session.execute(delete(UserWatchlistItem).where(
                UserWatchlistItem.user_id == int(user_id),
                UserWatchlistItem.symbol == normalized,
            ))
        return self.list_watchlist(user_id)

    def seed_watchlist_if_empty(self, user_id: int, symbols: list[str]) -> list[str]:
        existing = self.list_watchlist(user_id)
        if existing:
            return existing
        for symbol in symbols:
            self.add_watchlist(user_id, symbol, symbol)
        return self.list_watchlist(user_id)


def serialize_current_user(row: UserAccount, auth_method: str) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "name": row.display_name,
        "status": row.status,
        "authMethod": auth_method,
    }


def user_session_namespace(user_id: int) -> str:
    return f"web_user_{int(user_id)}"


def scope_session_id(user_id: int, session_id: str) -> str:
    prefix = f"{user_session_namespace(user_id)}:"
    value = str(session_id or "").strip()
    if value.startswith(prefix):
        return value
    return f"{prefix}{value}"


def unscope_session_id(user_id: int, session_id: str) -> str:
    prefix = f"{user_session_namespace(user_id)}:"
    value = str(session_id or "")
    return value[len(prefix):] if value.startswith(prefix) else value
