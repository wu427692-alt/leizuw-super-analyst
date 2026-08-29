# -*- coding: utf-8 -*-
"""Persistence and queue primitives for DeepSeek research-note analysis."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import threading
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from sqlalchemy import and_, asc, case, desc, func, or_, select

from src.storage import DatabaseManager, EssayAnalysisRecord, ResearchNote, utc_naive_now
from src.research_note_assets import (
    AUDIO_EXTENSIONS,
    asset_summary as summarize_assets,
    enrich_file_assets,
    is_audio_only_note,
)
from src.utils.essay_analysis_quality import LOW_QUALITY_SUMMARY_MARKERS
from src.utils.essay_topic_taxonomy import topic_search_terms


# Keep every ``IN`` clause safely below SQLite's host-parameter ceiling.  The
# production knowledge base can contain tens of thousands of topics, while the
# ceiling varies with the SQLite build shipped on the host.
_SQL_IN_BATCH_SIZE = 500
_FEED_COUNT_CACHE_TTL_SECONDS = 30.0
_FEED_COUNT_CACHE_MAX_ENTRIES = 256
_feed_count_cache: Dict[Tuple[Any, ...], Tuple[float, int]] = {}
_feed_count_cache_lock = threading.Lock()


def _batched(values: Sequence[str], size: int = _SQL_IN_BATCH_SIZE):
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _select_in_batches(session, entity, column, values: Sequence[str]) -> List[Any]:
    rows: List[Any] = []
    for batch in _batched(values):
        rows.extend(session.execute(select(entity).where(column.in_(batch))).scalars().all())
    return rows


def _loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _cached_feed_count(key: Tuple[Any, ...], loader) -> int:
    """Reuse the expensive full-library count while a user pages one query."""
    now = time.monotonic()
    with _feed_count_cache_lock:
        cached = _feed_count_cache.get(key)
        if cached and now - cached[0] < _FEED_COUNT_CACHE_TTL_SECONDS:
            return cached[1]

    total = int(loader() or 0)
    with _feed_count_cache_lock:
        if len(_feed_count_cache) >= _FEED_COUNT_CACHE_MAX_ENTRIES:
            expired = [
                cache_key for cache_key, (stored_at, _) in _feed_count_cache.items()
                if now - stored_at >= _FEED_COUNT_CACHE_TTL_SECONDS
            ]
            for cache_key in expired:
                _feed_count_cache.pop(cache_key, None)
            if len(_feed_count_cache) >= _FEED_COUNT_CACHE_MAX_ENTRIES:
                oldest = min(_feed_count_cache, key=lambda item: _feed_count_cache[item][0])
                _feed_count_cache.pop(oldest, None)
        _feed_count_cache[key] = (now, total)
    return total


class EssayAnalysisRepository:
    """Store analysis output and expose a durable pending/processing/failed queue."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def cache_revision(self) -> Tuple[int, str]:
        """Cheap token for invalidating aggregate caches after ingest/analysis."""
        with self.db.get_session() as session:
            latest_note_id = session.execute(select(func.max(ResearchNote.id))).scalar() or 0
            latest_analysis_update = session.execute(
                select(func.max(EssayAnalysisRecord.updated_at))
            ).scalar()
        return int(latest_note_id), latest_analysis_update.isoformat() if latest_analysis_update else ""

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
            notes = _select_in_batches(session, ResearchNote, ResearchNote.topic_id, normalized)
            return self._enqueue_rows(session, notes, model=model, prompt_version=prompt_version)

    def enqueue_unqueued(
        self,
        *,
        limit: int,
        order: str,
        model: str,
        prompt_version: str,
    ) -> Dict[str, Any]:
        """Queue an exact bounded slice of locally stored notes with no AI task."""
        safe_limit = max(1, min(int(limit), 5000))
        oldest_first = str(order).strip().lower() == "oldest"
        ordering = asc(ResearchNote.created_at) if oldest_first else desc(ResearchNote.created_at)
        with self.db.get_session() as session:
            notes = session.execute(
                select(ResearchNote)
                .outerjoin(EssayAnalysisRecord, EssayAnalysisRecord.topic_id == ResearchNote.topic_id)
                .where(EssayAnalysisRecord.id.is_(None))
                .order_by(ordering, asc(ResearchNote.id) if oldest_first else desc(ResearchNote.id))
                .limit(safe_limit)
            ).scalars().all()
            created_times = [note.created_at for note in notes if note.created_at is not None]
            result = self._enqueue_rows(session, notes, model=model, prompt_version=prompt_version)
        return {
            "requested": safe_limit,
            "selected": len(notes),
            "order": "oldest" if oldest_first else "newest",
            "earliest_selected_at": min(created_times).isoformat() + "Z" if created_times else None,
            "latest_selected_at": max(created_times).isoformat() + "Z" if created_times else None,
            **result,
        }

    def historical_backlog(self) -> Dict[str, Any]:
        """Return all-time factual AI queue coverage without changing queue state."""
        progress = self.progress()
        now = utc_naive_now()
        with self.db.get_session() as session:
            # Counting missing rows through a full LEFT JOIN became the
            # slowest essay-system endpoint once the local archive passed one
            # hundred thousand notes. The FK is one-to-one, so total minus
            # queued is exact and uses the two primary-key indexes.
            total_notes = session.execute(select(func.count(ResearchNote.id))).scalar() or 0
            queued_notes = session.execute(select(func.count(EssayAnalysisRecord.id))).scalar() or 0
            unqueued = max(0, int(total_notes) - int(queued_notes))
            earliest = latest = None
            if unqueued:
                missing_analysis = ~select(EssayAnalysisRecord.id).where(
                    EssayAnalysisRecord.topic_id == ResearchNote.topic_id
                ).exists()
                earliest = session.execute(
                    select(ResearchNote.created_at)
                    .where(missing_analysis)
                    .order_by(asc(ResearchNote.created_at), asc(ResearchNote.id))
                    .limit(1)
                ).scalar_one_or_none()
                latest = session.execute(
                    select(ResearchNote.created_at)
                    .where(missing_analysis)
                    .order_by(desc(ResearchNote.created_at), desc(ResearchNote.id))
                    .limit(1)
                ).scalar_one_or_none()
            (
                earliest_note,
                latest_note,
                latest_sync,
                group_count,
                notes_24h,
                notes_7d,
                notes_30d,
            ) = session.execute(select(
                func.min(ResearchNote.created_at),
                func.max(ResearchNote.created_at),
                func.max(ResearchNote.synced_at),
                func.count(func.distinct(ResearchNote.group_id)),
                func.sum(case((ResearchNote.created_at >= now - timedelta(days=1), 1), else_=0)),
                func.sum(case((ResearchNote.created_at >= now - timedelta(days=7), 1), else_=0)),
                func.sum(case((ResearchNote.created_at >= now - timedelta(days=30), 1), else_=0)),
            )).one()
        return {
            **progress,
            "unqueued": int(unqueued or 0),
            "earliest_unqueued_at": earliest.isoformat() + "Z" if earliest else None,
            "latest_unqueued_at": latest.isoformat() + "Z" if latest else None,
            "earliest_note_at": earliest_note.isoformat() + "Z" if earliest_note else None,
            "latest_note_at": latest_note.isoformat() + "Z" if latest_note else None,
            "latest_synced_at": latest_sync.isoformat() + "Z" if latest_sync else None,
            "group_count": int(group_count or 0),
            "notes_24h": int(notes_24h or 0),
            "notes_7d": int(notes_7d or 0),
            "notes_30d": int(notes_30d or 0),
        }

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
        existing_rows = _select_in_batches(
            session, EssayAnalysisRecord, EssayAnalysisRecord.topic_id, topic_ids
        )
        existing_by_topic = {row.topic_id: row for row in existing_rows}
        created = 0
        reset = 0
        unchanged = 0
        skipped_media = 0
        now = utc_naive_now()
        for note in notes:
            row = existing_by_topic.get(note.topic_id)
            if EssayAnalysisRepository._is_audio_only(note):
                if row is None:
                    row = EssayAnalysisRecord(
                        topic_id=note.topic_id,
                        status="media_only",
                        model=model,
                        prompt_version=prompt_version,
                        input_hash=note.content_hash,
                        error_message="录音附件仅供检索和源文件下载，不进入AI分析",
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                    existing_by_topic[note.topic_id] = row
                else:
                    row.status = "media_only"
                    row.model = model
                    row.prompt_version = prompt_version
                    row.input_hash = note.content_hash
                    row.error_message = "录音附件仅供检索和源文件下载，不进入AI分析"
                    row.started_at = None
                    row.next_retry_at = None
                    row.updated_at = now
                skipped_media += 1
                continue
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
                and row.status != "media_only"
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
        result = {"created": created, "reset": reset, "unchanged": unchanged}
        if skipped_media:
            result["skipped_media"] = skipped_media
        return result

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

    def suppress_audio_only_analyses(
        self,
        *,
        model: str = "media-only",
        prompt_version: str = "media-only-v1",
        limit: int = 50000,
    ) -> int:
        """Register all audio-only topics as terminal media records and suppress legacy AI rows."""
        safe_limit = max(1, min(int(limit), 200000))
        now = utc_naive_now()
        with self.db.get_session() as session:
            notes = session.execute(
                select(ResearchNote)
                .where(
                    ResearchNote.files_json.is_not(None),
                    or_(*[
                        ResearchNote.files_json.like(f"%{extension}%")
                        for extension in sorted(AUDIO_EXTENSIONS)
                    ]),
                )
                .limit(safe_limit)
            ).scalars().all()
            existing_rows = _select_in_batches(
                session,
                EssayAnalysisRecord,
                EssayAnalysisRecord.topic_id,
                [note.topic_id for note in notes],
            )
            existing_by_topic = {row.topic_id: row for row in existing_rows}
            changed = 0
            for note in notes:
                if not self._is_audio_only(note):
                    continue
                analysis = existing_by_topic.get(note.topic_id)
                if analysis is None:
                    session.add(EssayAnalysisRecord(
                        topic_id=note.topic_id,
                        status="media_only",
                        model=model,
                        prompt_version=prompt_version,
                        input_hash=note.content_hash,
                        error_message="录音附件仅供检索和源文件下载，不进入AI分析",
                        created_at=now,
                        updated_at=now,
                    ))
                    changed += 1
                    continue
                if analysis.status == "media_only":
                    continue
                analysis.status = "media_only"
                analysis.error_message = "录音附件仅供检索和源文件下载，不进入AI分析"
                analysis.started_at = None
                analysis.next_retry_at = None
                analysis.updated_at = now
                changed += 1
            session.commit()
            return changed

    def requeue_low_quality_completed(
        self,
        *,
        model: str,
        prompt_version: str,
        limit: int = 5000,
    ) -> int:
        """Return placeholder summaries to the durable queue after a code upgrade.

        This only targets completed rows whose summary is empty or matches a
        known placeholder.  Legitimate low-confidence analysis is untouched.
        """
        safe_limit = max(1, min(int(limit), 50000))
        quality_conditions = [
            EssayAnalysisRecord.summary.is_(None),
            func.length(func.trim(EssayAnalysisRecord.summary)) == 0,
            *(EssayAnalysisRecord.summary.like(f"%{marker}%") for marker in LOW_QUALITY_SUMMARY_MARKERS),
        ]
        now = utc_naive_now()
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord)
                .where(
                    EssayAnalysisRecord.status == "completed",
                    or_(*quality_conditions),
                )
                .order_by(desc(EssayAnalysisRecord.completed_at), desc(EssayAnalysisRecord.id))
                .limit(safe_limit)
            ).scalars().all()
            for row in rows:
                row.status = "pending"
                row.model = model
                row.prompt_version = prompt_version
                row.summary = None
                row.error_message = "正在自动重新分析"
                row.attempt_count = 0
                row.started_at = None
                row.completed_at = None
                row.next_retry_at = None
                row.updated_at = now
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
                .limit(min(safe_limit * 3, 900))
            ).all()
            claimed = []
            for analysis, note in rows:
                if self._is_audio_only(note):
                    analysis.status = "media_only"
                    analysis.error_message = "录音附件仅供检索和源文件下载，不进入AI分析"
                    analysis.next_retry_at = None
                    analysis.started_at = None
                    analysis.updated_at = now
                    continue
                analysis.status = "processing"
                analysis.started_at = now
                analysis.updated_at = now
                analysis.attempt_count = int(analysis.attempt_count or 0) + 1
                claimed.append(self._work_item(analysis, note))
                if len(claimed) >= safe_limit:
                    break
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
            rows = _select_in_batches(
                session,
                EssayAnalysisRecord,
                EssayAnalysisRecord.topic_id,
                list(result_by_topic),
            )
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
            rows = _select_in_batches(
                session, EssayAnalysisRecord, EssayAnalysisRecord.topic_id, normalized
            )
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
        media_only = status_counts.get("media_only", 0)
        ai_eligible_notes = max(0, int(total_notes) - media_only)
        return {
            "total_notes": int(total_notes),
            "queued_notes": sum(status_counts.values()),
            "completed": analyzed,
            "pending": status_counts.get("pending", 0),
            "processing": status_counts.get("processing", 0),
            "failed": status_counts.get("failed", 0),
            "media_only": media_only,
            "ai_eligible_notes": ai_eligible_notes,
            "coverage_percent": round((analyzed / ai_eligible_notes * 100), 2) if ai_eligible_notes else 0.0,
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
        if min_importance is not None and int(min_importance) > 0:
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

    def list_feed(
        self,
        *,
        cutoff: Optional[datetime] = None,
        query: Optional[str] = None,
        query_scope: str = "full",
        analysis_status: Optional[str] = None,
        sentiment: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        stock: Optional[str] = None,
        min_importance: Optional[int] = None,
        known_total: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Read the raw research-note library with optional AI enrichment.

        The feed is note-first: a note does not need an analysis queue record to
        be searchable or visible. AI-only filters naturally narrow the result to
        rows that have matching analysis data.
        """
        where_clause, count_cache_key = self._feed_where_clause(
            cutoff=cutoff,
            query=query,
            query_scope=query_scope,
            analysis_status=analysis_status,
            sentiment=sentiment,
            category=category,
            tag=tag,
            stock=stock,
            min_importance=min_importance,
        )
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 100))
        with self.db.get_session() as session:
            total = max(0, int(known_total)) if known_total is not None else _cached_feed_count(
                count_cache_key,
                lambda: session.execute(
                    select(func.count(ResearchNote.id))
                    .select_from(ResearchNote)
                    .outerjoin(EssayAnalysisRecord, EssayAnalysisRecord.topic_id == ResearchNote.topic_id)
                    .where(where_clause)
                ).scalar(),
            )
            rows = session.execute(
                select(EssayAnalysisRecord, ResearchNote)
                .select_from(ResearchNote)
                .outerjoin(EssayAnalysisRecord, EssayAnalysisRecord.topic_id == ResearchNote.topic_id)
                .where(where_clause)
                .order_by(desc(ResearchNote.created_at), desc(ResearchNote.id))
                .offset((safe_page - 1) * safe_size)
                .limit(safe_size)
            ).all()
        return [self._to_feed_dict(analysis, note) for analysis, note in rows], int(total)

    def iter_feed(
        self,
        *,
        cutoff: Optional[datetime] = None,
        query: Optional[str] = None,
        query_scope: str = "full",
        analysis_status: Optional[str] = None,
        sentiment: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        stock: Optional[str] = None,
        min_importance: Optional[int] = None,
        topic_ids: Optional[Sequence[str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Iterate every matching note without offset pagination for exports."""
        where_clause, _cache_key = self._feed_where_clause(
            cutoff=cutoff,
            query=query,
            query_scope=query_scope,
            analysis_status=analysis_status,
            sentiment=sentiment,
            category=category,
            tag=tag,
            stock=stock,
            min_importance=min_importance,
        )
        normalized_topic_ids = list(dict.fromkeys(str(value).strip() for value in (topic_ids or []) if str(value).strip()))
        if topic_ids is not None:
            where_clause = and_(where_clause, ResearchNote.topic_id.in_(normalized_topic_ids))
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayAnalysisRecord, ResearchNote)
                .select_from(ResearchNote)
                .outerjoin(EssayAnalysisRecord, EssayAnalysisRecord.topic_id == ResearchNote.topic_id)
                .where(where_clause)
                .order_by(desc(ResearchNote.created_at), desc(ResearchNote.id))
                .execution_options(yield_per=250)
            )
            for analysis, note in rows:
                yield self._to_feed_dict(analysis, note)

    def _feed_where_clause(
        self,
        *,
        cutoff: Optional[datetime],
        query: Optional[str],
        query_scope: str = "full",
        analysis_status: Optional[str],
        sentiment: Optional[str],
        category: Optional[str],
        tag: Optional[str],
        stock: Optional[str],
        min_importance: Optional[int],
    ) -> Tuple[Any, Tuple[Any, ...]]:
        conditions = []
        if cutoff is not None:
            conditions.append(ResearchNote.created_at >= cutoff)
        normalized_query_scope = "title" if str(query_scope or "").strip().lower() == "title" else "full"
        for keyword in str(query or "").split():
            equivalent_conditions = []
            for term in topic_search_terms(keyword):
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                equivalent_conditions.append(ResearchNote.title.like(pattern, escape="\\"))
                if normalized_query_scope == "full":
                    equivalent_conditions.extend((
                        ResearchNote.content.like(pattern, escape="\\"),
                        ResearchNote.group_name.like(pattern, escape="\\"),
                        ResearchNote.author_name.like(pattern, escape="\\"),
                        ResearchNote.symbol_codes.like(pattern, escape="\\"),
                        ResearchNote.files_json.like(pattern, escape="\\"),
                        EssayAnalysisRecord.summary.like(pattern, escape="\\"),
                        EssayAnalysisRecord.tags_json.like(pattern, escape="\\"),
                        EssayAnalysisRecord.industries_json.like(pattern, escape="\\"),
                        EssayAnalysisRecord.themes_json.like(pattern, escape="\\"),
                        EssayAnalysisRecord.stock_mentions_json.like(pattern, escape="\\"),
                    ))
            if equivalent_conditions:
                conditions.append(or_(*equivalent_conditions))
        if analysis_status == "essay":
            conditions.append(or_(
                EssayAnalysisRecord.id.is_(None),
                EssayAnalysisRecord.status != "media_only",
            ))
        elif analysis_status == "completed":
            conditions.append(EssayAnalysisRecord.status == "completed")
        elif analysis_status == "uncompleted":
            conditions.append(or_(
                EssayAnalysisRecord.id.is_(None),
                EssayAnalysisRecord.status != "completed",
            ))
        elif analysis_status == "not_queued":
            conditions.append(EssayAnalysisRecord.id.is_(None))
        elif analysis_status in {"pending", "processing", "failed"}:
            conditions.append(EssayAnalysisRecord.status == analysis_status)
        elif analysis_status == "media_only":
            conditions.append(EssayAnalysisRecord.status == "media_only")
        if sentiment:
            conditions.append(EssayAnalysisRecord.sentiment == sentiment)
        if category:
            conditions.append(EssayAnalysisRecord.primary_category == category)
        if tag:
            conditions.append(EssayAnalysisRecord.tags_json.like(f"%{tag.strip()}%"))
        if stock:
            conditions.append(or_(
                ResearchNote.symbol_codes.like(f"%{stock.strip().upper()}%"),
                EssayAnalysisRecord.stock_mentions_json.like(f"%{stock.strip()}%"),
            ))
        if min_importance is not None and int(min_importance) > 0:
            conditions.append(EssayAnalysisRecord.importance_score >= int(min_importance))
        where_clause = and_(*conditions) if conditions else True
        count_cache_key = (
            id(self.db),
            cutoff.replace(second=0, microsecond=0).isoformat() if cutoff else None,
            " ".join(str(query or "").split()),
            normalized_query_scope,
            analysis_status or "",
            sentiment or "",
            category or "",
            tag or "",
            stock or "",
            int(min_importance or 0),
        )
        return where_clause, count_cache_key

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
        # Aggregate views intentionally avoid loading note bodies and media
        # metadata. Those are the largest columns in the knowledge base and are
        # only needed when a user opens an individual source item.
        with self.db.get_session() as session:
            rows = session.execute(
                self._dashboard_select()
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(
                    ResearchNote.created_at >= cutoff,
                    EssayAnalysisRecord.status == "completed",
                )
                .order_by(desc(ResearchNote.created_at), desc(EssayAnalysisRecord.id))
            ).mappings().all()
        return [self._dashboard_dict(row) for row in rows]

    def completed_between(self, *, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                self._dashboard_select()
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(
                    ResearchNote.created_at >= start,
                    ResearchNote.created_at < end,
                    EssayAnalysisRecord.status == "completed",
                )
                .order_by(ResearchNote.created_at, EssayAnalysisRecord.id)
            ).mappings().all()
        return [self._dashboard_dict(row) for row in rows]

    @staticmethod
    def _dashboard_select():
        return select(
            EssayAnalysisRecord.id.label("analysis_id"),
            EssayAnalysisRecord.topic_id,
            EssayAnalysisRecord.status,
            EssayAnalysisRecord.model,
            EssayAnalysisRecord.prompt_version,
            EssayAnalysisRecord.summary,
            EssayAnalysisRecord.primary_category,
            EssayAnalysisRecord.sentiment,
            EssayAnalysisRecord.time_horizon,
            EssayAnalysisRecord.importance_score,
            EssayAnalysisRecord.confidence_score,
            EssayAnalysisRecord.tags_json,
            EssayAnalysisRecord.industries_json,
            EssayAnalysisRecord.themes_json,
            EssayAnalysisRecord.stock_mentions_json,
            EssayAnalysisRecord.key_points_json,
            EssayAnalysisRecord.catalysts_json,
            EssayAnalysisRecord.risks_json,
            EssayAnalysisRecord.raw_response,
            EssayAnalysisRecord.total_tokens,
            EssayAnalysisRecord.updated_at,
            ResearchNote.title.label("note_title"),
            ResearchNote.group_id.label("note_group_id"),
            ResearchNote.group_name.label("note_group_name"),
            ResearchNote.author_name.label("note_author_name"),
            ResearchNote.digested.label("note_digested"),
            ResearchNote.created_at.label("note_created_at"),
        )

    @staticmethod
    def _dashboard_dict(row: Any) -> Dict[str, Any]:
        raw = _loads(row.get("raw_response"), {})
        updated_at = row.get("updated_at")
        note_created_at = row.get("note_created_at")
        return {
            "id": row.get("analysis_id"),
            "topic_id": row.get("topic_id"),
            "status": row.get("status"),
            "model": row.get("model"),
            "prompt_version": row.get("prompt_version"),
            "summary": row.get("summary"),
            "primary_category": row.get("primary_category"),
            "sentiment": row.get("sentiment"),
            "time_horizon": row.get("time_horizon"),
            "importance_score": row.get("importance_score"),
            "confidence_score": row.get("confidence_score"),
            "tags": _loads(row.get("tags_json"), []),
            "industries": _loads(row.get("industries_json"), []),
            "themes": _loads(row.get("themes_json"), []),
            "stock_mentions": _loads(row.get("stock_mentions_json"), []),
            "key_points": _loads(row.get("key_points_json"), []),
            "catalysts": _loads(row.get("catalysts_json"), []),
            "risks": _loads(row.get("risks_json"), []),
            "evidence": raw.get("evidence", []),
            "contradictions": raw.get("contradictions", []),
            "falsification_conditions": raw.get("falsification_conditions", []),
            "monitoring_points": raw.get("monitoring_points", []),
            "earnings_impact": raw.get("earnings_impact", ""),
            "valuation_impact": raw.get("valuation_impact", ""),
            "source_quality": raw.get("source_quality", "unknown"),
            "novelty_score": raw.get("novelty_score", 0),
            "information_type": raw.get("information_type", "unknown"),
            "total_tokens": int(row.get("total_tokens") or 0),
            "updated_at": updated_at.isoformat() + "Z" if updated_at else None,
            "note": {
                "title": row.get("note_title"),
                "content": None,
                "group_id": row.get("note_group_id"),
                "group_name": row.get("note_group_name"),
                "author_name": row.get("note_author_name"),
                "digested": bool(row.get("note_digested")),
                "created_at": note_created_at.isoformat() + "Z" if note_created_at else None,
                "files": [],
                "images": [],
                "asset_summary": summarize_assets([], []),
                "ai_eligible": True,
            },
        }

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
    def _is_audio_only(note: ResearchNote) -> bool:
        return is_audio_only_note(
            title=note.title,
            content=note.content,
            files=_loads(note.files_json, []),
            images=_loads(note.images_json, []),
        )

    @staticmethod
    def _to_dict(analysis: EssayAnalysisRecord, note: ResearchNote) -> Dict[str, Any]:
        if EssayAnalysisRepository._is_audio_only(note):
            return EssayAnalysisRepository._media_only_dict(note)
        files = enrich_file_assets(_loads(note.files_json, []))
        images = _loads(note.images_json, [])
        for asset in files:
            asset.pop("local_path", None)
            asset.pop("local_url", None)
            if asset.get("file_id"):
                asset["view_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/files/{asset['file_id']}"
                asset["download_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/files/{asset['file_id']}/download"
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
                "asset_summary": summarize_assets(files, images),
                "ai_eligible": True,
            },
        }

    @classmethod
    def _to_feed_dict(
        cls,
        analysis: Optional[EssayAnalysisRecord],
        note: ResearchNote,
    ) -> Dict[str, Any]:
        if cls._is_audio_only(note):
            return cls._media_only_dict(note)
        if analysis is not None:
            return cls._to_dict(analysis, note)

        files = enrich_file_assets(_loads(note.files_json, []))
        images = _loads(note.images_json, [])
        for asset in files:
            asset.pop("local_path", None)
            asset.pop("local_url", None)
            if asset.get("file_id"):
                asset["view_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/files/{asset['file_id']}"
                asset["download_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/files/{asset['file_id']}/download"
                asset["download_status"] = "remote_on_demand"
        for asset in images:
            asset.pop("local_path", None)
            asset.pop("local_url", None)
            if asset.get("image_id"):
                asset["view_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/images/{asset['image_id']}"
                asset["download_status"] = "remote_on_demand"
        return {
            "id": None,
            "topic_id": note.topic_id,
            "status": "not_queued",
            "model": None,
            "prompt_version": None,
            "summary": None,
            "primary_category": None,
            "sentiment": None,
            "time_horizon": None,
            "importance_score": None,
            "confidence_score": None,
            "tags": [],
            "industries": [],
            "themes": [],
            "stock_mentions": [],
            "key_points": [],
            "catalysts": [],
            "risks": [],
            "evidence": [],
            "contradictions": [],
            "falsification_conditions": [],
            "monitoring_points": [],
            "earnings_impact": "",
            "valuation_impact": "",
            "source_quality": "unknown",
            "novelty_score": 0,
            "information_type": "unanalyzed",
            "error_message": None,
            "completed_at": None,
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
                "asset_summary": summarize_assets(files, images),
                "ai_eligible": True,
            },
        }

    @staticmethod
    def _media_only_dict(note: ResearchNote) -> Dict[str, Any]:
        files = enrich_file_assets(_loads(note.files_json, []))
        images = _loads(note.images_json, [])
        for asset in files:
            asset.pop("local_path", None)
            asset.pop("local_url", None)
            if asset.get("file_id"):
                asset["view_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/files/{asset['file_id']}"
                asset["download_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/files/{asset['file_id']}/download"
                asset["download_status"] = "remote_on_demand"
        for asset in images:
            asset.pop("local_path", None)
            asset.pop("local_url", None)
            if asset.get("image_id"):
                asset["view_url"] = f"/api/v1/financial-data/research-notes/{note.topic_id}/media/images/{asset['image_id']}"
                asset["download_status"] = "remote_on_demand"
        return {
            "id": None,
            "topic_id": note.topic_id,
            "status": "media_only",
            "model": None,
            "prompt_version": None,
            "summary": None,
            "primary_category": "media",
            "sentiment": None,
            "time_horizon": None,
            "importance_score": None,
            "confidence_score": None,
            "tags": [], "industries": [], "themes": [], "stock_mentions": [],
            "key_points": [], "catalysts": [], "risks": [], "evidence": [],
            "contradictions": [], "falsification_conditions": [], "monitoring_points": [],
            "earnings_impact": "", "valuation_impact": "", "source_quality": "source_file",
            "novelty_score": 0, "information_type": "media_only",
            "error_message": "录音附件仅供检索和源文件下载，不进入AI分析",
            "attempt_count": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "started_at": None, "completed_at": None, "updated_at": None,
            "note": {
                "title": note.title, "content": note.content, "group_id": note.group_id,
                "group_name": note.group_name, "author_name": note.author_name,
                "digested": bool(note.digested),
                "created_at": note.created_at.isoformat() + "Z" if note.created_at else None,
                "files": files, "images": images,
                "asset_summary": summarize_assets(files, images), "ai_eligible": False,
            },
        }
