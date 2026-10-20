"""Owner-scoped durable jobs and paid decision cache in the existing database."""
import json
import time
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, Float, String, Text, UniqueConstraint
from sqlalchemy.exc import IntegrityError

from src.storage import Base, DatabaseManager


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class JevReplayTask(Base):
    __tablename__ = "jev_replay_tasks"
    task_id = Column(String(36), primary_key=True)
    owner_id = Column(String(80), nullable=False, index=True)
    request_id = Column(String(36), nullable=False)
    active_owner = Column(String(80), unique=True, nullable=True)
    status = Column(String(20), nullable=False)
    updated_at = Column(Float, nullable=False)
    body = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)


class JevReplayDecision(Base):
    __tablename__ = "jev_replay_decisions"
    owner_id = Column(String(80), primary_key=True)
    input_hash = Column(String(64), primary_key=True)
    body = Column(Text, nullable=False)


class JevReplayRepository:
    def __init__(self, db=None):
        self.db = db or DatabaseManager.get_instance()
        with self.db.get_session() as session:
            for table in (JevReplayTask.__table__, JevReplayDecision.__table__):
                table.create(session.get_bind(), checkfirst=True)

    def get(self, owner, task_id):
        with self.db.get_session() as s:
            row = s.query(JevReplayTask).filter_by(owner_id=owner, task_id=task_id).first()
            return json.loads(row.body) if row else None

    def list(self, owner, limit=20):
        with self.db.get_session() as s:
            rows = s.query(JevReplayTask).filter_by(owner_id=owner).order_by(
                JevReplayTask.updated_at.desc()).limit(limit).all()
            return [{k: v for k, v in json.loads(r.body).items()
                     if k not in ("result", "preview")} for r in rows]

    def create(self, owner, config):
        request_id = str(config["request_id"])
        with self.db.get_session() as s:
            old = s.query(JevReplayTask).filter_by(owner_id=owner, request_id=request_id).first()
            if old:
                body = json.loads(old.body)
                if body["config"] != config:
                    raise ValueError("同一请求编号不能用于不同参数")
                return body, False
            # A paid request interrupted for >30 minutes is never automatically retried.
            stale = s.query(JevReplayTask).filter_by(active_owner=owner).filter(
                JevReplayTask.updated_at < time.time() - 1800).first()
            if stale:
                body = json.loads(stale.body)
                body.update(status="failed", error="任务进程已中断，请新建回测；已缓存信号可复用",
                            completed_at=timestamp())
                stale.status, stale.active_owner, stale.body = "failed", None, encode(body)
                s.flush()
            task_id = str(uuid4())
            body = dict(task_id=task_id, status="queued", progress=0, message="等待分钟行情",
                        config=config, created_at=timestamp(), started_at=None, completed_at=None,
                        usage={"requests": 0, "cached": 0, "cost_usd": 0}, error=None)
            s.add(JevReplayTask(task_id=task_id, owner_id=owner, request_id=request_id,
                               active_owner=owner, status="queued", updated_at=time.time(),
                               body=encode(body)))
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                old = s.query(JevReplayTask).filter_by(owner_id=owner, request_id=request_id).first()
                if old and json.loads(old.body)["config"] == config:
                    return json.loads(old.body), False
                raise ValueError("已有回测正在运行，请等待完成或取消") from None
            return body, True

    def update(self, owner, task_id, patch, *, expected_status=None, require_not_cancelled=False):
        with self.db.get_session() as s:
            query = s.query(JevReplayTask).filter_by(owner_id=owner, task_id=task_id)
            row = query.first()
            if not row or (expected_status and row.status != expected_status):
                return None
            body = json.loads(row.body)
            if require_not_cancelled and body.get("cancel_requested"):
                return None
            body.update(patch)
            status = body["status"]
            values = dict(body=encode(body), status=status, updated_at=time.time())
            if status in ("completed", "failed", "cancelled"):
                values["active_owner"] = None
            # Prevent concurrent cancel/claim from overwriting one another.
            changed = query.filter(JevReplayTask.updated_at == row.updated_at).update(values)
            s.commit()
            if not changed:
                return self.update(owner, task_id, patch, expected_status=expected_status,
                                   require_not_cancelled=require_not_cancelled)
            return body

    def cached(self, owner, digest):
        with self.db.get_session() as s:
            row = s.get(JevReplayDecision, (owner, digest))
            return json.loads(row.body) if row else None

    def cache(self, owner, digest, decision, *, replace=False):
        with self.db.get_session() as s:
            row = s.get(JevReplayDecision, (owner, digest))
            if row and replace:
                row.body = encode(decision)
                s.commit()
            elif not row:
                s.add(JevReplayDecision(owner_id=owner, input_hash=digest, body=encode(decision)))
                try:
                    s.commit()
                except IntegrityError:
                    s.rollback()
