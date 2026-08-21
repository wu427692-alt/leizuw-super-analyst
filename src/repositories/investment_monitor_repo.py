# -*- coding: utf-8 -*-
"""Persistence for pluggable investment-monitor sources and normalized events."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, desc, func, not_, or_, select, tuple_

from src.storage import (
    DatabaseManager,
    MonitoringEventRecord,
    MonitoringSourceRecord,
    WatchlistBackfillRecord,
    utc_naive_now,
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
            rows = session.execute(
                select(
                    MonitoringEventRecord.source_key,
                    func.max(MonitoringEventRecord.event_at),
                    func.max(MonitoringEventRecord.ingested_at),
                    func.count(MonitoringEventRecord.id),
                ).group_by(MonitoringEventRecord.source_key)
            ).all()
        return {
            str(source_key): {
                "latest_event_at": self._iso(latest_event_at),
                "latest_ingested_at": self._iso(latest_ingested_at),
                "stored_event_count": int(event_count or 0),
            }
            for source_key, latest_event_at, latest_ingested_at, event_count in rows
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
                retry_seconds = max(60, min(cadence * 2, 1800))
                if last_started + timedelta(seconds=retry_seconds) > current:
                    continue
            elif status == "not_configured" and last_started is not None:
                if last_started + timedelta(seconds=max(cadence, 3600)) > current:
                    continue
            elif status == "running" and last_started is not None:
                # Do not schedule a parallel copy of a source that still has a
                # plausible in-flight request. The watchdog will wake the owner
                # again after the stale-running window passes.
                if last_started + timedelta(seconds=max(300, cadence * 3)) > current:
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
            ).scalars().all()
        return [self._event_dict(row) for row in rows], int(total)

    def all_recent_events(self, *, days: int = 7) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringEventRecord)
                .where(MonitoringEventRecord.event_at >= utc_naive_now() - timedelta(days=max(1, days)))
                .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
            ).scalars().all()
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
            ).scalars().all()
        return [self._event_dict(row) for row in rows]

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
