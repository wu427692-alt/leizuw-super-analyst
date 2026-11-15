# -*- coding: utf-8 -*-
"""Persistence for pluggable investment-monitor sources and normalized events."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
import unicodedata

from sqlalchemy import and_, case, desc, false, func, literal, not_, or_, select, true, tuple_, union_all
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only

from src.storage import (
    DatabaseManager,
    MonitoringEventContentRecord,
    MonitoringEventRecord,
    MonitoringEventSymbolRecord,
    MonitoringSourceRecord,
    ResearchNote,
    WatchlistBackfillRecord,
    WatchlistAnnouncementSyncRecord,
    utc_naive_now,
)
from src.utils.research_stock_relation import (
    RESEARCH_EVENT_TYPES, direct_research_condition, research_stock_relation,
)
from src.utils.financial_statement_selection import (
    FINANCIAL_SECTIONS, FINANCIAL_WHITESPACE, financial_content_identity,
)


# Event responses expose saved excerpts and structured metrics, not the often
# large raw source payload. Read original blobs explicitly only in code paths
# that actually need them (such as source-backed research attribution).
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

_ANNOUNCEMENT_SOURCE_KEYS = (
    "cninfo.announcements",
    "sse.announcements",
    "szse.announcements",
)
_SHANGHAI = timezone(timedelta(hours=8))

# 用户明确不要「同一篇新闻多源重复」。判据是归一化标题相同 + 时间相邻。
#
# 时间窗口刻意收在 12 小时而不是「近 7 天」：同一篇新闻被多个来源转载几乎都发生
# 在分钟级（生产库实测跨源重复的时间差 p90 = 0.4 小时），而盘面/策划类标题
# （「周五美股盘前你需要了解的全球要闻」「A股开盘：三大指数集体低开」
# 「南向资金净买入额达10亿港元」）每周/每天都会以完全相同的标题重新发布。
# 用 7 天窗口会把这类真实的不同事件当重复删掉——生产库副本实测会多删 1,470 条，
# 其中 1,325 条是同源周期性栏目。窗口改为 12 小时后，这类周期性标题（间隔 ≥ 24h）
# 全部保留，只合并真正的重复转载。
_NEWS_EVENT_TYPES = ("news", "long_news")
_NEWS_DEDUPE_WINDOW = timedelta(hours=12)
# 归一化后不足 12 个字符的标题（「早盘」「涨停」这类）没有区分度，不参与去重。
_NEWS_DEDUPE_MIN_LENGTH = 12
# 同标题重复时保留谁：主流财经媒体 > tushare 长篇新闻 > RSS/NewsNow。
# 未知来源用默认值，保证不会被 RSS/NewsNow 反超。
_NEWS_SOURCE_AUTHORITY = {
    "tushare.news.cls": 100,
    "tushare.news.yicai": 95,
    "tushare.news.sina": 90,
    "tushare.news.eastmoney": 85,
    "tushare.news.wallstreetcn": 80,
    "tushare.news.10jqka": 75,
    "tushare.long_news": 50,
    "feeds.intelligence": 20,
}
_NEWS_SOURCE_AUTHORITY_DEFAULT = 60
# 用户已明确不要的监测源（东方财富股吧帖子）：停采，存量也清理。
_REJECTED_MONITORING_SOURCE_KEYS = ("eastmoney.guba_posts",)
# A modeled cost/profit distribution is not a directional investment opinion.
# Project legacy snapshot labels consistently at read time, without rewriting
# saved rows or changing sentiment for news, research or other event types.
_CAPITAL_SNAPSHOT_TYPE = "capital_chip_snapshot"
_READ_SENTIMENT = case(
    (MonitoringEventRecord.event_type == _CAPITAL_SNAPSHOT_TYPE, "neutral"),
    else_=MonitoringEventRecord.sentiment,
)

# `recent_event_stats` 是纯聚合读（count / count(distinct) / 若干 CASE 求和），
# 线上实测在 55 万行的 monitoring_events 上单次约 330ms，而看板一次构建会调用它，
# 且多个面板共用同一结果。聚合结果对秒级延迟不敏感，用短 TTL 内存缓存即可，
# 既不动表结构也不改变任何筛选语义。
_RECENT_EVENT_STATS_TTL_SECONDS = 30.0
_recent_event_stats_cache: Dict[Tuple[str, int], Tuple[float, Dict[str, Any]]] = {}
_recent_event_stats_lock = __import__("threading").Lock()


def _event_symbol_condition(symbol: str) -> Any:
    """Match one complete stored CSV token, not a substring or LIKE pattern.

    The token is resolved through ``monitoring_event_symbols``, whose primary
    key is ``(event_id, symbol)``. The correlated ``EXISTS`` lets SQLite keep the
    selective outer access path (``event_at`` range) and turn the symbol test
    into a primary-key probe. It is deliberately not ``id IN (SELECT ...)``: a
    materialized set of a popular symbol's event_ids makes the planner scan the
    outer table by rowid, which measured ~36x slower than the old ``LIKE``.

    Callers retain responsibility for market-code canonicalization; do not
    collapse distinct exchanges or introduce aliases while selecting facts.
    """
    value = str(symbol or "").strip()
    if not value or "," in value:
        return false()
    return select(MonitoringEventSymbolRecord.event_id).where(
        MonitoringEventSymbolRecord.event_id == MonitoringEventRecord.id,
        MonitoringEventSymbolRecord.symbol == value,
    ).correlate(MonitoringEventRecord).exists()


def _financial_calendar_date_expr(value: Any) -> Any:
    """SQLite equivalent of financial_calendar_date, for narrow row ranking."""
    raw = func.trim(func.coalesce(value, ""), FINANCIAL_WHITESPACE)
    compact = func.replace(raw, "-", "")
    shape = and_(
        func.length(compact) == 8,
        compact.op("NOT GLOB")("*[^0-9]*"),
        or_(func.length(raw) == 8, and_(
            func.length(raw) == 10, func.substr(raw, 5, 1) == "-", func.substr(raw, 8, 1) == "-",
        )),
    )
    iso = func.printf("%s-%s-%s", func.substr(compact, 1, 4), func.substr(compact, 5, 2), func.substr(compact, 7, 2))
    # The explicit calendar round-trip rejects e.g. February 30, not merely a
    # malformed shape. SQLite permits year 0000; Python/source dates do not.
    valid = and_(shape, func.substr(compact, 1, 4) != "0000",
                 func.strftime("%Y%m%d", iso, "+0 days") == compact)
    return case((valid, compact), else_="")


def _financial_statement_scope_expr(section: Any) -> Any:
    """SQL counterpart to financial_statement_scope_rank; -1 is incompatible."""
    kind = func.trim(func.coalesce(func.json_extract(section, "$.report_type"), ""), FINANCIAL_WHITESPACE)
    json_kind = func.json_type(section, "$.report_type")
    return case(
        (json_kind.in_(("true", "false", "object", "array")), -1),
        (kind.in_(("1", "4")), 2), (kind == "5", 1), (kind == "", 0),
        else_=-1,
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


def normalized_title_key(value: Any, actors_json: Any = "[]") -> str:
    """把标题归一成可比较的 key，公告与新闻共用同一套实现。

    依次做 HTML 反转义、去标签、NFKC、casefold、去空白，并剥离 ``actors``
    里记录的来源名前缀（``新华网:`` 这类），让同一篇内容在不同来源的标题
    写法落到同一个 key 上。新闻多源去重直接复用它，不另写平行实现。
    """
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", "", text)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"\s+", "", text)
    actors = _load(actors_json, []) if isinstance(actors_json, str) else actors_json
    if isinstance(actors, list):
        for actor in actors:
            actor_text = html.unescape(str(actor or ""))
            actor_text = unicodedata.normalize("NFKC", actor_text).casefold()
            actor_text = re.sub(r"\s+", "", actor_text)
            prefix = f"{actor_text}:"
            if actor_text and text.startswith(prefix):
                return text[len(prefix):]
    return text


def news_dedupe_key(title: Any, actors_json: Any = "[]") -> Optional[str]:
    """新闻去重键：归一化标题的哈希；归一化后太短时返回 None（不参与去重）。

    存哈希而不是整段中文标题，是为了让 ``monitoring_events.dedupe_key`` 上的
    索引保持小、与文本排序规则无关；只做等值比较，32 位十六进制足够。
    """
    normalized = normalized_title_key(title, actors_json)
    if len(normalized) < _NEWS_DEDUPE_MIN_LENGTH:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def is_news_event_type(value: Any) -> bool:
    """新闻类事件（快讯 / 长篇新闻）才参与英文过滤与多源去重。"""
    return str(value or "") in _NEWS_EVENT_TYPES


def news_source_authority(source_key: Any) -> int:
    """来源权威度：同标题多来源重复时决定保留哪一条。"""
    return _NEWS_SOURCE_AUTHORITY.get(
        str(source_key or "").strip(), _NEWS_SOURCE_AUTHORITY_DEFAULT,
    )


def news_dedupe_window_match(left: Any, right: Any) -> bool:
    """两个事件时间是否落在同一个去重窗口内（``_NEWS_DEDUPE_WINDOW``）。

    任一侧不是 datetime 时返回 False：宁可多留一条，也不要有未定义时间的事件
    被当成重复删掉。
    """
    if not isinstance(left, datetime) or not isinstance(right, datetime):
        return False
    try:
        return abs(left - right) <= _NEWS_DEDUPE_WINDOW
    except TypeError:
        # naive / aware 混用：这两个列在库里都是 naive UTC，出现混用说明调用方
        # 传错了数据，保守跳过比较。
        return False


def news_dedupe_keeper_rank(source_key: Any, ingested_at: Any, event_id: Any) -> Tuple[int, datetime, int]:
    """同标题重复组里「该留哪一条」的排序键：越权威越前，同权威越早入库越前。"""
    ingested = ingested_at if isinstance(ingested_at, datetime) else datetime.max
    return (-news_source_authority(source_key), ingested, int(event_id or 0))


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
        days = max(30, min(int(days), 730))
        with self.db.get_session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            active = session.execute(
                select(WatchlistBackfillRecord).where(
                    WatchlistBackfillRecord.symbol == symbol,
                    WatchlistBackfillRecord.status.in_(("pending", "running")),
                    # A running half-year job cannot satisfy a new two-year
                    # request. Keep its scope immutable and queue a successor.
                    WatchlistBackfillRecord.days >= days,
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
            # 计数用一次 GROUP BY 走覆盖索引取全（实测 40 万行 125ms），
            # 命中单源的 LIMIT 1 仍按 (source_key, event_at) 索引取最后一条。
            # 原来的实现每个源各发一条 COUNT，27 个源共 54 条查询、实测 2.97s。
            count_rows = session.execute(
                select(MonitoringEventRecord.source_key, func.count(MonitoringEventRecord.id))
                .group_by(MonitoringEventRecord.source_key)
            ).all()
            counts = {str(key): int(count or 0) for key, count in count_rows}
            result: Dict[str, Dict[str, Any]] = {}
            for source_key in source_keys:
                latest = session.execute(
                    select(MonitoringEventRecord.event_at, MonitoringEventRecord.ingested_at)
                    .where(MonitoringEventRecord.source_key == source_key)
                    .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                    .limit(1)
                ).first()
                result[str(source_key)] = {
                    "latest_event_at": self._iso(latest[0]) if latest else None,
                    "latest_ingested_at": self._iso(latest[1]) if latest else None,
                    "stored_event_count": counts.get(str(source_key), 0),
                }
        return result

    def recent_event_stats(self, *, days: int = 7) -> Dict[str, Any]:
        """Exact narrow-column aggregates for dashboards without hydrating documents."""
        # 缓存必须按数据库实例区分：同一进程里可能先后绑定不同库（测试、多环境切换），
        # 只用 days 做 key 会把 A 库的统计结果返回给 B 库。
        database_key = str(getattr(self.db, "_db_url", id(self.db)))
        cache_key = (database_key, max(1, int(days)))
        now_monotonic = __import__("time").monotonic()
        with _recent_event_stats_lock:
            cached = _recent_event_stats_cache.get(cache_key)
        if cached and now_monotonic - cached[0] <= _RECENT_EVENT_STATS_TTL_SECONDS:
            return cached[1]
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
                func.sum(case((_READ_SENTIMENT == "bullish", 1), else_=0)),
                func.sum(case((_READ_SENTIMENT == "bearish", 1), else_=0)),
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
                    _READ_SENTIMENT,
                    func.count(MonitoringEventRecord.id),
                ).where(*conditions).group_by(
                    MonitoringEventRecord.source_key,
                    _READ_SENTIMENT,
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

            stats = {
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
            with _recent_event_stats_lock:
                _recent_event_stats_cache[cache_key] = (now_monotonic, stats)
            return stats

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
            _event_symbol_condition(symbol),
            MonitoringEventRecord.event_type != "realtime_quote",
        )
        today = utc_naive_now().date().isoformat()
        direction = case(
            (_READ_SENTIMENT == "bullish", 1),
            (_READ_SENTIMENT == "bearish", -1),
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
            "sentiment": grouped(_READ_SENTIMENT),
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
        serialized_events = [self._event_fields(item) for item in normalized]
        with self.db.get_session() as session:
            if (
                session.get_bind().dialect.name == "sqlite"
                and any(self._is_exchange_announcement(item) for item in serialized_events)
            ):
                # Claim SQLite's single writer before the natural-key lookup.
                # Otherwise two processes can both observe no cross-source row
                # and race to insert it. No upstream I/O occurs while held.
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            # Financial source records are immutable content versions. Insert
            # before any lookup so concurrent writers contend on SQLite's
            # unique constraint, not an application-level read-then-write race.
            for item in serialized_events:
                if item["event_type"] != "fundamental_snapshot":
                    continue
                item["external_id"] = financial_content_identity(
                    item["source_key"], item["symbol_codes"].split(","), _load(item["metrics_json"], {}),
                )
                result = session.execute(sqlite_insert(MonitoringEventRecord).values(**item).on_conflict_do_nothing(
                    index_elements=["source_key", "external_id"],
                ))
                created += int(result.rowcount > 0)
            # Never pass content versions (including a duplicate version) into
            # the mutable upsert path. Legacy financial rows keep their IDs and
            # original payloads; all other event types retain existing behavior.
            serialized_events = [item for item in serialized_events if item["event_type"] != "fundamental_snapshot"]
            keys = [(item["source_key"], item["external_id"]) for item in serialized_events]
            existing_by_key: Dict[Tuple[str, str], MonitoringEventRecord] = {}
            for offset in range(0, len(keys), 500):
                chunk = keys[offset:offset + 500]
                rows = session.execute(
                    select(MonitoringEventRecord).where(
                        tuple_(MonitoringEventRecord.source_key, MonitoringEventRecord.external_id).in_(chunk)
                    )
                ).scalars().all()
                existing_by_key.update({(row.source_key, row.external_id): row for row in rows})
            announcement_rows = self._announcement_dedup_candidates(session, serialized_events)
            announcement_by_alias: Dict[Tuple[str, str, str, str], List[MonitoringEventRecord]] = {}
            announcement_by_external: Dict[
                Tuple[str, str, str], List[Tuple[MonitoringEventRecord, str]]
            ] = {}
            announcement_by_content: Dict[Tuple[str, str, str], List[MonitoringEventRecord]] = {}
            for row in announcement_rows:
                self._index_announcement_candidate(
                    row, announcement_by_alias, announcement_by_external, announcement_by_content,
                )
            news_dedupe_candidates = self._news_dedupe_candidates(session, serialized_events)

            for serialized in serialized_events:
                event_key = (serialized["source_key"], serialized["external_id"])
                existing = existing_by_key.get(event_key)
                if existing is None:
                    duplicate = self._cross_source_announcement_match(
                        serialized, announcement_by_alias,
                        announcement_by_external, announcement_by_content,
                    )
                    if duplicate is not None:
                        if self._append_announcement_provenance(duplicate, serialized):
                            duplicate.updated_at = utc_naive_now()
                            updated += 1
                        self._index_announcement_candidate(
                            duplicate, announcement_by_alias,
                            announcement_by_external, announcement_by_content,
                        )
                        continue
                    duplicate = self._news_duplicate_match(serialized, news_dedupe_candidates)
                    if duplicate is not None:
                        # 同一篇新闻已经在窗口内入库：不再新增一条，只把新来源
                        # 记进 _source_provenance，保留原 ID，列表不会抖动。
                        if self._append_announcement_provenance(duplicate, serialized):
                            duplicate.updated_at = utc_naive_now()
                            updated += 1
                        continue
                    existing = MonitoringEventRecord(**serialized)
                    session.add(existing)
                    existing_by_key[event_key] = existing
                    self._index_announcement_candidate(
                        existing, announcement_by_alias,
                        announcement_by_external, announcement_by_content,
                    )
                    if serialized.get("dedupe_key"):
                        news_dedupe_candidates.setdefault(
                            serialized["dedupe_key"], [],
                        ).append(existing)
                    created += 1
                    continue
                self._preserve_announcement_provenance(existing, serialized)
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

    @classmethod
    def _news_dedupe_candidates(
        cls, session: Any, serialized_events: Iterable[Dict[str, Any]],
    ) -> Dict[str, List[MonitoringEventRecord]]:
        """按去重键捞出窗口内已入库的新闻行，供写入路径判断是否重复。

        键是归一化标题的哈希，走 ``(dedupe_key, event_at)`` 索引；只取本批
        事件前后一个去重窗口，避免一次历史回填把整段新闻史读进内存。
        """
        keyed = [item for item in serialized_events if item.get("dedupe_key")]
        moments = [
            item["event_at"] for item in keyed if isinstance(item.get("event_at"), datetime)
        ]
        if not keyed or not moments:
            return {}
        keys = sorted({str(item["dedupe_key"]) for item in keyed})
        start = min(moments) - _NEWS_DEDUPE_WINDOW
        end = max(moments) + _NEWS_DEDUPE_WINDOW
        candidates: Dict[str, List[MonitoringEventRecord]] = {}
        for offset in range(0, len(keys), 500):
            chunk = keys[offset:offset + 500]
            rows = session.execute(
                select(MonitoringEventRecord).where(
                    MonitoringEventRecord.dedupe_key.in_(chunk),
                    MonitoringEventRecord.event_at >= start,
                    MonitoringEventRecord.event_at < end,
                )
            ).scalars().all()
            for row in rows:
                candidates.setdefault(str(row.dedupe_key), []).append(row)
        return candidates

    @classmethod
    def _news_duplicate_match(
        cls,
        serialized: Dict[str, Any],
        candidates: Dict[str, List[MonitoringEventRecord]],
    ) -> Optional[MonitoringEventRecord]:
        """同归一化标题且落在去重窗口内 = 同一篇新闻；返回窗口内最权威的一条。"""
        key = serialized.get("dedupe_key")
        event_at = serialized.get("event_at")
        if not key or not isinstance(event_at, datetime):
            return None
        best: Optional[MonitoringEventRecord] = None
        best_rank: Optional[Tuple[int, datetime, int]] = None
        for row in candidates.get(str(key), []):
            if not news_dedupe_window_match(row.event_at, event_at):
                continue
            rank = news_dedupe_keeper_rank(row.source_key, row.ingested_at, row.id)
            if best_rank is None or rank < best_rank:
                best, best_rank = row, rank
        return best

    @classmethod
    def _announcement_dedup_candidates(
        cls, session: Any, serialized_events: Iterable[Dict[str, Any]],
    ) -> List[MonitoringEventRecord]:
        identities = [
            cls._announcement_content_identity(item)
            for item in serialized_events
            if cls._is_exchange_announcement(item)
        ]
        valid = [identity for identity in identities if identity is not None]
        if not valid:
            return []
        symbols = sorted({identity[0] for identity in valid})
        days = [date.fromisoformat(identity[1]) for identity in valid]
        # event_at is persisted as naive UTC while disclosure_date is a Shanghai
        # natural day. The one-day margin covers both UTC boundaries without
        # loading an unbounded announcement history into memory.
        start_at = datetime.combine(min(days) - timedelta(days=1), datetime.min.time())
        end_at = datetime.combine(max(days) + timedelta(days=2), datetime.min.time())
        return list(session.execute(select(MonitoringEventRecord).where(
            MonitoringEventRecord.source_key.in_(_ANNOUNCEMENT_SOURCE_KEYS),
            MonitoringEventRecord.event_type == "company_announcement",
            MonitoringEventRecord.symbol_codes.in_(symbols),
            MonitoringEventRecord.event_at >= start_at,
            MonitoringEventRecord.event_at < end_at,
        )).scalars().all())

    @classmethod
    def _index_announcement_candidate(
        cls,
        row: MonitoringEventRecord,
        by_alias: Dict[Tuple[str, str, str, str], List[MonitoringEventRecord]],
        by_external: Dict[
            Tuple[str, str, str], List[Tuple[MonitoringEventRecord, str]]
        ],
        by_content: Dict[Tuple[str, str, str], List[MonitoringEventRecord]],
    ) -> None:
        if not cls._is_exchange_announcement(row):
            return
        symbol = cls._single_announcement_symbol(row.symbol_codes)
        if not symbol:
            return
        for alias in cls._announcement_source_aliases(row):
            source_key = str(alias.get("source_key") or "").strip()
            external_id = str(alias.get("external_id") or "").strip()
            disclosure_date = str(alias.get("disclosure_date") or "").strip()
            if not source_key or not external_id or not disclosure_date:
                continue
            alias_rows = by_alias.setdefault(
                (source_key, symbol, disclosure_date, external_id), [],
            )
            if all(candidate is not row for candidate in alias_rows):
                alias_rows.append(row)
            external_rows = by_external.setdefault(
                (symbol, disclosure_date, external_id), [],
            )
            if all(candidate is not row or candidate_source != source_key
                   for candidate, candidate_source in external_rows):
                external_rows.append((row, source_key))
        identity = cls._announcement_content_identity(row)
        if identity is not None:
            content_rows = by_content.setdefault(identity, [])
            if all(candidate is not row for candidate in content_rows):
                content_rows.append(row)

    @classmethod
    def _cross_source_announcement_match(
        cls,
        serialized: Dict[str, Any],
        by_alias: Dict[Tuple[str, str, str, str], List[MonitoringEventRecord]],
        by_external: Dict[
            Tuple[str, str, str], List[Tuple[MonitoringEventRecord, str]]
        ],
        by_content: Dict[Tuple[str, str, str], List[MonitoringEventRecord]],
    ) -> Optional[MonitoringEventRecord]:
        if not cls._is_exchange_announcement(serialized):
            return None
        source_key = serialized["source_key"]
        symbol = cls._single_announcement_symbol(serialized["symbol_codes"])
        if not symbol:
            return None
        external_id = str(serialized.get("external_id") or "").strip()
        disclosure_date = cls._announcement_disclosure_date(
            serialized.get("metrics_json"), serialized.get("event_at"),
        )
        if not external_id or not disclosure_date:
            return None

        # Once a fallback id was attached as provenance, it is a stable alias
        # even if a later upstream refresh changes that source's display title.
        aliases = cls._unique_announcement_rows(by_alias.get(
            (source_key, symbol, disclosure_date, external_id), [],
        ))
        if len(aliases) == 1:
            return aliases[0]
        if len(aliases) > 1:
            return None

        shared_id_rows = cls._unique_announcement_rows([
            row
            for row, candidate_source in by_external.get(
                (symbol, disclosure_date, external_id), [],
            )
            if cls._has_shared_announcement_id_namespace(source_key, candidate_source)
        ])
        if len(shared_id_rows) == 1:
            return shared_id_rows[0]
        if len(shared_id_rows) > 1:
            return None

        candidates: Dict[int, MonitoringEventRecord] = {}
        content_identity = cls._announcement_content_identity(serialized)
        if content_identity is not None:
            content_rows = cls._unique_announcement_rows(by_content.get(content_identity, []))
            # Even if one candidate would later be rejected as the same source,
            # multiple stored rows make cross-source pairing ambiguous.
            if len(content_rows) != 1:
                return None
            for row in content_rows:
                source_aliases = cls._announcement_source_aliases(row)
                candidate_sources = {
                    str(alias.get("source_key") or "").strip()
                    for alias in source_aliases
                }
                # Content metadata never collapses two ids from one source.
                if source_key in candidate_sources:
                    continue
                # CNINFO and SZSE have a proven shared id namespace, so a
                # differing id is evidence against treating their titles as one
                # document. SSE has independent ids and needs the strict
                # symbol/day/title identity path.
                if source_key != "sse.announcements" and "sse.announcements" not in candidate_sources:
                    continue
                candidates[id(row)] = row
        # Multiple exact candidates indicate pre-existing duplicate evidence.
        # Retain the new row instead of guessing which historical row is primary.
        return next(iter(candidates.values())) if len(candidates) == 1 else None

    @staticmethod
    def _unique_announcement_rows(
        rows: Iterable[MonitoringEventRecord],
    ) -> List[MonitoringEventRecord]:
        unique: Dict[int, MonitoringEventRecord] = {}
        for row in rows:
            unique[id(row)] = row
        return list(unique.values())

    @classmethod
    def _append_announcement_provenance(
        cls, existing: MonitoringEventRecord, incoming: Dict[str, Any],
    ) -> bool:
        metrics = _load(existing.metrics_json, {})
        metrics = dict(metrics) if isinstance(metrics, dict) else {}
        incoming_metrics = _load(incoming.get("metrics_json"), {})
        incoming_metrics = incoming_metrics if isinstance(incoming_metrics, dict) else {}
        before = _dump(metrics)
        provenance = cls._merged_announcement_provenance(
            metrics.get("_source_provenance"),
            cls._announcement_provenance_entry(existing),
            incoming_metrics.get("_source_provenance"),
            cls._announcement_provenance_entry(incoming),
        )
        metrics["_source_provenance"] = provenance
        after = _dump(metrics)
        if after == before:
            return False
        existing.metrics_json = after
        return True

    @classmethod
    def _preserve_announcement_provenance(
        cls, existing: MonitoringEventRecord, incoming: Dict[str, Any],
    ) -> None:
        if not cls._is_exchange_announcement(incoming):
            return
        existing_metrics = _load(existing.metrics_json, {})
        incoming_metrics = _load(incoming.get("metrics_json"), {})
        if not isinstance(existing_metrics, dict) or not isinstance(incoming_metrics, dict):
            return
        existing_provenance = existing_metrics.get("_source_provenance")
        incoming_provenance = incoming_metrics.get("_source_provenance")
        if not existing_provenance and not incoming_provenance:
            return
        incoming_metrics["_source_provenance"] = cls._merged_announcement_provenance(
            existing_provenance,
            cls._announcement_provenance_entry(existing),
            incoming_provenance,
            cls._announcement_provenance_entry(incoming),
        )
        incoming["metrics_json"] = _dump(incoming_metrics)

    @staticmethod
    def _merged_announcement_provenance(*groups: Any) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for group in groups:
            values = group if isinstance(group, list) else [group]
            for value in values:
                if not isinstance(value, dict):
                    continue
                source_key = str(value.get("source_key") or "").strip()
                external_id = str(value.get("external_id") or "").strip()
                if not source_key or not external_id:
                    continue
                identity = (source_key, external_id)
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append({
                    "source_key": source_key,
                    "source_name": str(value.get("source_name") or "").strip(),
                    "external_id": external_id,
                    "url": str(value.get("url") or "").strip() or None,
                    "disclosure_date": str(value.get("disclosure_date") or "").strip() or None,
                })
        return merged

    @classmethod
    def _announcement_provenance_entry(cls, value: Any) -> Dict[str, Any]:
        getter = value.get if isinstance(value, dict) else lambda name, default=None: getattr(value, name, default)
        return {
            "source_key": getter("source_key", ""),
            "source_name": getter("source_name", ""),
            "external_id": getter("external_id", ""),
            "url": getter("url", ""),
            "disclosure_date": cls._announcement_disclosure_date(
                getter("metrics_json", "{}"), getter("event_at"),
            ),
        }

    @classmethod
    def _announcement_source_aliases(cls, value: Any) -> List[Dict[str, Any]]:
        getter = value.get if isinstance(value, dict) else lambda name, default=None: getattr(value, name, default)
        metrics = getter("metrics_json", "{}")
        metrics = _load(metrics, {}) if isinstance(metrics, str) else metrics
        provenance = metrics.get("_source_provenance") if isinstance(metrics, dict) else None
        return cls._merged_announcement_provenance(
            provenance, cls._announcement_provenance_entry(value),
        )

    @classmethod
    def _announcement_content_identity(cls, value: Any) -> Optional[Tuple[str, str, str]]:
        getter = value.get if isinstance(value, dict) else lambda name, default=None: getattr(value, name, default)
        symbol = cls._single_announcement_symbol(getter("symbol_codes", ""))
        title = cls._normalized_announcement_title(
            getter("title", ""), getter("actors_json", "[]"),
        )
        disclosure_date = cls._announcement_disclosure_date(
            getter("metrics_json", "{}"), getter("event_at"),
        )
        if not symbol or not title or not disclosure_date:
            return None
        return symbol, disclosure_date, title

    @staticmethod
    def _has_shared_announcement_id_namespace(left: str, right: str) -> bool:
        """Only CNINFO and SZSE are proven to expose the same announcement id."""
        return {str(left), str(right)} == {"cninfo.announcements", "szse.announcements"}

    @staticmethod
    def _is_exchange_announcement(value: Any) -> bool:
        getter = value.get if isinstance(value, dict) else lambda name, default=None: getattr(value, name, default)
        return (
            getter("source_key", "") in _ANNOUNCEMENT_SOURCE_KEYS
            and getter("event_type", "") == "company_announcement"
        )

    @staticmethod
    def _single_announcement_symbol(value: Any) -> Optional[str]:
        symbols = [item for item in str(value or "").split(",") if item]
        return symbols[0] if len(symbols) == 1 else None

    @staticmethod
    def _normalized_announcement_title(value: Any, actors_json: Any = "[]") -> str:
        """公告标题归一化；实现与新闻去重共用 ``normalized_title_key``。"""
        return normalized_title_key(value, actors_json)

    @staticmethod
    def _announcement_disclosure_date(metrics_json: Any, event_at: Any) -> Optional[str]:
        metrics = _load(metrics_json, {}) if isinstance(metrics_json, str) else metrics_json
        disclosure = metrics.get("disclosure_date") if isinstance(metrics, dict) else None
        if disclosure:
            raw = str(disclosure).strip()
            for pattern in ("%Y-%m-%d", "%Y%m%d"):
                try:
                    return datetime.strptime(raw[:10] if pattern == "%Y-%m-%d" else raw[:8], pattern).date().isoformat()
                except ValueError:
                    continue
        if isinstance(event_at, date) and not isinstance(event_at, datetime):
            return event_at.isoformat()
        if not isinstance(event_at, datetime):
            try:
                event_at = datetime.fromisoformat(str(event_at).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
        if event_at.tzinfo is None:
            event_at = event_at.replace(tzinfo=timezone.utc)
        return event_at.astimezone(_SHANGHAI).date().isoformat()

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

    def delete_quote_snapshot_feed_events(self, scan_limit: int = 200000) -> int:
        """删除历史上被误当新闻入库的「行情快照」条目。

        RSS/NewsNow 上游里有雪球个股页这类订阅源，把个股行情当 feed 条目输出：
        标题是股票名、摘要是「2.99% SH」、URL 是个股页。它们在生成事件的入口
        已经被 `_is_quote_snapshot_item` 拦截，但存量数据仍会出现在快讯列表里，
        这里按同样的判据一次性清掉。只删没有正文的纯行情行，不影响真实新闻。

        注意：判据含 Python 侧逻辑（标题长度/标点），只能在读出后判断，所以先
        只读三列把命中的 id 收集完，再按 id 分批删除。不能边 offset 分页边删——
        删除会让后续 offset 错位，实测漏掉 7/362 条。
        """
        from src.services.investment_monitor_service import _is_quote_snapshot_item

        with self.db.get_session() as session:
            candidates = session.execute(
                select(
                    MonitoringEventRecord.id,
                    MonitoringEventRecord.title,
                    MonitoringEventRecord.summary,
                    MonitoringEventRecord.url,
                )
                .where(
                    MonitoringEventRecord.source_key == "feeds.intelligence",
                    MonitoringEventRecord.event_type == "news",
                )
                .limit(scan_limit)
            ).all()

        targets = [
            int(row[0]) for row in candidates
            if _is_quote_snapshot_item(row[1], row[2], row[3])
        ]
        if not targets:
            return 0

        removed = 0
        with self.db.get_session() as session:
            for start in range(0, len(targets), 500):
                chunk = targets[start:start + 500]
                rows = session.execute(
                    select(MonitoringEventRecord).where(MonitoringEventRecord.id.in_(chunk))
                ).scalars().all()
                for row in rows:
                    session.delete(row)
                    removed += 1
                session.commit()
        return removed

    def collect_rejected_monitoring_noise(self, *, sample_size: int = 10) -> Dict[str, Any]:
        """只读：收集用户明确不要的三类情报事件的 id 与样本，不删除任何数据。

        三类：
        * 东方财富股吧帖子（整源停采后，存量零信息量情绪贴也没有价值）；
        * 纯英文标题新闻（港交所 RSS 这类英文公告，判据见
          ``_is_english_only_title``）；
        * 同标题多源重复里被淘汰的那些（归一化标题相同 + 12 小时窗口，保留最
          权威来源，同权威取最早入库）。

        判据含 Python 侧逻辑（标题归一化、英文判定），所以先把命中的 id 全部
        收集完，再交给 ``delete_monitoring_events_by_ids`` 分批删除。不能边
        offset 分页边删——删除会让后续 offset 错位，实测漏删 7/362。
        """
        from src.services.investment_monitor_service import _is_english_only_title

        samples: Dict[str, List[Dict[str, Any]]] = {"delete": [], "keep": []}
        with self.db.get_session() as session:
            rejected_rows = session.execute(
                select(
                    MonitoringEventRecord.id,
                    MonitoringEventRecord.source_key,
                    MonitoringEventRecord.title,
                    MonitoringEventRecord.event_at,
                ).where(
                    MonitoringEventRecord.source_key.in_(_REJECTED_MONITORING_SOURCE_KEYS),
                ).order_by(MonitoringEventRecord.id)
            ).all()
            guba_ids = [int(row.id) for row in rejected_rows]

            groups: Dict[str, List[Tuple[int, str, Any, Any]]] = {}
            english_ids: List[int] = []
            news_rows = session.execute(
                select(
                    MonitoringEventRecord.id,
                    MonitoringEventRecord.source_key,
                    MonitoringEventRecord.title,
                    MonitoringEventRecord.actors_json,
                    MonitoringEventRecord.event_at,
                    MonitoringEventRecord.ingested_at,
                    MonitoringEventRecord.dedupe_key,
                ).where(MonitoringEventRecord.event_type.in_(_NEWS_EVENT_TYPES))
            ).yield_per(5000)
            for row in news_rows:
                if _is_english_only_title(row.title):
                    english_ids.append(int(row.id))
                    continue
                key = row.dedupe_key or news_dedupe_key(row.title, row.actors_json)
                if not key:
                    continue
                groups.setdefault(str(key), []).append((
                    int(row.id), str(row.source_key or ""), row.event_at, row.ingested_at,
                ))

        duplicate_ids: List[int] = []
        keeper_by_loser: Dict[int, int] = {}
        for group in groups.values():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda item: (item[2] or datetime.min, item[0]))
            index = 0
            while index < len(ordered):
                anchor = ordered[index][2]
                cluster = [ordered[index]]
                cursor = index + 1
                while cursor < len(ordered):
                    if news_dedupe_window_match(anchor, ordered[cursor][2]):
                        cluster.append(ordered[cursor])
                        cursor += 1
                    else:
                        break
                keeper = min(
                    cluster,
                    key=lambda item: news_dedupe_keeper_rank(item[1], item[3], item[0]),
                )
                for item in cluster:
                    event_id = int(item[0])
                    if event_id == int(keeper[0]):
                        continue
                    duplicate_ids.append(event_id)
                    keeper_by_loser[event_id] = int(keeper[0])
                index = cursor

        delete_ids = sorted(set(guba_ids) | set(english_ids) | set(duplicate_ids))
        per_category = max(1, int(sample_size) // 3)
        samples["delete"].extend(self._noise_samples(
            [int(row.id) for row in rejected_rows[:per_category]], "股吧帖子（停采源存量）",
        ))
        samples["delete"].extend(self._noise_samples(
            english_ids[:per_category], "纯英文标题",
        ))
        duplicate_samples = self._noise_samples(
            duplicate_ids[:max(1, int(sample_size) - per_category * 2)],
            "同标题多源重复（被淘汰）",
        )
        for item in duplicate_samples:
            item["kept_event_id"] = keeper_by_loser.get(item["id"])
        samples["delete"].extend(duplicate_samples)
        samples["keep"].extend(self._noise_samples(
            [item["kept_event_id"] for item in duplicate_samples if item["kept_event_id"]],
            "同标题重复组里保留的最权威/最早一条",
        ))
        samples["keep"].extend(self._kept_news_samples(
            set(delete_ids), max(0, int(sample_size) - len(samples["keep"])),
        ))
        return {
            "guba": len(guba_ids),
            "english": len(english_ids),
            "duplicate": len(duplicate_ids),
            "total": len(delete_ids),
            "guba_ids": guba_ids,
            "english_ids": english_ids,
            "duplicate_ids": duplicate_ids,
            "delete_ids": delete_ids,
            "samples": samples,
        }

    def _noise_samples(self, ids: Iterable[int], reason: str) -> List[Dict[str, Any]]:
        """给报告用的样本行：说清楚删的是哪一条、为什么删。"""
        targets = [int(value) for value in ids if value]
        if not targets:
            return []
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    MonitoringEventRecord.id,
                    MonitoringEventRecord.source_key,
                    MonitoringEventRecord.event_type,
                    MonitoringEventRecord.title,
                    MonitoringEventRecord.event_at,
                ).where(MonitoringEventRecord.id.in_(targets))
            ).all()
        by_id = {int(row.id): row for row in rows}
        samples: List[Dict[str, Any]] = []
        for event_id in targets:
            row = by_id.get(event_id)
            if row is None:
                continue
            samples.append({
                "id": int(row.id),
                "source_key": str(row.source_key or ""),
                "event_type": str(row.event_type or ""),
                "title": str(row.title or "")[:120],
                "event_at": str(row.event_at or ""),
                "reason": reason,
            })
        return samples

    def _kept_news_samples(self, delete_ids: set, limit: int) -> List[Dict[str, Any]]:
        """给报告用的「保留」样本：最近入库且不在删除集里的真实新闻。"""
        if limit <= 0:
            return []
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    MonitoringEventRecord.id,
                    MonitoringEventRecord.source_key,
                    MonitoringEventRecord.event_type,
                    MonitoringEventRecord.title,
                    MonitoringEventRecord.event_at,
                )
                .where(MonitoringEventRecord.event_type.in_(_NEWS_EVENT_TYPES))
                .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                .limit(max(limit * 4, 40))
            ).all()
        samples: List[Dict[str, Any]] = []
        for row in rows:
            if int(row.id) in delete_ids:
                continue
            samples.append({
                "id": int(row.id),
                "source_key": str(row.source_key or ""),
                "event_type": str(row.event_type or ""),
                "title": str(row.title or "")[:120],
                "event_at": str(row.event_at or ""),
                "reason": "保留：真实新闻",
            })
            if len(samples) >= limit:
                break
        return samples

    def delete_monitoring_events_by_ids(
        self, event_ids: Iterable[int], *, batch_size: int = 500,
    ) -> int:
        """按 id 分批删除事件；符号投影表由数据库触发器同步清理。"""
        targets = sorted({int(value) for value in event_ids if value})
        if not targets:
            return 0
        removed = 0
        with self.db.get_session() as session:
            for start in range(0, len(targets), max(1, int(batch_size))):
                chunk = targets[start:start + max(1, int(batch_size))]
                rows = session.execute(
                    select(MonitoringEventRecord).where(MonitoringEventRecord.id.in_(chunk))
                ).scalars().all()
                for row in rows:
                    session.delete(row)
                    removed += 1
                session.commit()
        return removed

    def delete_rejected_monitoring_noise(self, *, sample_size: int = 10) -> Dict[str, Any]:
        """按用户明确不要的三类判据清理存量事件，返回各类条数与样本。"""
        report = self.collect_rejected_monitoring_noise(sample_size=sample_size)
        removed = self.delete_monitoring_events_by_ids(report["delete_ids"])
        return {
            "guba": report["guba"],
            "english": report["english"],
            "duplicate": report["duplicate"],
            "total": report["total"],
            "removed": removed,
            "samples": report["samples"],
        }

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
        if symbol is not None:
            conditions.append(_event_symbol_condition(symbol))
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
                    _event_symbol_condition(symbol),
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
                _event_symbol_condition(symbol),
                MonitoringEventRecord.event_type != "realtime_quote",
            )
            if types:
                query = query.where(MonitoringEventRecord.event_type.in_(types))
            query = query.order_by(
                desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id),
            ).options(load_only(*_EVENT_LIST_COLUMNS, *(
                (MonitoringEventRecord.raw_payload,) if types and set(types) <= RESEARCH_EVENT_TYPES else ()
            )))
            if limit is not None:
                query = query.limit(max(1, int(limit)))
            rows = session.execute(query).scalars().all()
            return self._symbol_event_dicts(session, rows, symbol)

    def _symbol_event_dicts(
        self, session: Any, rows: Iterable[MonitoringEventRecord], symbol: str,
        stock_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = list(rows)
        events = [self._event_dict(row) for row in rows]
        research_rows = [row for row in rows if row.event_type in RESEARCH_EVENT_TYPES]
        originals = {row.id: row.__dict__["raw_payload"] for row in research_rows if "raw_payload" in row.__dict__}
        research_ids = [row.id for row in research_rows if row.id not in originals]
        # Read originals only for the bounded visible research slice, never for
        # all news/forum rows and never one lazy-load query per event.
        originals.update(dict(session.execute(select(
            MonitoringEventRecord.id, MonitoringEventRecord.raw_payload,
        ).where(MonitoringEventRecord.id.in_(research_ids))).all()) if research_ids else {})
        for item in events:
            if item["event_type"] in RESEARCH_EVENT_TYPES:
                item["stock_relation"] = research_stock_relation(
                    item, symbol, raw=originals.get(item["id"]), stock_name=stock_name,
                )
        return events

    def symbol_workspace_bundle(
        self, *, symbol: str, days: int, overview: bool = False, stock_name: Optional[str] = None,
    ) -> Dict[str, Any]:
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
            _event_symbol_condition(symbol),
            MonitoringEventRecord.event_type != "realtime_quote",
        )
        snapshot_types = {
            "market_open_snapshot", "market_snapshot", "fundamental_snapshot",
            "capital_chip_snapshot", "ownership_snapshot", "technical_factor",
            "market_theme_flow", "limit_pool", "company_profile",
        }
        if overview:
            snapshot_types -= {"ownership_snapshot", "market_theme_flow", "limit_pool"}
        research_types = {
            "research_report_pdf", "institution_forecast", "institution_survey",
            "broker_recommendation",
        }
        news_types = ("news", "long_news")
        enterprise_types = {
            "enterprise_registration",
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
            extra: Any = True,
        ) -> List[Dict[str, Any]]:
            query = select(MonitoringEventRecord).where(*conditions, extra)
            values = tuple(types or ())
            if values:
                query = query.where(MonitoringEventRecord.event_type.in_(values))
            rows = session.execute(
                query.order_by(
                    desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id),
                ).limit(max(1, int(limit))).options(load_only(*_EVENT_LIST_COLUMNS, *(
                    (MonitoringEventRecord.raw_payload,) if values and set(values) <= RESEARCH_EVENT_TYPES else ()
                )))
            ).scalars().all()
            return self._symbol_event_dicts(session, rows, symbol, stock_name)

        with self.db.get_session() as session:
            # The landing view is a bounded preview, not a census of the entire
            # evidence store. Exact counts remain available in the full view.
            grouped_rows = [] if overview else session.execute(select(
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
            # Each snapshot type represents one independent latest state.
            # A shared newest-200 cap lets daily market updates crowd out an
            # older but still valid quarterly financial/profile snapshot.
            # Financial state is the highest *reported* period, not the newest
            # announcement about any older quarter. Keep all other snapshot
            # types' event-time ordering and every event's original chronology.
            candidates = select(
                MonitoringEventRecord.id, MonitoringEventRecord.event_type,
                MonitoringEventRecord.event_at,
                case((MonitoringEventRecord.event_type == "fundamental_snapshot", MonitoringEventRecord.metrics_json),
                     else_="{}").label("metrics_json"),
            ).where(*conditions, MonitoringEventRecord.event_type.in_(snapshot_types)).cte("workspace_snapshots")
            safe_metrics = case((func.json_valid(candidates.c.metrics_json) == 1, candidates.c.metrics_json), else_="{}")
            sections = func.json_each(safe_metrics).table_valued("key", "value", "type")
            section = case((sections.c.type == "object", sections.c.value), else_="{}")
            period = _financial_calendar_date_expr(func.json_extract(section, "$.end_date"))
            statement_scope = case((sections.c.key.in_(("indicator", "forecast")), 0),
                                   else_=_financial_statement_scope_expr(section))
            announcement = func.coalesce(
                func.nullif(_financial_calendar_date_expr(func.json_extract(section, "$.f_ann_date")), ""),
                _financial_calendar_date_expr(func.json_extract(section, "$.ann_date")),
            )
            # Match the utility's declared policy: only explicit integer/text
            # 1 is a latest marker; arbitrary truthy aliases are not evidence.
            updated = case((and_(
                func.json_type(section, "$.update_flag").in_(("integer", "text")),
                func.trim(func.json_extract(section, "$.update_flag"), FINANCIAL_WHITESPACE) == "1",
            ), 1), else_=0)
            financial_rows = select(
                candidates.c.id, sections.c.key.label("section_key"), section.label("section"),
                period.label("period"), statement_scope.label("statement_scope"),
                announcement.label("announcement"), updated.label("updated"),
            ).select_from(candidates.join(sections, true())).where(
                candidates.c.event_type == "fundamental_snapshot",
                sections.c.key.in_(FINANCIAL_SECTIONS),
                sections.c.type == "object", period != "", statement_scope >= 0,
            ).cte("workspace_financial_rows")
            # Forecast end_date, top-level period and external_id are not a
            # disclosed statement period. Rank actual source rows only; choose
            # revisions within the snapshot's highest valid period, not a newer
            # announcement about one of its older tables.
            financial_periods = select(
                financial_rows.c.id, func.max(financial_rows.c.period).label("period"),
            ).where(financial_rows.c.section_key != "forecast").group_by(
                financial_rows.c.id,
            ).cte("workspace_financial_periods")
            # Scope preference must not hide a newer indicator revision beside
            # an unchanged typed statement. Compare scope and disclosure time
            # independently across compatible sections in this highest period.
            financial_order = select(
                financial_rows.c.id, financial_periods.c.period,
                func.max(financial_rows.c.statement_scope).label("statement_scope"),
                func.max(financial_rows.c.announcement).label("announcement"),
                func.max(financial_rows.c.updated).label("updated"),
            ).join(financial_periods, and_(
                financial_rows.c.id == financial_periods.c.id,
                financial_rows.c.period == financial_periods.c.period,
            )).where(financial_rows.c.section_key != "forecast").group_by(
                financial_rows.c.id, financial_periods.c.period,
            ).subquery()
            ranked_snapshots = select(
                candidates.c.id,
                func.row_number().over(
                    partition_by=candidates.c.event_type,
                    order_by=(
                        desc(func.coalesce(financial_order.c.period, "")),
                        desc(func.coalesce(financial_order.c.statement_scope, 0)),
                        desc(func.coalesce(financial_order.c.updated, 0)),
                        desc(func.coalesce(financial_order.c.announcement, "")),
                        desc(candidates.c.event_at), desc(candidates.c.id),
                    ),
                ).label("position"),
            ).select_from(candidates.outerjoin(financial_order, candidates.c.id == financial_order.c.id)).subquery()
            # Select each table independently using only disclosed revision
            # evidence. Completeness, arrival time and content lexical order
            # must never turn an unresolved value conflict into a "newer" fact.
            ranked_sections = select(
                financial_rows,
                func.dense_rank().over(partition_by=financial_rows.c.section_key, order_by=(
                    desc(financial_rows.c.period), desc(financial_rows.c.statement_scope),
                    desc(financial_rows.c.updated), desc(financial_rows.c.announcement),
                )).label("priority"),
            ).cte("workspace_ranked_sections")
            top_sections = select(ranked_sections).where(ranked_sections.c.priority == 1).cte("workspace_top_sections")
            # Canonicalize JSON in SQL only for the top revision tier. Sorted
            # tree paths preserve nested objects, arrays, null and scalar types
            # while ignoring object insertion order in old source records.
            tree = func.json_tree(top_sections.c.section).table_valued("fullkey", "type", "atom")
            canonical_atom = case(
                # json_array otherwise rounds REALs to fewer than 17 digits,
                # incorrectly merging distinct saved binary64 source values.
                (tree.c.type == "real", func.printf("%!.17g", tree.c.atom)),
                # Preserve integers beyond SQLite's signed 64-bit extraction
                # range rather than rounding them through a REAL atom.
                (tree.c.type == "integer", top_sections.c.section.op("->")(tree.c.fullkey)),
                else_=tree.c.atom,
            )
            ordered_atoms = select(func.json_array(tree.c.fullkey, tree.c.type, canonical_atom).label("atom")).select_from(
                tree,
            ).order_by(tree.c.fullkey).correlate(top_sections).subquery()
            canonical = select(func.json_group_array(ordered_atoms.c.atom)).scalar_subquery()
            canonical_sections = select(
                top_sections.c.id, top_sections.c.section_key, top_sections.c.period,
                top_sections.c.announcement, canonical.label("content_key"),
            ).cte("workspace_canonical_sections")
            distinct_sections = select(
                canonical_sections.c.section_key, canonical_sections.c.period, canonical_sections.c.announcement,
                canonical_sections.c.content_key, func.min(canonical_sections.c.id).label("id"),
            ).group_by(
                canonical_sections.c.section_key, canonical_sections.c.period,
                canonical_sections.c.announcement, canonical_sections.c.content_key,
            ).cte("workspace_distinct_sections")
            bounded_sections = select(
                distinct_sections,
                func.count().over(partition_by=distinct_sections.c.section_key).label("candidate_count"),
                func.row_number().over(partition_by=distinct_sections.c.section_key,
                                       order_by=distinct_sections.c.content_key).label("candidate_position"),
            ).cte("workspace_bounded_sections")
            # Distinct-content counts precede the five-candidate output cap;
            # duplicate copies can never crowd out evidence of a conflict.
            section_choices = select(
                case((func.max(bounded_sections.c.candidate_count) == 1, func.min(bounded_sections.c.id)),
                     else_=None).label("event_id"),
                bounded_sections.c.section_key,
                func.max(bounded_sections.c.candidate_count).label("candidate_count"),
                func.max(bounded_sections.c.period).label("period"),
                func.max(bounded_sections.c.announcement).label("announcement"),
                func.json_group_array(bounded_sections.c.id).label("candidate_ids"),
            ).where(bounded_sections.c.candidate_position <= 5).group_by(bounded_sections.c.section_key)
            choices = union_all(select(
                ranked_snapshots.c.id.label("event_id"), literal("").label("section_key"),
                literal(0).label("candidate_count"), literal("").label("period"),
                literal("").label("announcement"), literal("[]").label("candidate_ids"),
            ).where(ranked_snapshots.c.position == 1), section_choices).cte("workspace_choices")
            selected_rows = session.execute(select(
                MonitoringEventRecord, choices.c.section_key, choices.c.candidate_count,
                choices.c.period, choices.c.announcement, choices.c.candidate_ids,
            ).select_from(choices.outerjoin(MonitoringEventRecord, MonitoringEventRecord.id == choices.c.event_id)).order_by(
                desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id),
            ).options(load_only(*_EVENT_LIST_COLUMNS))).all()
            snapshots = []
            financial_sections = {}
            for row, key, count, report_period, announcement_date, candidate_ids in selected_rows:
                if not key:
                    snapshots.append(self._event_dict(row))
                    continue
                source_event = self._event_dict(row) if row is not None else None
                financial_sections[key] = {
                    "selection_status": "selected" if count == 1 else "conflict",
                    "row": dict(source_event["metrics"][key]) if source_event else {},
                    "event": source_event, "period": report_period, "announcement": announcement_date,
                    "candidate_count": int(count), "candidate_event_ids": _load(candidate_ids, []),
                }
            if overview:
                # Apply source-backed attribution in SQL before LIMIT. Filtering
                # the newest three wide-recall PDFs hides older company reports.
                research = fetch(session, research_types, 3, direct_research_condition(MonitoringEventRecord, symbol))
                if len(research) < 3:
                    research += fetch(session, research_types, 3 - len(research),
                                      MonitoringEventRecord.id.not_in([item["id"] for item in research]))
            else:
                research = fetch(session, research_types, 200)
                # Keep the same direct evidence visible after moving from the
                # overview to the full workspace. A newer related-report flood
                # must not make its three company reports disappear there.
                direct = fetch(session, research_types, 3, direct_research_condition(MonitoringEventRecord, symbol))
                research = sorted({item["id"]: item for item in [*research, *direct]}.values(),
                                  key=lambda item: (str(item.get("event_at") or ""), int(item["id"])), reverse=True)
            announcements = fetch(session, ("company_announcement",), 3 if overview else 20)
            essays = fetch(session, ("essay",), 3 if overview else 30)
            stock_comments = [] if overview else fetch(session, ("stock_forum_post",), 20)
            news = fetch(session, news_types, 3 if overview else 20)
            if overview:
                messages = news
            else:
                # Independent channel budgets keep the overview's news visible
                # even when newer opinions or enterprise alerts dominate. Do not
                # lend unused slots between channels; the service's 40-row cap
                # must never trim one channel after this merge. Essays already
                # have a newest-first bounded read for their dedicated section.
                enterprise = fetch(session, enterprise_types, 10)
                messages = sorted(
                    {item["id"]: item for item in [*news, *essays[:10], *enterprise]}.values(),
                    key=lambda item: (str(item.get("event_at") or ""), int(item["id"])), reverse=True,
                )
            timeline = sorted(
                [*announcements, *research, *essays, *messages],
                key=lambda row: (str(row.get("event_at") or ""), int(row["id"])), reverse=True,
            ) if overview else fetch(session, None, 40)

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
            "financial_sections": financial_sections,
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
            "counts_complete": not overview,
        }

    def consensus_note_candidates(
        self, *, symbol: str, cutoff: datetime,
        before: Optional[Tuple[datetime, int]] = None, limit: int = 128,
    ) -> List[Dict[str, Any]]:
        """Page narrow note metadata, not essay bodies, for 20+5 sampling.

        A keyset cursor allows callers to find older single-stock notes without
        either a fixed candidate cap or hydrating all matching historical text.
        Joining notes here skips orphaned event projections before pagination.
        """
        query = select(
            MonitoringEventRecord.id.label("event_id"),
            MonitoringEventRecord.external_id.label("topic_id"),
            MonitoringEventRecord.event_at,
            ResearchNote.symbol_codes,
        ).join(
            ResearchNote, ResearchNote.topic_id == MonitoringEventRecord.external_id,
        ).where(
            MonitoringEventRecord.event_at >= cutoff,
            MonitoringEventRecord.event_type == "essay",
            _event_symbol_condition(symbol),
        )
        if before is not None:
            query = query.where(tuple_(
                MonitoringEventRecord.event_at, MonitoringEventRecord.id,
            ) < before)
        with self.db.get_session() as session:
            rows = session.execute(query.order_by(
                desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id),
            ).limit(max(1, min(int(limit), 500)))).mappings().all()
        return [dict(row) for row in rows]

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
        if symbol is not None:
            conditions.append(_event_symbol_condition(symbol))
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

    def recent_news_events(self, *, days: int = 7, limit: int = 320) -> List[Dict[str, Any]]:
        """首页新闻栏的候选池。

        只取 news / long_news，带 limit 和精简列——首页每次刷新都调这个方法，
        拉全类型 600 行会把仪表盘拖到几十秒。
        """
        cutoff = utc_naive_now() - timedelta(days=max(1, int(days)))
        with self.db.get_session() as session:
            rows = session.execute(
                select(MonitoringEventRecord)
                .where(
                    MonitoringEventRecord.event_type.in_(("news", "long_news")),
                    MonitoringEventRecord.event_at >= cutoff,
                )
                .order_by(desc(MonitoringEventRecord.event_at), desc(MonitoringEventRecord.id))
                .limit(max(1, int(limit)))
                .options(load_only(*_EVENT_LIST_COLUMNS))
            ).scalars().all()
        return [self._event_dict(row) for row in rows]

    def get_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.get(MonitoringEventRecord, int(event_id), options=(load_only(*_EVENT_LIST_COLUMNS),))
            return self._event_dict(row) if row is not None else None

    def get_event_raw_url(self, event_id: int) -> Optional[str]:
        """取出入库 payload 里的发布方链接。

        ``long_news`` 这类来源只用 ``raw_payload`` 保存原文链接，``url`` 列是空的；
        只在这一条事件的 url 列与 metrics 都没有链接时才做一次窄查询，
        不把整个 payload 带进常规读取路径。
        """
        with self.db.get_session() as session:
            raw = session.execute(
                select(MonitoringEventRecord.raw_payload).where(MonitoringEventRecord.id == int(event_id))
            ).scalar()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        value = str(payload.get("url") or "").strip()
        return value or None

    def get_event_content_cache(self, event_id: int) -> Optional[Dict[str, Any]]:
        """读取按需正文缓存。

        返回原始 datetime（naive UTC），冷却窗口判断由服务层负责，避免把
        「多久算太频繁」这种策略写进仓储层。
        """
        with self.db.get_session() as session:
            row = session.get(MonitoringEventContentRecord, int(event_id))
            return self._event_content_dict(row) if row is not None else None

    def save_event_content_cache(
        self, event_id: int, *, status: str, content: Optional[str] = None,
        error: Optional[str] = None, source_url: Optional[str] = None,
        fetched_at: Optional[datetime] = None, attempted_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """写入/更新单条正文缓存，返回落库后的快照。

        并发点开同一条快讯时两个请求可能同时插入，主键冲突退化为更新，
        不能让一次重复点击变成接口错误。
        """
        identifier = int(event_id)
        now = utc_naive_now()
        values = {
            "status": str(status or "")[:20] or "failed",
            "content": content or None,
            "error": str(error)[:200] if error else None,
            "source_url": str(source_url)[:1000] if source_url else None,
            "fetched_at": fetched_at if fetched_at is not None else (
                now if str(status or "") == "ok" else None
            ),
            "attempted_at": attempted_at or now,
        }
        with self.db.get_session() as session:
            row = session.get(MonitoringEventContentRecord, identifier)
            if row is None:
                row = MonitoringEventContentRecord(event_id=identifier, **values)
                session.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                row = session.get(MonitoringEventContentRecord, identifier)
                if row is None:
                    raise
                for key, value in values.items():
                    setattr(row, key, value)
                session.commit()
            stored = self._event_content_dict(row)
        return stored

    @staticmethod
    def _event_content_dict(row: MonitoringEventContentRecord) -> Dict[str, Any]:
        return {
            "event_id": int(row.event_id),
            "status": str(row.status or ""),
            "content": row.content,
            "error": row.error,
            "source_url": row.source_url,
            "fetched_at": row.fetched_at,
            "attempted_at": row.attempted_at,
        }

    def list_announcements(
        self, *, start_at: datetime, end_at: datetime, symbol: Optional[str] = None,
        category: Optional[str] = None, query: Optional[str] = None,
        page: int = 1, page_size: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = [
            MonitoringEventRecord.source_key.in_(_ANNOUNCEMENT_SOURCE_KEYS),
            MonitoringEventRecord.event_type == "company_announcement",
            MonitoringEventRecord.event_at >= start_at,
            MonitoringEventRecord.event_at <= end_at,
        ]
        if symbol is not None:
            conditions.append(_event_symbol_condition(symbol))
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
            # 只有新闻类事件参与「同标题多源重复」判重，其余类型保持 NULL，
            # 由 (dedupe_key, event_at) 索引在写入路径做等值探测。
            "dedupe_key": (
                news_dedupe_key(fields.get("title"), fields.get("actors") or [])
                if is_news_event_type(fields.get("event_type")) else None
            ),
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
            "sentiment": "neutral" if row.event_type == _CAPITAL_SNAPSHOT_TYPE else row.sentiment,
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
