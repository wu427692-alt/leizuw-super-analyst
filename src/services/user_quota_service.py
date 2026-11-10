# -*- coding: utf-8 -*-
"""Per-user daily quotas and quota-extension requests.

Counters and requests live in the main SQLite database. Administrators
(user id 0 / no session) are not limited.

Limits are read from the environment or the private runtime ``ENV_FILE``:

    USER_DAILY_DOWNLOAD_TASKS=5
    USER_DAILY_CHAT_MESSAGES=50

An approved request adds its amount to that user's limit for the approval day
only. Users may have at most one pending request per feature.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Optional

from dotenv import dotenv_values
from sqlalchemy import text

from src.storage import DatabaseManager

FEATURE_DOWNLOAD = "download_task"
FEATURE_CHAT = "chat_message"

_DEFAULTS = {
    FEATURE_DOWNLOAD: 5,
    FEATURE_CHAT: 50,
}
_ENV_KEYS = {
    FEATURE_DOWNLOAD: "USER_DAILY_DOWNLOAD_TASKS",
    FEATURE_CHAT: "USER_DAILY_CHAT_MESSAGES",
}
_LABELS = {
    FEATURE_DOWNLOAD: "数据下载",
    FEATURE_CHAT: "问股",
}
_MAX_REQUEST = {
    FEATURE_DOWNLOAD: 20,
    FEATURE_CHAT: 200,
}


class QuotaExceeded(Exception):
    """Raised when a user has used up today's allowance."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class QuotaRequestError(ValueError):
    """Raised for invalid or duplicate quota-extension requests."""


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        env_path = os.environ.get("ENV_FILE")
        if env_path and os.path.isfile(env_path):
            try:
                raw = dotenv_values(env_path).get(key)
            except Exception:
                raw = None
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    return max(0, value)


