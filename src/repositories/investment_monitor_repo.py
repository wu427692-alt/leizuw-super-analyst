# -*- coding: utf-8 -*-
"""Persistence for pluggable investment-monitor sources and normalized events."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, case, desc, func, not_, or_, select, tuple_
from sqlalchemy.orm import load_only

from src.storage import (
    DatabaseManager,
    MonitoringEventRecord,
    MonitoringSourceRecord,
    ResearchNote,
    WatchlistBackfillRecord,
    WatchlistAnnouncementSyncRecord,
    utc_naive_now,
)


# List and dashboard responses never expose ``raw_payload``. Avoid hydrating
# that often very large evidence blob; the detail endpoint loads it separately
# when a user explicitly opens one item.
_EVENT_LIST_COLUMNS = (
    MonitoringEventRecord.id, MonitoringEventRecord.source_key,
    MonitoringEventRecord.source_name, MonitoringEventRecord.source_type,
    MonitoringEventRecord.external_id, MonitoringEventRecord.event_type,
    MonitoringEventRecord.perspective, MonitoringEventRecord.title,
    MonitoringEventRecord.summary, MonitoringEventRecord.url,
    MonitoringEventRecord.symbol_codes, MonitoringEventRecord.sentiment,
    MonitoringEventRecord.importance_score, MonitoringEventRecord.confidence_score,
    MonitoringEventRecord.tags_json, MonitoringEventRecord.actors_json,
    MonitoringEventRecord.metrics_json, MonitoringEventRecord.event_at,
    MonitoringEventRecord.ingested_at,
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class InvestmentMonitorRepository:
    """Small, source-agnostic database contract used by every adapter."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def cache_revision(self) -> Tuple[int, str]:
        """Cheap token that changes whenever the normalized event store changes."""
        with self.db.get_session() as session:
            latest_id, latest_ingested = session.execute(select(
                func.max(MonitoringEventRecord.id),
                func.max(MonitoringEventRecord.ingested_at),
            )).one()
        return int(latest_id or 0), latest_ingested.isoformat() if latest_ingested else ""

    def create_backfill_job(self, symbol: str, *, stock_name: str = "", days: int = 183) -> Dict[str, Any]:
        with self.db.get_session() as session:
            active = session.execute(
                select(WatchlistBackfillRecord).where(
                    WatchlistBackfillRecord.symbol == symbol,
                    WatchlistBackfillRecord.status.in_(("pending", "running")),
                ).order_by(desc(WatchlistBackfillRecord.requested_at))
            ).scalars().first()
            if active is None:
                active = WatchlistBackfillRecord(symbol=symbol, stock_name=stock_name, days=days)
                session.add(active)
                session.commit()
                session.refresh(active)
            return self._backfill_dict(active)

    def ensure_announcement_sync_state(
        self, symbol: str, *, stock_name: str = "", target_start: Any, target_end: Any,
    ) -> Dict[str, Any]:
        """Create or widen the durable one-year CNInfo coverage target."""
        with self.db.get_session() as session:
            row = session.execute(
                select(WatchlistAnnouncementSyncRecord)
                .where(WatchlistAnnouncementSyncRecord.symbol == symbol)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = WatchlistAnnouncementSyncRecord(
                    symbol=symbol, stock_name=stock_name,
                    target_start=target_start, target_end=target_end,
                )
                session.add(row)
            else:
                if target_start < row.target_start:
                    row.target_start = target_start
                    row.status = "pending"
                    row.backfill_completed_at = None
                if target_end > row.target_end:
                    row.target_end = target_end
                    row.status = "pending"
                    row.backfill_completed_at = None
                if stock_name:
                    row.stock_name = stock_name
                row.updated_at = utc_naive_now()
            session.commit()
            session.refresh(row)
            return self._announcement_sync_dict(row)

    def list_announcement_sync_states(
        self, symbols: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            query = select(WatchlistAnnouncementSyncRecord)
            values = list(symbols or [])
            if values:
                query = query.where(WatchlistAnnouncementSyncRecord.symbol.in_(values))
            rows = session.execute(query.order_by(WatchlistAnnouncementSyncRecord.symbol)).scalars().all()
            return [self._announcement_sync_dict(row) for row in rows]

    def update_announcement_sync_state(self, symbol: str, **fields: Any) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.execute(
                select(WatchlistAnnouncementSyncRecord)
                .where(WatchlistAnnouncementSyncRecord.symbol == symbol)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                raise ValueError("announcement sync state not found")
            if "completed_windows" in fields:
                row.completed_windows_json = _dump(fields.pop("completed_windows"))
            for key, value in fields.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = utc_naive_now()
            session.commit()
            session.refresh(row)
            return self._announcement_sync_dict(row)

    @staticmethod
    def _announcement_sync_dict(row: WatchlistAnnouncementSyncRecord) -> Dict[str, Any]:
        def iso(value: Any) -> Optional[str]:
            return value.isoformat() + "Z" if value else None

        return {
            "id": row.id, "symbol": row.symbol, "stock_name": row.stock_name,
            "target_start": row.target_start.isoformat(), "target_end": row.target_end.isoformat(),
            "completed_windows": _load(row.completed_windows_json, []),
            "status": row.status, "consecutive_failures": int(row.consecutive_failures or 0),
            "next_retry_at": iso(row.next_retry_at), "last_error": row.last_error,
            "last_incremental_started_at": iso(row.last_incremental_started_at),
            "last_incremental_success_at": iso(row.last_incremental_success_at),
            "last_backfill_started_at": iso(row.last_backfill_started_at),
            "last_backfill_success_at": iso(row.last_backfill_success_at),
            "backfill_completed_at": iso(row.backfill_completed_at),
            "total_fetched": int(row.total_fetched or 0),
            "total_created": int(row.total_created or 0),
            "total_updated": int(row.total_updated or 0),
            "updated_at": iso(row.updated_at),
        }

    def update_backfill_job(self, job_id: int, **fields: Any) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.get(WatchlistBackfillRecord, job_id)
            if row is None:
                raise ValueError("backfill job not found")
            if "channel_status" in fields:
                row.channel_status_json = _dump(fields.pop("channel_status"))
            for key, value in fields.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = utc_naive_now()
            session.commit()
            session.refresh(row)
            return self._backfill_dict(row)

    def list_backfill_jobs(self, symbols: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            query = select(WatchlistBackfillRecord)
            values = list(symbols or [])
            if values:
                query = query.where(WatchlistBackfillRecord.symbol.in_(values))
            rows = session.execute(query.order_by(desc(WatchlistBackfillRecord.requested_at))).scalars().all()
            latest: Dict[str, WatchlistBackfillRecord] = {}
            for row in rows:
                latest.setdefault(row.symbol, row)
            return [self._backfill_dict(row) for row in latest.values()]

    def pending_backfill_jobs(self) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(select(WatchlistBackfillRecord).where(
                WatchlistBackfillRecord.status.in_(("pending", "running"))
            ).order_by(WatchlistBackfillRecord.requested_at)).scalars().all()
            return [self._backfill_dict(row) for row in rows]

    @staticmethod
    def _backfill_dict(row: WatchlistBackfillRecord) -> Dict[str, Any]:
        return {
            "id": row.id, "symbol": row.symbol, "stock_name": row.stock_name,
            "days": row.days, "status": row.status, "progress": row.progress,
            "channels": _load(row.channel_status_json, {}), "error": row.error,
            "requested_at": row.requested_at.isoformat() + "Z" if row.requested_at else None,
            "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
            "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
        }

    def ensure_sources(self, definitions: Iterable[Dict[str, Any]]) -> int:
        created = 0
        with self.db.get_session() as session:
            existing = {
                row.source_key: row
                for row in session.execute(select(MonitoringSourceRecord)).scalars().all()
            }
            for fields in definitions:
                key = str(fields["source_key"])
                if key in existing:
                    row = existing[key]
                    row.name = fields["name"]
                    row.adapter_type = fields["adapter_type"]
                    row.provider = fields["provider"]
                    row.category = fields["category"]
                    row.poll_interval_seconds = int(fields["poll_interval_seconds"])
                    row.config_json = _dump(fields.get("config") or {})
                    continue
                session.add(MonitoringSourceRecord(
                    source_key=key,
                    name=fields["name"],
                    adapter_type=fields["adapter_type"],
                    provider=fields["provider"],
                    category=fields["category"],
                    enabled=bool(fields.get("enabled", True)),
                    poll_interval_seconds=int(fields["poll_interval_seconds"]),
                    config_json=_dump(fields.get("config") or {}),
                ))
                created += 1
            session.commit()
        return created

    def backfill_zsxq_topic_urls(self) -> int:
        updated = 0
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringEventRecord).where(
                    MonitoringEventRecord.source_key == "zsxq.essays",
                    or_(MonitoringEventRecord.url.is_(None), MonitoringEventRecord.url == ""),
                )
            ).scalars().all()
            for row in rows:
                payload = _load(row.raw_payload, {})
                group_id = str(payload.get("group_id") or "") if isinstance(payload, dict) else ""
                topic_id = str(payload.get("topic_id") or "") if isinstance(payload, dict) else ""
                if not (group_id.isdigit() and topic_id.isdigit()):
                    continue
                row.url = f"https://wx.zsxq.com/group/{group_id}/topic/{topic_id}"
                row.updated_at = utc_naive_now()
                updated += 1
            if updated:
                session.commit()
        return updated

    def create_source(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = MonitoringSourceRecord(
                source_key=fields["source_key"],
                name=fields["name"],
                adapter_type=fields["adapter_type"],
                provider=fields["provider"],
                category=fields["category"],
                enabled=bool(fields.get("enabled", True)),
                poll_interval_seconds=int(fields.get("poll_interval_seconds") or 300),
                config_json=_dump(fields.get("config") or {}),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._source_dict(row)

    def get_source(self, source_key: str) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.execute(
                select(MonitoringSourceRecord)
                .where(MonitoringSourceRecord.source_key == source_key)
                .limit(1)
            ).scalar_one_or_none()
        return self._source_dict(row) if row else None

    def list_sources(self) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringSourceRecord)
                .order_by(MonitoringSourceRecord.category, MonitoringSourceRecord.name)
            ).scalars().all()
        return [self._source_dict(row) for row in rows]

    def source_event_freshness(self) -> Dict[str, Dict[str, Any]]:
        """Return actual source-data recency, independent of scheduler success."""
        with self.db.get_session() as session:
            source_keys = session.execute(
                select(MonitoringSourceRecord.source_key).order_by(MonitoringSourceRecord.source_key)
            ).scalars().all()
            result: Dict[str, Dict[str, Any]] = {}
            for source_key in source_keys:
                # The previous GROUP BY read event_at and ingested_at from the
                # whole event table. On a 2 GiB host that could hold the API for
                # more than ten seconds. The source/time and unique source/id
                # indexes make these small per-source probes predictable.
                latest = session.execute(
                    select(MonitoringEventRecord.event_at, MonitoringEventRecord.ingested_at)
                    .where(MonitoringEventRecord.source_key == source_key)
                    .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                    .limit(1)
                ).first()
                event_count = session.execute(
                    select(func.count(MonitoringEventRecord.id))
                    .where(MonitoringEventRecord.source_key == source_key)
                ).scalar() or 0
                result[str(source_key)] = {
                    "latest_event_at": self._iso(latest[0]) if latest else None,
                    "latest_ingested_at": self._iso(latest[1]) if latest else None,
                    "stored_event_count": int(event_count),
                }
        return result

    def recent_event_stats(self, *, days: int = 7) -> Dict[str, Any]:
        """Exact narrow-column aggregates for dashboards without hydrating documents."""
        cutoff = utc_naive_now() - timedelta(days=max(1, int(days)))
        conditions = (
            MonitoringEventRecord.event_at >= cutoff,
            MonitoringEventRecord.event_type != "realtime_quote",
        )
        with self.db.get_session() as session:
            summary = session.execute(select(
                func.count(MonitoringEventRecord.id),
                func.count(func.distinct(MonitoringEventRecord.source_key)),
                func.sum(case((MonitoringEventRecord.importance_score >= 75, 1), else_=0)),
                func.sum(case((MonitoringEventRecord.sentiment == "bullish", 1), else_=0)),
                func.sum(case((MonitoringEventRecord.sentiment == "bearish", 1), else_=0)),
                func.sum(case((and_(MonitoringEventRecord.url.is_not(None), MonitoringEventRecord.url != ""), 1), else_=0)),
            ).where(*conditions)).one()

            def grouped(column: Any) -> Dict[str, int]:
                rows = session.execute(
                    select(column, func.count(MonitoringEventRecord.id))
                    .where(*conditions)
                    .group_by(column)
                ).all()
                return {str(key or "unknown"): int(count or 0) for key, count in rows}

            source_sentiments: Dict[str, Dict[str, int]] = {}
            for source_key, sentiment, count in session.execute(
                select(
                    MonitoringEventRecord.source_key,
                    MonitoringEventRecord.sentiment,
                    func.count(MonitoringEventRecord.id),
                ).where(*conditions).group_by(
                    MonitoringEventRecord.source_key,
                    MonitoringEventRecord.sentiment,
                )
            ).all():
                source_sentiments.setdefault(str(source_key), {})[str(sentiment or "neutral")] = int(count or 0)

            source_high_priority = {
                str(source_key): int(count or 0)
                for source_key, count in session.execute(
                    select(
                        MonitoringEventRecord.source_key,
                        func.count(MonitoringEventRecord.id),
                    ).where(
                        *conditions,
                        MonitoringEventRecord.importance_score >= 75,
                    ).group_by(MonitoringEventRecord.source_key)
                ).all()
            }

            daily_sources = [{
                "date": str(event_date),
                "source_key": str(source_key),
                "count": int(count or 0),
                "high_priority": int(high_priority or 0),
            } for event_date, source_key, count, high_priority in session.execute(
                select(
                    func.date(MonitoringEventRecord.event_at),
                    MonitoringEventRecord.source_key,
                    func.count(MonitoringEventRecord.id),
                    func.sum(case((MonitoringEventRecord.importance_score >= 75, 1), else_=0)),
                ).where(*conditions).group_by(
                    func.date(MonitoringEventRecord.event_at),
                    MonitoringEventRecord.source_key,
                )
            ).all()]

            return {
                "event_count": int(summary[0] or 0),
                "active_source_count": int(summary[1] or 0),
                "high_priority_count": int(summary[2] or 0),
                "bullish_count": int(summary[3] or 0),
                "bearish_count": int(summary[4] or 0),
                "original_link_count": int(summary[5] or 0),
                "perspectives": grouped(MonitoringEventRecord.perspective),
                "event_types": grouped(MonitoringEventRecord.event_type),
                "sources": grouped(MonitoringEventRecord.source_key),
                "source_sentiments": source_sentiments,
                "source_high_priority": source_high_priority,
                "daily_sources": daily_sources,
            }

    def symbol_dashboard_stats(self, *, symbol: str, days: int) -> Dict[str, Any]:
        """Aggregate one watchlist card without hydrating every evidence body.

        A popular stock can have thousands of essays, reports and forum posts.
        Loading their summaries and JSON payloads merely to count sentiments
        makes the public dashboard retain hundreds of megabytes per request.
        Keep that complete evidence set available to paginated workspaces while
        using narrow SQL aggregates for the home-card summary.
        """
        cutoff = utc_naive_now() - timedelta(days=max(1, int(days)))
        conditions = (
            MonitoringEventRecord.event_at >= cutoff,
            MonitoringEventRecord.symbol_codes.like(f"%{symbol}%"),
            MonitoringEventRecord.event_type != "realtime_quote",
        )
        today = utc_naive_now().date().isoformat()
        direction = case(
            (MonitoringEventRecord.sentiment == "bullish", 1),
            (MonitoringEventRecord.sentiment == "bearish", -1),
            else_=0,
        )
        with self.db.get_session() as session:
            summary = session.execute(select(
                func.count(MonitoringEventRecord.id),
                func.sum(case((MonitoringEventRecord.importance_score >= 75, 1), else_=0)),
                func.sum((MonitoringEventRecord.importance_score - 40) * direction),
                func.sum(case((MonitoringEventRecord.title.like("%风险%"), 1), else_=0)),
                func.sum(case((MonitoringEventRecord.event_type == "institution_forecast", 1), else_=0)),
                func.max(MonitoringEventRecord.event_at),
                func.sum(case((func.date(MonitoringEventRecord.event_at) == today, 1), else_=0)),
            ).where(*conditions)).one()

            def grouped(column: Any) -> Dict[str, int]:
                rows = session.execute(
                    select(column, func.count(MonitoringEventRecord.id))
                    .where(*conditions)
                    .group_by(column)
                ).all()
                return {str(key or "unknown"): int(count or 0) for key, count in rows}

            latest_forecast = session.execute(
                select(MonitoringEventRecord.metrics_json)
                .where(*conditions, MonitoringEventRecord.event_type == "institution_forecast")
                .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                .limit(1)
            ).scalar_one_or_none()

        metrics = _load(latest_forecast, {}) if latest_forecast else {}
        return {
            "event_count": int(summary[0] or 0),
            "high_priority_count": int(summary[1] or 0),
            "weighted_sentiment": int(summary[2] or 0),
            "risk_title_count": int(summary[3] or 0),
            "institution_rating_count": int(summary[4] or 0),
            "latest_event_at": summary[5].isoformat() if summary[5] else None,
            "today_event_count": int(summary[6] or 0),
            "perspectives": grouped(MonitoringEventRecord.perspective),
            "sentiment": grouped(MonitoringEventRecord.sentiment),
            "latest_rating": metrics.get("rating"),
        }

    def due_sources(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        current = now or utc_naive_now()
        due = []
        for source in self.list_sources():
            if not source["enabled"]:
                continue
            cadence = max(10, int(source["poll_interval_seconds"]))
            last_success = self._parse_iso(source["last_success_at"]) if source["last_success_at"] else None
            if last_success is not None and last_success + timedelta(seconds=cadence) > current:
                continue
            last_started = self._parse_iso(source["last_started_at"]) if source["last_started_at"] else None
            status = str(source.get("last_status") or "")
            if status == "failed" and last_started is not None:
                retry_seconds = max(15, min(cadence * 2, 300))
                if last_started + timedelta(seconds=retry_seconds) > current:
                    continue
            elif status == "not_configured" and last_started is not None:
                if last_started + timedelta(seconds=max(cadence, 3600)) > current:
                    continue
            elif status == "running" and last_started is not None:
                # Do not schedule a parallel copy of a source that still has a
                # plausible in-flight request. The watchdog will wake the owner
                # again after the stale-running window passes.
                if last_started + timedelta(seconds=max(90, cadence * 3)) > current:
                    continue
            due.append(source)
        return due

    def update_source_status(
        self,
        source_key: str,
        *,
        status: str,
        error: Optional[str] = None,
        item_count: Optional[int] = None,
        received_count: Optional[int] = None,
        created_count: Optional[int] = None,
        updated_count: Optional[int] = None,
        duration_ms: Optional[int] = None,
        started_at: Optional[datetime] = None,
        success_at: Optional[datetime] = None,
    ) -> None:
        with self.db.get_session() as session:
            row = session.execute(
                select(MonitoringSourceRecord)
                .where(MonitoringSourceRecord.source_key == source_key)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return
            row.last_status = status
            row.last_error = str(error or "")[:1000] or None
            row.updated_at = utc_naive_now()
            if started_at is not None:
                row.last_started_at = started_at
            if success_at is not None:
                row.last_success_at = success_at
            if item_count is not None:
                row.last_item_count = int(item_count)
                row.total_item_count = int(row.total_item_count or 0) + int(item_count)
            if received_count is not None:
                row.last_received_count = int(received_count)
            if created_count is not None:
                row.last_created_count = int(created_count)
            if updated_count is not None:
                row.last_updated_count = int(updated_count)
            if duration_ms is not None:
                row.last_duration_ms = max(0, int(duration_ms))
            session.commit()

    def source_activity(self, *, days: int = 30) -> List[Dict[str, Any]]:
        """Daily event and ingestion volume per source for the source BI page."""
        cutoff = utc_naive_now() - timedelta(days=max(1, int(days)) - 1)
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    MonitoringEventRecord.source_key,
                    func.date(MonitoringEventRecord.event_at).label("event_date"),
                    func.count(MonitoringEventRecord.id).label("event_count"),
                    func.count(func.distinct(func.date(MonitoringEventRecord.ingested_at))).label("ingest_days"),
                )
                .where(MonitoringEventRecord.event_at >= cutoff)
                .group_by(MonitoringEventRecord.source_key, func.date(MonitoringEventRecord.event_at))
                .order_by(func.date(MonitoringEventRecord.event_at))
            ).all()
        return [{
            "source_key": str(source_key), "date": str(event_date),
            "count": int(event_count or 0), "ingest_days": int(ingest_days or 0),
        } for source_key, event_date, event_count, ingest_days in rows]

    def upsert_events(self, events: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        normalized = list(events)
        created = 0
        updated = 0
        if not normalized:
            return {"created": 0, "updated": 0, "received": 0}
        with self.db.get_session() as session:
            keys = [(str(item["source_key"]), str(item["external_id"])[:160]) for item in normalized]
            existing_by_key: Dict[Tuple[str, str], MonitoringEventRecord] = {}
            for offset in range(0, len(keys), 500):
                chunk = keys[offset:offset + 500]
                rows = session.execute(
                    select(MonitoringEventRecord).where(
                        tuple_(MonitoringEventRecord.source_key, MonitoringEventRecord.external_id).in_(chunk)
                    )
                ).scalars().all()
                existing_by_key.update({(row.source_key, row.external_id): row for row in rows})
            for fields in normalized:
                serialized = self._event_fields(fields)
                event_key = (serialized["source_key"], serialized["external_id"])
                existing = existing_by_key.get(event_key)
                if existing is None:
                    existing = MonitoringEventRecord(**serialized)
                    session.add(existing)
                    existing_by_key[event_key] = existing
                    created += 1
                    continue
                changed = False
                for key, value in serialized.items():
                    if key in {"source_key", "external_id", "ingested_at"}:
                        continue
                    if getattr(existing, key) != value:
                        setattr(existing, key, value)
                        changed = True
                if changed:
                    existing.updated_at = utc_naive_now()
                    updated += 1
            session.commit()
        return {"created": created, "updated": updated, "received": len(normalized)}

    def delete_duplicate_audio_memo_events(
        self,
        *,
        keep_external_id: str,
        source_signature: str,
    ) -> int:
        """Remove old task-id events for an already indexed recording set."""
        removed = 0
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringEventRecord).where(
                    MonitoringEventRecord.source_key == "audio.memo.ai"
                )
            ).scalars().all()
            for row in rows:
                if row.external_id == keep_external_id:
                    continue
                payload = _load(row.raw_payload, {})
                result = payload.get("result") if isinstance(payload, dict) else {}
                source_files = result.get("source_files") if isinstance(result, dict) else []
                if self._audio_source_signature(source_files or []) != source_signature:
                    continue
                session.delete(row)
                removed += 1
            if removed:
                session.commit()
        return removed

    def delete_audio_memo_events_for_topics(self, topic_ids: Iterable[str]) -> int:
        """Remove both native and essay projections for deleted audio memos."""
        targets = {str(value).strip() for value in topic_ids if str(value).strip()}
        if not targets:
            return 0
        removed = 0
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringEventRecord).where(
                    MonitoringEventRecord.external_id.in_(targets),
                    MonitoringEventRecord.source_key.in_(("audio.memo.ai", "zsxq.essays")),
                )
            ).scalars().all()
            for row in rows:
                session.delete(row)
                removed += 1
            if removed:
                session.commit()
        return removed

    def delete_orphaned_audio_memo_essay_events(self) -> int:
        """Delete stale essay projections whose audio memo no longer exists."""
        removed = 0
        with self.db.get_session() as session:
            valid_ids = set(session.execute(
                select(ResearchNote.topic_id).where(ResearchNote.group_id == "ai-audio-memo")
            ).scalars().all())
            rows = session.execute(
                select(MonitoringEventRecord).where(
                    MonitoringEventRecord.source_key == "zsxq.essays",
                    MonitoringEventRecord.external_id.like("audio-memo-%"),
                )
            ).scalars().all()
            for row in rows:
                if row.external_id in valid_ids:
                    continue
                session.delete(row)
                removed += 1
            if removed:
                session.commit()
        return removed

    @staticmethod
    def _audio_source_signature(files: Iterable[Any]) -> str:
        identities = set()
        for item in files:
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("file_id") or "").strip()
            if file_id:
                identities.add(f"file:{file_id}")
                continue
            topic_id = str(item.get("topic_id") or item.get("source_topic_id") or "").strip()
            filename = str(item.get("filename") or item.get("name") or "").strip()
            if topic_id or filename:
                identities.add(f"fallback:{topic_id}:{filename}")
        return "\n".join(sorted(identities))

    def list_events(
        self,
        *,
        days: int = 7,
        symbol: Optional[str] = None,
        perspective: Optional[str] = None,
        event_type: Optional[str] = None,
        source_key: Optional[str] = None,
        channel: Optional[str] = None,
        evidence_level: Optional[str] = None,
        query: Optional[str] = None,
        min_importance: Optional[int] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = [MonitoringEventRecord.event_at >= utc_naive_now() - timedelta(days=max(1, days))]
        if symbol:
            conditions.append(MonitoringEventRecord.symbol_codes.like(f"%{symbol}%"))
        if perspective:
            conditions.append(MonitoringEventRecord.perspective == perspective)
        if event_type:
            conditions.append(MonitoringEventRecord.event_type == event_type)
        else:
            # Intraday quotes are market state, not intelligence. Historical rows
            # remain explicitly queryable for audit but stay out of normal feeds.
            conditions.append(MonitoringEventRecord.event_type != "realtime_quote")
        if source_key:
            conditions.append(MonitoringEventRecord.source_key == source_key)
        if channel:
            channels = [value.strip() for value in channel.split(",") if value.strip()]
            conditions.append(or_(*[
                MonitoringEventRecord.metrics_json.like(f'%"channel":"{value}"%')
                for value in channels
            ]))
        if evidence_level == "factual":
            conditions.extend([
                MonitoringEventRecord.source_key != "zsxq.essays",
                not_(MonitoringEventRecord.metrics_json.like('%"evidence_level":"unverified"%')),
            ])
        elif evidence_level:
            conditions.append(MonitoringEventRecord.metrics_json.like(
                f'%"evidence_level":"{evidence_level}"%'
            ))
        if query:
            pattern = f"%{query.strip()}%"
            conditions.append(or_(
                MonitoringEventRecord.title.like(pattern),
                MonitoringEventRecord.summary.like(pattern),
                MonitoringEventRecord.tags_json.like(pattern),
            ))
        if min_importance is not None:
            conditions.append(MonitoringEventRecord.importance_score >= int(min_importance))
        where_clause = and_(*conditions)
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 200))
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(MonitoringEventRecord.id)).where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(MonitoringEventRecord)
                .where(where_clause)
                .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                .offset((safe_page - 1) * safe_size)
                .limit(safe_size)
                .options(load_only(*_EVENT_LIST_COLUMNS))
            ).scalars().all()
        return [self._event_dict(row) for row in rows], int(total)

    def all_recent_events(self, *, days: int = 7, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            query = (
                select(MonitoringEventRecord)
                .where(MonitoringEventRecord.event_at >= utc_naive_now() - timedelta(days=max(1, days)))
                .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                .options(load_only(*_EVENT_LIST_COLUMNS))
            )
            if limit is not None:
                query = query.limit(max(1, int(limit)))
            rows = session.execute(query).scalars().all()
        return [self._event_dict(row) for row in rows]

    def all_symbol_events(self, *, symbol: str, days: int) -> List[Dict[str, Any]]:
        """Return the complete local evidence set used by stock workspaces.

        Feed endpoints stay paginated, but an aggregate must not silently derive
        coverage and financial snapshots from only the newest page of events.
        Realtime quotes remain in the dedicated market database and are therefore
        deliberately excluded from this factual evidence query.
        """
        cutoff = utc_naive_now() - timedelta(days=max(1, int(days)))
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringEventRecord).where(
                    MonitoringEventRecord.event_at >= cutoff,
                    MonitoringEventRecord.symbol_codes.like(f"%{symbol}%"),
                    MonitoringEventRecord.event_type != "realtime_quote",
                ).order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                .options(load_only(*_EVENT_LIST_COLUMNS))
            ).scalars().all()
        return [self._event_dict(row) for row in rows]

    def recent_symbol_events(
        self,
        *,
        symbol: str,
        days: int,
        event_types: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Read a bounded, type-filtered slice of one symbol's evidence.

        Stock workspaces only need the newest rows for a visible section.  The
        older ``all_symbol_events`` contract remains available to offline
        research jobs, while interactive pages avoid hydrating and JSON-decoding
        thousands of unrelated documents.
        """
        cutoff = utc_naive_now() - timedelta(days=max(1, int(days)))
        types = tuple(dict.fromkeys(str(value) for value in (event_types or ()) if str(value)))
        with self.db.get_session() as session:
            query = select(MonitoringEventRecord).where(
                MonitoringEventRecord.event_at >= cutoff,
                MonitoringEventRecord.symbol_codes.like(f"%{symbol}%"),
                MonitoringEventRecord.event_type != "realtime_quote",
            )
            if types:
                query = query.where(MonitoringEventRecord.event_type.in_(types))
            query = query.order_by(
                desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id),
            ).options(load_only(*_EVENT_LIST_COLUMNS))
            if limit is not None:
                query = query.limit(max(1, int(limit)))
            rows = session.execute(query).scalars().all()
        return [self._event_dict(row) for row in rows]

    def symbol_workspace_bundle(self, *, symbol: str, days: int) -> Dict[str, Any]:
        """Return exact counters plus bounded evidence for an interactive stock page.

        The former workspace path materialized every matching event merely to
        compute counts.  Popular stocks can have tens of thousands of comments
        and news rows, making a three-stock page spend seconds in SQLite and JSON
        parsing.  This method keeps totals as narrow SQL aggregates and fetches
        only the rows that can actually be rendered.
        """
        safe_days = max(1, int(days))
        cutoff = utc_naive_now() - timedelta(days=safe_days)
        conditions = (
            MonitoringEventRecord.event_at >= cutoff,
            MonitoringEventRecord.symbol_codes.like(f"%{symbol}%"),
            MonitoringEventRecord.event_type != "realtime_quote",
        )
        snapshot_types = {
            "market_open_snapshot", "market_snapshot", "fundamental_snapshot",
            "capital_chip_snapshot", "ownership_snapshot", "technical_factor",
            "market_theme_flow", "limit_pool", "company_profile",
        }
        research_types = {
            "research_report_pdf", "institution_forecast", "institution_survey",
            "broker_recommendation",
        }
        message_types = {
            "news", "long_news", "essay", "enterprise_registration",
            "enterprise_risk", "enterprise_credit", "enterprise_ipr",
            "enterprise_history",
        }
        original = case(
            (and_(MonitoringEventRecord.url.is_not(None), MonitoringEventRecord.url != ""), 1),
            else_=0,
        )
        unverified = case(
            (MonitoringEventRecord.metrics_json.like('%"evidence_level":"unverified"%'), 1),
            (MonitoringEventRecord.source_key.in_(("zsxq.essays", "eastmoney.guba_posts")), 1),
            else_=0,
        )

        def fetch(
            session: Any,
            types: Optional[Iterable[str]],
            limit: int,
        ) -> List[Dict[str, Any]]:
            query = select(MonitoringEventRecord).where(*conditions)
            values = tuple(types or ())
            if values:
                query = query.where(MonitoringEventRecord.event_type.in_(values))
            rows = session.execute(
                query.order_by(
                    desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id),
                ).limit(max(1, int(limit))).options(load_only(*_EVENT_LIST_COLUMNS))
            ).scalars().all()
            return [self._event_dict(row) for row in rows]

        with self.db.get_session() as session:
            grouped_rows = session.execute(select(
                MonitoringEventRecord.source_key,
                MonitoringEventRecord.event_type,
                func.count(MonitoringEventRecord.id),
                func.max(MonitoringEventRecord.event_at),
                func.sum(original),
                func.sum(unverified),
            ).where(*conditions).group_by(
                MonitoringEventRecord.source_key,
                MonitoringEventRecord.event_type,
            )).all()
            snapshots = fetch(session, snapshot_types, 200)
            research = fetch(session, research_types, 200)
            announcements = fetch(session, ("company_announcement",), 20)
            essays = fetch(session, ("essay",), 30)
            stock_comments = fetch(session, ("stock_forum_post",), 20)
            messages = fetch(session, message_types, 40)
            timeline = fetch(session, None, 40)

        type_counts: Dict[str, int] = {}
        type_latest: Dict[str, str] = {}
        source_counts: Dict[str, int] = {}
        source_latest: Dict[str, str] = {}
        raw_event_count = decision_event_count = original_link_count = unverified_count = 0
        groups: List[Dict[str, Any]] = []
        for source_key, event_type, count, latest_at, original_count, unverified_rows in grouped_rows:
            raw_count = int(count or 0)
            decision_count = 1 if str(event_type) in snapshot_types else raw_count
            raw_event_count += raw_count
            decision_event_count += decision_count
            original_links = min(decision_count, int(original_count or 0))
            unverified_links = min(decision_count, int(unverified_rows or 0))
            original_link_count += original_links
            unverified_count += unverified_links
            type_counts[str(event_type)] = type_counts.get(str(event_type), 0) + decision_count
            source_counts[str(source_key)] = source_counts.get(str(source_key), 0) + decision_count
            latest_iso = self._iso(latest_at)
            if latest_iso:
                type_latest[str(event_type)] = max(type_latest.get(str(event_type), ""), latest_iso)
                source_latest[str(source_key)] = max(source_latest.get(str(source_key), ""), latest_iso)
            groups.append({
                "source_key": str(source_key), "event_type": str(event_type),
                "count": decision_count, "raw_count": raw_count,
                "latest_at": latest_iso, "original_link_count": original_links,
                "unverified_count": unverified_links,
            })

        return {
            "snapshots": snapshots,
            "research": research,
            "announcements": announcements,
            "essays": essays,
            "stock_comments": stock_comments,
            "messages": messages,
            "timeline": timeline,
            "groups": groups,
            "type_counts": type_counts,
            "type_latest": type_latest,
            "source_counts": source_counts,
            "source_latest": source_latest,
            "raw_event_count": raw_event_count,
            "decision_event_count": decision_event_count,
            "original_link_count": original_link_count,
            "unverified_count": unverified_count,
        }

    def reindex_keyword_symbols(
        self, *, aliases_by_symbol: Dict[str, Iterable[str]], days: int = 3650,
        event_types: Iterable[str] = ("essay",),
    ) -> Dict[str, int]:
        """Attach watchlist symbols to persisted text events using explicit aliases.

        This is intentionally additive: source adapters remain authoritative for
        event content, while the local symbol index can be expanded when a new
        watchlist stock or shorthand alias becomes known.
        """
        normalized = {
            str(symbol): tuple(dict.fromkeys(
                str(alias).strip() for alias in aliases if str(alias).strip()
            ))
            for symbol, aliases in aliases_by_symbol.items()
            if str(symbol).strip()
        }
        types = tuple(str(value) for value in event_types if str(value))
        if not normalized or not types:
            return {"scanned": 0, "updated": 0}
        cutoff = utc_naive_now() - timedelta(days=max(1, int(days)))
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringEventRecord).where(
                    MonitoringEventRecord.event_at >= cutoff,
                    MonitoringEventRecord.event_type.in_(types),
                )
            ).scalars().all()
            updated = 0
            for row in rows:
                text = f"{row.title or ''}\n{row.summary or ''}"
                symbols = {value for value in (row.symbol_codes or "").split(",") if value}
                before = set(symbols)
                for symbol, aliases in normalized.items():
                    if any(alias in text for alias in aliases):
                        symbols.add(symbol)
                if symbols != before:
                    row.symbol_codes = ",".join(sorted(symbols))
                    row.updated_at = utc_naive_now()
                    updated += 1
            if updated:
                session.commit()
        return {"scanned": len(rows), "updated": updated}

    def list_events_between(
        self, *, event_types: Iterable[str], start_at: datetime, end_at: datetime,
        symbol: Optional[str] = None, query: Optional[str] = None,
        page: int = 1, page_size: int = 100,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Query a bounded factual dataset by exchange-date boundaries."""
        types = [str(value) for value in event_types if str(value)]
        conditions = [
            MonitoringEventRecord.event_type.in_(types),
            MonitoringEventRecord.event_at >= start_at,
            MonitoringEventRecord.event_at < end_at,
        ]
        if symbol:
            conditions.append(MonitoringEventRecord.symbol_codes.like(f"%{symbol}%"))
        if query:
            pattern = f"%{query.strip()}%"
            conditions.append(or_(
                MonitoringEventRecord.title.like(pattern),
                MonitoringEventRecord.summary.like(pattern),
                MonitoringEventRecord.symbol_codes.like(pattern),
                MonitoringEventRecord.actors_json.like(pattern),
                MonitoringEventRecord.metrics_json.like(pattern),
            ))
        where_clause = and_(*conditions)
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 500))
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(MonitoringEventRecord.id)).where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(MonitoringEventRecord)
                .where(where_clause)
                .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                .offset((safe_page - 1) * safe_size)
                .limit(safe_size)
            ).scalars().all()
        return [self._event_dict(row) for row in rows], int(total)

    def all_events_between(
        self, *, event_types: Iterable[str], start_at: datetime, end_at: datetime,
    ) -> List[Dict[str, Any]]:
        types = [str(value) for value in event_types if str(value)]
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringEventRecord).where(
                    MonitoringEventRecord.event_type.in_(types),
                    MonitoringEventRecord.event_at >= start_at,
                    MonitoringEventRecord.event_at < end_at,
                ).order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
            ).scalars().all()
        return [self._event_dict(row) for row in rows]

    def get_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.get(MonitoringEventRecord, int(event_id))
            return self._event_dict(row) if row is not None else None

    def list_announcements(
        self, *, start_at: datetime, end_at: datetime, symbol: Optional[str] = None,
        category: Optional[str] = None, query: Optional[str] = None,
        page: int = 1, page_size: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = [
            MonitoringEventRecord.source_key == "cninfo.announcements",
            MonitoringEventRecord.event_type == "company_announcement",
            MonitoringEventRecord.event_at >= start_at,
            MonitoringEventRecord.event_at <= end_at,
        ]
        if symbol:
            conditions.append(MonitoringEventRecord.symbol_codes.like(f"%{symbol}%"))
        if category:
            conditions.append(MonitoringEventRecord.metrics_json.like(f"%{category}%"))
        if query:
            pattern = f"%{query.strip()}%"
            conditions.append(or_(
                MonitoringEventRecord.title.like(pattern),
                MonitoringEventRecord.summary.like(pattern),
                MonitoringEventRecord.symbol_codes.like(pattern),
                MonitoringEventRecord.actors_json.like(pattern),
            ))
        where_clause = and_(*conditions)
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 500))
        with self.db.get_session() as session:
            total = session.execute(select(func.count(MonitoringEventRecord.id)).where(where_clause)).scalar() or 0
            rows = session.execute(
                select(MonitoringEventRecord).where(where_clause)
                .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                .offset((safe_page - 1) * safe_size).limit(safe_size)
                .options(load_only(*_EVENT_LIST_COLUMNS))
            ).scalars().all()
        return [self._event_dict(row) for row in rows], int(total)

    def get_events_by_ids(self, event_ids: Iterable[int]) -> List[Dict[str, Any]]:
        ids = list(dict.fromkeys(int(value) for value in event_ids))
        if not ids:
            return []
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringEventRecord)
                .where(MonitoringEventRecord.id.in_(ids))
                .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                .options(load_only(*_EVENT_LIST_COLUMNS))
            ).scalars().all()
        return [self._event_dict(row) for row in rows]

    @staticmethod
    def _event_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "source_key": str(fields["source_key"])[:100],
            "source_name": str(fields["source_name"])[:120],
            "source_type": str(fields["source_type"])[:32],
            "external_id": str(fields["external_id"])[:160],
            "event_type": str(fields["event_type"])[:40],
            "perspective": str(fields["perspective"])[:20],
            "title": str(fields["title"] or "无标题事件")[:500],
            "summary": str(fields.get("summary") or "")[:10000] or None,
            "url": str(fields.get("url") or "")[:1000] or None,
            "symbol_codes": ",".join(sorted(set(fields.get("symbols") or []))),
            "sentiment": str(fields.get("sentiment") or "neutral")[:20],
            "importance_score": max(0, min(int(fields.get("importance_score") or 50), 100)),
            "confidence_score": max(0.0, min(float(fields.get("confidence_score") or 0.5), 1.0)),
            "tags_json": _dump(fields.get("tags") or []),
            "actors_json": _dump(fields.get("actors") or []),
            "metrics_json": _dump(fields.get("metrics") or {}),
            "raw_payload": _dump(fields.get("raw_payload") or {}),
            "event_at": fields.get("event_at") or utc_naive_now(),
            "ingested_at": fields.get("ingested_at") or utc_naive_now(),
        }

    @staticmethod
    def _source_dict(row: MonitoringSourceRecord) -> Dict[str, Any]:
        return {
            "id": row.id,
            "source_key": row.source_key,
            "name": row.name,
            "adapter_type": row.adapter_type,
            "provider": row.provider,
            "category": row.category,
            "enabled": bool(row.enabled),
            "poll_interval_seconds": int(row.poll_interval_seconds),
            "config": _load(row.config_json, {}),
            "last_status": row.last_status,
            "last_error": row.last_error,
            "last_started_at": InvestmentMonitorRepository._iso(row.last_started_at),
            "last_success_at": InvestmentMonitorRepository._iso(row.last_success_at),
            "last_item_count": int(row.last_item_count or 0),
            "last_received_count": int(row.last_received_count or 0),
            "last_created_count": int(row.last_created_count or 0),
            "last_updated_count": int(row.last_updated_count or 0),
            "last_duration_ms": int(row.last_duration_ms or 0),
            "total_item_count": int(row.total_item_count or 0),
        }

    @staticmethod
    def _event_dict(row: MonitoringEventRecord) -> Dict[str, Any]:
        return {
            "id": row.id,
            "source_key": row.source_key,
            "source_name": row.source_name,
            "source_type": row.source_type,
            "external_id": row.external_id,
            "event_type": row.event_type,
            "perspective": row.perspective,
            "title": row.title,
            "summary": row.summary,
            "url": row.url,
            "symbols": [value for value in (row.symbol_codes or "").split(",") if value],
            "sentiment": row.sentiment,
            "importance_score": int(row.importance_score or 0),
            "confidence_score": float(row.confidence_score or 0),
            "tags": _load(row.tags_json, []),
            "actors": _load(row.actors_json, []),
            "metrics": _load(row.metrics_json, {}),
            "event_at": InvestmentMonitorRepository._iso(row.event_at),
            "ingested_at": InvestmentMonitorRepository._iso(row.ingested_at),
        }

    @staticmethod
    def _iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() + "Z" if value else None

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
