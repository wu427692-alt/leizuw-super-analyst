# -*- coding: utf-8 -*-
"""Persistence for model-specific daily essay synthesis reports."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import desc, select

from src.storage import DatabaseManager, EssayDailyReportRecord, utc_naive_now


class EssayDailyReportRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def get(self, report_date: str, model: str) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayDailyReportRecord).where(
                    EssayDailyReportRecord.report_date == report_date,
                    EssayDailyReportRecord.model == model,
                )
            ).scalar_one_or_none()
        return self._to_dict(row) if row else None

    def begin(self, *, report_date: str, model: str, prompt_version: str, source_count: int, source_hash: str) -> None:
        now = utc_naive_now()
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayDailyReportRecord).where(
                    EssayDailyReportRecord.report_date == report_date,
                    EssayDailyReportRecord.model == model,
                )
            ).scalar_one_or_none()
            if row is None:
                row = EssayDailyReportRecord(report_date=report_date, model=model)
                session.add(row)
            row.status = "processing"
            row.prompt_version = prompt_version
            row.source_count = source_count
            row.source_hash = source_hash
            row.error_message = None
            row.started_at = now
            row.updated_at = now
            session.commit()

    def save_success(self, *, report_date: str, model: str, report: Dict[str, Any], usage: Dict[str, Any]) -> None:
        now = utc_naive_now()
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayDailyReportRecord).where(
                    EssayDailyReportRecord.report_date == report_date,
                    EssayDailyReportRecord.model == model,
                )
            ).scalar_one()
            row.status = "completed"
            row.report_json = json.dumps(report, ensure_ascii=False)
            row.error_message = None
            row.prompt_tokens = int(usage.get("prompt_tokens") or 0)
            row.completion_tokens = int(usage.get("completion_tokens") or 0)
            row.total_tokens = int(usage.get("total_tokens") or 0)
            row.completed_at = now
            row.updated_at = now
            session.commit()

    def save_failure(self, *, report_date: str, model: str, error: str) -> None:
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayDailyReportRecord).where(
                    EssayDailyReportRecord.report_date == report_date,
                    EssayDailyReportRecord.model == model,
                )
            ).scalar_one_or_none()
            if row:
                row.status = "failed"
                row.error_message = str(error)[:1000]
                row.updated_at = utc_naive_now()
                session.commit()

    def list(self, *, limit: int = 30, model: Optional[str] = None) -> List[Dict[str, Any]]:
        query = select(EssayDailyReportRecord)
        if model:
            query = query.where(EssayDailyReportRecord.model == model)
        query = query.order_by(desc(EssayDailyReportRecord.report_date), EssayDailyReportRecord.model).limit(max(1, min(limit, 366)))
        with self.db.get_session() as session:
            rows = session.execute(query).scalars().all()
        return [self._to_dict(row) for row in rows]

    @staticmethod
    def _to_dict(row: EssayDailyReportRecord) -> Dict[str, Any]:
        try:
            report = json.loads(row.report_json) if row.report_json else None
        except (TypeError, ValueError):
            report = None
        return {
            "id": row.id,
            "report_date": row.report_date,
            "model": row.model,
            "status": row.status,
            "prompt_version": row.prompt_version,
            "source_count": int(row.source_count or 0),
            "source_hash": row.source_hash,
            "report": report,
            "error_message": row.error_message,
            "prompt_tokens": int(row.prompt_tokens or 0),
            "completion_tokens": int(row.completion_tokens or 0),
            "total_tokens": int(row.total_tokens or 0),
            "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
            "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
            "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        }
