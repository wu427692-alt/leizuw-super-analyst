# -*- coding: utf-8 -*-
"""Persistence and queue primitives for DeepSeek research-note analysis."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, desc, func, or_, select

from src.storage import DatabaseManager, EssayAnalysisRecord, ResearchNote, utc_naive_now


def _loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class EssayAnalysisRepository:
    """Store analysis output and expose a durable pending/processing/failed queue."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def enqueue_recent(
        self,
        *,
        cutoff: datetime,
        model: str,
        prompt_version: str,
    ) -> Dict[str, int]:
        with self.db.get_session() as session:
            notes = session.execute(
                select(ResearchNote).where(ResearchNote.created_at >= cutoff)
            ).scalars().all()
            return self._enqueue_rows(session, notes, model=model, prompt_version=prompt_version)

    def enqueue_topic_ids(
        self,
        topic_ids: Iterable[str],
        *,
        model: str,
        prompt_version: str,
    ) -> Dict[str, int]:
        normalized = sorted({str(value).strip() for value in topic_ids if str(value).strip()})
        if not normalized:
            return {"created": 0, "reset": 0, "unchanged": 0}
        with self.db.get_session() as session:
            notes = session.execute(
                select(ResearchNote).where(ResearchNote.topic_id.in_(normalized))
            ).scalars().all()
            return self._enqueue_rows(session, notes, model=model, prompt_version=prompt_version)

    @staticmethod
    def _enqueue_rows(
        session,
        notes: Sequence[ResearchNote],
        *,
        model: str,
        prompt_version: str,
    ) -> Dict[str, int]:
        if not notes:
            return {"created": 0, "reset": 0, "unchanged": 0}
        topic_ids = [row.topic_id for row in notes]
        existing_rows = session.execute(
            select(EssayAnalysisRecord).where(EssayAnalysisRecord.topic_id.in_(topic_ids))
        ).scalars().all()
        existing_by_topic = {row.topic_id: row for row in existing_rows}
        created = 0
        reset = 0
        unchanged = 0
        now = utc_naive_now()
        for note in notes:
            row = existing_by_topic.get(note.topic_id)
            if row is None:
                session.add(EssayAnalysisRecord(
                    topic_id=note.topic_id,
                    status="pending",
                    model=model,
                    prompt_version=prompt_version,
                    input_hash=note.content_hash,
                    created_at=now,
                    updated_at=now,
                ))
                created += 1
                continue
            if (
                row.input_hash == note.content_hash
                and row.prompt_version == prompt_version
                and row.model == model
            ):
                unchanged += 1
                continue
            row.status = "pending"
            row.model = model
            row.prompt_version = prompt_version
            row.input_hash = note.content_hash
            row.error_message = None
            row.attempt_count = 0
            row.started_at = None
            row.completed_at = None
            row.next_retry_at = None
            row.updated_at = now
            reset += 1
        session.commit()
        return {"created": created, "reset": reset, "unchanged": unchanged}

    def recover_stale(self, *, stale_after_seconds: int = 600) -> int:
        cutoff = utc_naive_now() - timedelta(seconds=max(60, stale_after_seconds))
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord).where(
                    EssayAnalysisRecord.status == "processing",
                    EssayAnalysisRecord.started_at < cutoff,
                )
            ).scalars().all()
            for row in rows:
                row.status = "pending"
                row.started_at = None
                row.updated_at = utc_naive_now()
            session.commit()
            return len(rows)

    def claim_batch(self, *, limit: int, max_attempts: int) -> List[Dict[str, Any]]:
        now = utc_naive_now()
        safe_limit = max(1, min(int(limit), 300))
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord, ResearchNote)
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(
                    EssayAnalysisRecord.attempt_count < max_attempts,
                    or_(
                        EssayAnalysisRecord.status == "pending",
                        and_(
                            EssayAnalysisRecord.status == "failed",
                            or_(
                                EssayAnalysisRecord.next_retry_at.is_(None),
                                EssayAnalysisRecord.next_retry_at <= now,
                            ),
                        ),
                    ),
                )
                .order_by(desc(ResearchNote.created_at), EssayAnalysisRecord.id)
                .limit(safe_limit)
            ).all()
            claimed = []
            for analysis, note in rows:
                analysis.status = "processing"
                analysis.started_at = now
                analysis.updated_at = now
                analysis.attempt_count = int(analysis.attempt_count or 0) + 1
                claimed.append(self._work_item(analysis, note))
            session.commit()
            return claimed

    def save_successes(
        self,
        results: Sequence[Dict[str, Any]],
        *,
        raw_response: str,
        usage: Dict[str, int],
    ) -> int:
        if not results:
            return 0
        now = utc_naive_now()
        per_item_usage = {
            key: int((usage.get(key) or 0) / len(results))
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        result_by_topic = {str(item["topic_id"]): item for item in results}
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord).where(
                    EssayAnalysisRecord.topic_id.in_(list(result_by_topic))
                )
            ).scalars().all()
            saved = 0
            for row in rows:
                result = result_by_topic[row.topic_id]
                row.status = "completed"
                row.summary = result["summary"]
                row.primary_category = result["primary_category"]
                row.sentiment = result["sentiment"]
                row.time_horizon = result["time_horizon"]
                row.importance_score = result["importance_score"]
                row.confidence_score = result["confidence_score"]
                row.tags_json = json.dumps(result["tags"], ensure_ascii=False)
                row.industries_json = json.dumps(result["industries"], ensure_ascii=False)
                row.themes_json = json.dumps(result["themes"], ensure_ascii=False)
                row.stock_mentions_json = json.dumps(result["stock_mentions"], ensure_ascii=False)
                row.key_points_json = json.dumps(result["key_points"], ensure_ascii=False)
                row.catalysts_json = json.dumps(result["catalysts"], ensure_ascii=False)
                row.risks_json = json.dumps(result["risks"], ensure_ascii=False)
                # Store only this item's normalized JSON. Persisting the entire
                # batch response on every row multiplies the database size by
                # the batch length without adding audit value.
                row.raw_response = json.dumps(result, ensure_ascii=False)
                row.error_message = None
                row.completed_at = now
                row.next_retry_at = None
                row.updated_at = now
                row.prompt_tokens = per_item_usage["prompt_tokens"]
                row.completion_tokens = per_item_usage["completion_tokens"]
                row.total_tokens = per_item_usage["total_tokens"]
                saved += 1
            session.commit()
            return saved

    def save_failures(
        self,
        topic_ids: Iterable[str],
        *,
        error_message: str,
        retry_delay_seconds: int,
    ) -> int:
        normalized = sorted({str(value).strip() for value in topic_ids if str(value).strip()})
        if not normalized:
            return 0
        now = utc_naive_now()
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord).where(EssayAnalysisRecord.topic_id.in_(normalized))
            ).scalars().all()
            for row in rows:
                row.status = "failed"
                row.error_message = str(error_message or "analysis failed")[:1000]
                row.next_retry_at = now + timedelta(seconds=max(1, retry_delay_seconds))
                row.updated_at = now
            session.commit()
            return len(rows)

    def retry_failed(self) -> int:
        now = utc_naive_now()
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord).where(EssayAnalysisRecord.status == "failed")
            ).scalars().all()
            for row in rows:
                row.status = "pending"
                row.attempt_count = 0
                row.error_message = None
                row.next_retry_at = None
                row.started_at = None
                row.updated_at = now
            session.commit()
            return len(rows)

    def progress(self, *, cutoff: Optional[datetime] = None) -> Dict[str, Any]:
        conditions = []
        if cutoff is not None:
            conditions.append(ResearchNote.created_at >= cutoff)
        where_clause = and_(*conditions) if conditions else True
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord.status, func.count(EssayAnalysisRecord.id))
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(where_clause)
                .group_by(EssayAnalysisRecord.status)
            ).all()
            status_counts = {str(status): int(count or 0) for status, count in rows}
            total_notes = session.execute(
                select(func.count(ResearchNote.id)).where(where_clause)
            ).scalar() or 0
        analyzed = status_counts.get("completed", 0)
        return {
            "total_notes": int(total_notes),
            "queued_notes": sum(status_counts.values()),
            "completed": analyzed,
            "pending": status_counts.get("pending", 0),
            "processing": status_counts.get("processing", 0),
            "failed": status_counts.get("failed", 0),
            "coverage_percent": round((analyzed / total_notes * 100), 2) if total_notes else 0.0,
        }

    def list_analyses(
        self,
        *,
        cutoff: Optional[datetime] = None,
        query: Optional[str] = None,
        status: Optional[str] = None,
        sentiment: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        stock: Optional[str] = None,
        min_importance: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = []
        if cutoff is not None:
            conditions.append(ResearchNote.created_at >= cutoff)
        if query:
            pattern = f"%{query.strip()}%"
            conditions.append(or_(ResearchNote.title.like(pattern), EssayAnalysisRecord.summary.like(pattern)))
        if status:
            conditions.append(EssayAnalysisRecord.status == status)
        if sentiment:
            conditions.append(EssayAnalysisRecord.sentiment == sentiment)
        if category:
            conditions.append(EssayAnalysisRecord.primary_category == category)
        if tag:
            conditions.append(EssayAnalysisRecord.tags_json.like(f"%{tag.strip()}%"))
        if stock:
            conditions.append(EssayAnalysisRecord.stock_mentions_json.like(f"%{stock.strip()}%"))
        if min_importance is not None:
            conditions.append(EssayAnalysisRecord.importance_score >= int(min_importance))
        where_clause = and_(*conditions) if conditions else True
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 100))
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(EssayAnalysisRecord.id))
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(EssayAnalysisRecord, ResearchNote)
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(where_clause)
                .order_by(desc(ResearchNote.created_at), desc(EssayAnalysisRecord.id))
                .offset((safe_page - 1) * safe_size)
                .limit(safe_size)
            ).all()
        return [self._to_dict(analysis, note) for analysis, note in rows], int(total)

    def get_analysis(self, topic_id: str) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayAnalysisRecord, ResearchNote)
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(EssayAnalysisRecord.topic_id == topic_id)
                .limit(1)
            ).one_or_none()
        return self._to_dict(*row) if row else None

    def completed_for_dashboard(self, *, cutoff: datetime) -> List[Dict[str, Any]]:
        # Dashboard aggregation needs the complete window. One joined scan is
        # substantially faster than issuing a count plus one query per 100 rows.
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord, ResearchNote)
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(
                    ResearchNote.created_at >= cutoff,
                    EssayAnalysisRecord.status == "completed",
                )
                .order_by(desc(ResearchNote.created_at), desc(EssayAnalysisRecord.id))
            ).all()
        return [self._to_dict(analysis, note) for analysis, note in rows]

    def completed_between(self, *, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord, ResearchNote)
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(
                    ResearchNote.created_at >= start,
                    ResearchNote.created_at < end,
                    EssayAnalysisRecord.status == "completed",
                )
                .order_by(ResearchNote.created_at, EssayAnalysisRecord.id)
            ).all()
        return [self._to_dict(analysis, note) for analysis, note in rows]

    @staticmethod
    def _work_item(analysis: EssayAnalysisRecord, note: ResearchNote) -> Dict[str, Any]:
        return {
            "analysis_id": analysis.id,
            "topic_id": note.topic_id,
            "title": note.title,
            "content": note.content or "",
            "existing_symbols": [value for value in (note.symbol_codes or "").split(",") if value],
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "attempt_count": int(analysis.attempt_count or 0),
        }

    @staticmethod
    def _to_dict(analysis: EssayAnalysisRecord, note: ResearchNote) -> Dict[str, Any]:
        files = _loads(note.files_json, [])
        images = _loads(note.images_json, [])
        for asset in files:
            asset.pop("local_path", None)
            asset.pop("local_url", None)
            if asset.get("file_id"):
                asset["view_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/files/{asset['file_id']}"
                asset["download_status"] = "remote_on_demand"
        for asset in images:
            asset.pop("local_path", None)
            asset.pop("local_url", None)
            if asset.get("image_id"):
                asset["view_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/images/{asset['image_id']}"
                asset["download_status"] = "remote_on_demand"
        raw = _loads(analysis.raw_response, {})
        return {
            "id": analysis.id,
            "topic_id": analysis.topic_id,
            "status": analysis.status,
            "model": analysis.model,
            "prompt_version": analysis.prompt_version,
            "summary": analysis.summary,
            "primary_category": analysis.primary_category,
            "sentiment": analysis.sentiment,
            "time_horizon": analysis.time_horizon,
            "importance_score": analysis.importance_score,
            "confidence_score": analysis.confidence_score,
            "tags": _loads(analysis.tags_json, []),
            "industries": _loads(analysis.industries_json, []),
            "themes": _loads(analysis.themes_json, []),
            "stock_mentions": _loads(analysis.stock_mentions_json, []),
            "key_points": _loads(analysis.key_points_json, []),
            "catalysts": _loads(analysis.catalysts_json, []),
            "risks": _loads(analysis.risks_json, []),
            "evidence": raw.get("evidence", []),
            "contradictions": raw.get("contradictions", []),
            "falsification_conditions": raw.get("falsification_conditions", []),
            "monitoring_points": raw.get("monitoring_points", []),
            "earnings_impact": raw.get("earnings_impact", ""),
            "valuation_impact": raw.get("valuation_impact", ""),
            "source_quality": raw.get("source_quality", "unknown"),
            "novelty_score": raw.get("novelty_score", 0),
            "information_type": raw.get("information_type", "unknown"),
            "error_message": analysis.error_message,
            "attempt_count": int(analysis.attempt_count or 0),
            "prompt_tokens": int(analysis.prompt_tokens or 0),
            "completion_tokens": int(analysis.completion_tokens or 0),
            "total_tokens": int(analysis.total_tokens or 0),
            "started_at": analysis.started_at.isoformat() + "Z" if analysis.started_at else None,
            "completed_at": analysis.completed_at.isoformat() + "Z" if analysis.completed_at else None,
            "updated_at": analysis.updated_at.isoformat() + "Z" if analysis.updated_at else None,
            "note": {
                "title": note.title,
                "content": note.content,
                "group_id": note.group_id,
                "group_name": note.group_name,
                "author_name": note.author_name,
                "digested": bool(note.digested),
                "created_at": note.created_at.isoformat() + "Z" if note.created_at else None,
                "files": files,
                "images": images,
            },
        }