class UserQuotaService:
    _table_ready = False

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or DatabaseManager.get_instance()
        if not UserQuotaService._table_ready:
            self._ensure_tables()
            UserQuotaService._table_ready = True

    def _ensure_tables(self) -> None:
        with self.db.session_scope() as session:
            session.execute(text(
                """
                CREATE TABLE IF NOT EXISTS user_usage_counters (
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, day, feature)
                )
                """
            ))
            session.execute(text(
                """
                CREATE TABLE IF NOT EXISTS user_quota_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    feature TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    grant_day TEXT
                )
                """
            ))
            session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_quota_requests_status "
                "ON user_quota_requests(status, requested_at)"
            ))
            session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_quota_requests_user "
                "ON user_quota_requests(user_id, feature, status)"
            ))

    def limit(self, feature: str) -> int:
        return _env_int(_ENV_KEYS[feature], _DEFAULTS[feature])

    def bonus(self, user_id: int, feature: str) -> int:
        day = date.today().isoformat()
        with self.db.session_scope() as session:
            row = session.execute(
                text(
                    "SELECT COALESCE(SUM(amount), 0) FROM user_quota_requests "
                    "WHERE user_id=:uid AND feature=:feature "
                    "AND status='approved' AND grant_day=:day"
                ),
                {"uid": int(user_id), "feature": feature, "day": day},
            ).first()
            return int(row[0]) if row else 0

    def effective_limit(self, user_id: int, feature: str) -> int:
        return self.limit(feature) + self.bonus(int(user_id), feature)

    def used(self, user_id: int, feature: str) -> int:
        day = date.today().isoformat()
        with self.db.session_scope() as session:
            row = session.execute(
                text(
                    "SELECT count FROM user_usage_counters "
                    "WHERE user_id=:uid AND day=:day AND feature=:feature"
                ),
                {"uid": int(user_id), "day": day, "feature": feature},
            ).first()
            return int(row[0]) if row else 0

    def consume(self, user_id: Optional[int], feature: str, amount: int = 1) -> dict[str, Any]:
        """Reserve ``amount`` units or raise :class:`QuotaExceeded`."""
        if not user_id or int(user_id) <= 0:
            return {"unlimited": True, "used": 0, "limit": None, "remaining": None}

        uid = int(user_id)
        day = date.today().isoformat()
        limit = self.effective_limit(uid, feature)
        now = datetime.now(timezone.utc).isoformat()

        with self.db.session_scope() as session:
            row = session.execute(
                text(
                    "SELECT count FROM user_usage_counters "
                    "WHERE user_id=:uid AND day=:day AND feature=:feature"
                ),
                {"uid": uid, "day": day, "feature": feature},
            ).first()
            used = int(row[0]) if row else 0
            if used + amount > limit:
                raise QuotaExceeded(
                    f"今日{_LABELS[feature]}次数已用完（上限 {limit} 次）。"
                    "请到 /quota-request 提交额度申请，管理员批准后当天生效"
                )
            session.execute(
                text(
                    """
                    INSERT INTO user_usage_counters (user_id, day, feature, count, updated_at)
                    VALUES (:uid, :day, :feature, :amount, :now)
                    ON CONFLICT(user_id, day, feature)
                    DO UPDATE SET count = count + :amount, updated_at = :now
                    """
                ),
                {"uid": uid, "day": day, "feature": feature, "amount": amount, "now": now},
            )
            new_used = used + amount

        return {
            "unlimited": False,
            "used": new_used,
            "limit": limit,
            "remaining": max(0, limit - new_used),
        }

    def pending_request(self, user_id: int) -> Optional[dict[str, Any]]:
        with self.db.session_scope() as session:
            row = session.execute(
                text(
                    "SELECT id, feature, amount, reason, status, requested_at "
                    "FROM user_quota_requests "
                    "WHERE user_id=:uid AND status='pending' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"uid": int(user_id)},
            ).first()
            if not row:
                return None
            return {
                "id": int(row[0]),
                "feature": row[1],
                "amount": int(row[2]),
                "reason": row[3],
                "status": row[4],
                "requestedAt": row[5],
            }

    def request_extension(
        self,
        user_id: int,
        feature: str,
        amount: int,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        if feature not in _MAX_REQUEST:
            raise QuotaRequestError("不支持的额度类型")
        amount = int(amount)
        if amount < 1 or amount > _MAX_REQUEST[feature]:
            raise QuotaRequestError(
                f"申请数量需在 1-{_MAX_REQUEST[feature]} 之间"
            )
        uid = int(user_id)
        if uid <= 0:
            raise QuotaRequestError("请先登录后再申请")

        with self.db.session_scope() as session:
            existing = session.execute(
                text(
                    "SELECT id FROM user_quota_requests "
                    "WHERE user_id=:uid AND feature=:feature AND status='pending' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"uid": uid, "feature": feature},
            ).first()
            if existing:
                raise QuotaRequestError("已有待审批的申请，请等待管理员处理")
            now = datetime.now(timezone.utc).isoformat()
            result = session.execute(
                text(
                    """
                    INSERT INTO user_quota_requests
                        (user_id, feature, amount, reason, status, requested_at)
                    VALUES (:uid, :feature, :amount, :reason, 'pending', :now)
                    """
                ),
                {
                    "uid": uid,
                    "feature": feature,
                    "amount": amount,
                    "reason": (reason or "").strip()[:500],
                    "now": now,
                },
            )
            request_id = int(result.lastrowid)

        return {
            "id": request_id,
            "feature": feature,
            "amount": amount,
            "status": "pending",
            "requestedAt": now,
        }

    def list_requests(self, status: Optional[str] = "pending", limit: int = 100) -> list[dict[str, Any]]:
        sql = (
            "SELECT r.id, r.user_id, u.display_name, r.feature, r.amount, r.reason, "
            "r.status, r.requested_at, r.decided_at, r.decided_by, r.grant_day "
            "FROM user_quota_requests r "
            "LEFT JOIN user_accounts u ON u.id = r.user_id "
        )
        params: dict[str, Any] = {"limit": int(limit)}
        if status:
            sql += "WHERE r.status=:status "
            params["status"] = status
        sql += "ORDER BY r.requested_at DESC LIMIT :limit"
        with self.db.session_scope() as session:
            rows = session.execute(text(sql), params).all()
        return [
            {
                "id": int(r[0]),
                "userId": int(r[1]),
                "userName": r[2],
                "feature": r[3],
                "amount": int(r[4]),
                "reason": r[5],
                "status": r[6],
                "requestedAt": r[7],
                "decidedAt": r[8],
                "decidedBy": r[9],
                "grantDay": r[10],
            }
            for r in rows
        ]

    def decide_request(
        self,
        request_id: int,
        approve: bool,
        admin: str = "administrator",
        amount: Optional[int] = None,
    ) -> dict[str, Any]:
        with self.db.session_scope() as session:
            row = session.execute(
                text(
                    "SELECT id, feature, amount, status FROM user_quota_requests "
                    "WHERE id=:rid"
                ),
                {"rid": int(request_id)},
            ).first()
            if not row:
                raise QuotaRequestError("申请不存在")
            if row[3] != "pending":
                raise QuotaRequestError("该申请已处理")
            feature = row[1]
            final_amount = int(amount) if amount is not None else int(row[2])
            final_amount = max(1, min(final_amount, _MAX_REQUEST.get(feature, 200)))
            now = datetime.now(timezone.utc).isoformat()
            session.execute(
                text(
                    "UPDATE user_quota_requests SET status=:status, amount=:amount, "
                    "decided_at=:now, decided_by=:admin, grant_day=:day WHERE id=:rid"
                ),
                {
                    "status": "approved" if approve else "rejected",
                    "amount": final_amount,
                    "now": now,
                    "admin": admin,
                    "day": date.today().isoformat() if approve else None,
                    "rid": int(request_id),
                },
            )
        return {
            "id": int(request_id),
            "status": "approved" if approve else "rejected",
            "amount": final_amount,
        }

    def snapshot(self, user_id: Optional[int]) -> dict[str, Any]:
        if not user_id or int(user_id) <= 0:
            return {
                "unlimited": True,
                "download": {"used": 0, "limit": None, "remaining": None, "bonus": 0},
                "chat": {"used": 0, "limit": None, "remaining": None, "bonus": 0},
                "pendingRequest": None,
            }
        uid = int(user_id)
        download_used = self.used(uid, FEATURE_DOWNLOAD)
        chat_used = self.used(uid, FEATURE_CHAT)
        download_bonus = self.bonus(uid, FEATURE_DOWNLOAD)
        chat_bonus = self.bonus(uid, FEATURE_CHAT)
        download_limit = self.limit(FEATURE_DOWNLOAD) + download_bonus
        chat_limit = self.limit(FEATURE_CHAT) + chat_bonus
        return {
            "unlimited": False,
            "download": {
                "used": download_used,
                "limit": download_limit,
                "remaining": max(0, download_limit - download_used),
                "bonus": download_bonus,
            },
            "chat": {
                "used": chat_used,
                "limit": chat_limit,
                "remaining": max(0, chat_limit - chat_used),
                "bonus": chat_bonus,
            },
            "pendingRequest": self.pending_request(uid),
        }
