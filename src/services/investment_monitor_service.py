# -*- coding: utf-8 -*-
"""Unified monitoring across quotes, Tushare, ZSXQ essays, feeds and external APIs."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd
from sqlalchemy import desc, or_, select

from src.config import get_config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.financial_data_service import TushareGatewayService
from src.services.market_data_service import MarketDataService
from src.services.cninfo_announcement_service import CninfoAnnouncementError, CninfoAnnouncementService
from src.services.announcement_artifact_service import AnnouncementArtifactError, AnnouncementArtifactService
from src.services.eastmoney_guba_service import EastmoneyGubaError, EastmoneyGubaService
from src.services.essay_consensus_service import EssayConsensusError, EssayConsensusService, EssayConsensusWorker
from src.services.stock_service import StockService
from src.services.intelligence_service import IntelligenceService
from src.storage import (
    DatabaseManager,
    IntelligenceItem,
    MonitoringEventRecord,
    ResearchNote,
    EssayAnalysisRecord,
    StockDaily,
    utc_naive_now,
)

logger = logging.getLogger(__name__)

_STOCK_WORKSPACE_CACHE_TTL_SECONDS = 20
_stock_workspace_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
_stock_workspace_cache_lock = threading.Lock()

PERSPECTIVES = ("investor", "company", "institution")
SENTIMENTS = ("bullish", "bearish", "neutral", "mixed")
_SOURCE_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,99}$")
_A_SHARE_RE = re.compile(r"(?<!\d)(\d{6})(?:\.(SH|SS|SZ|BJ))?(?!\d)", re.I)
_POSITIVE_WORDS = ("增长", "超预期", "上调", "买入", "增持", "中标", "突破", "回购", "盈利", "提价")
_NEGATIVE_WORDS = ("下降", "低于预期", "下调", "减持", "亏损", "风险", "处罚", "终止", "暴跌", "问询")
_COMPANY_WORDS = ("公告", "公司", "董事会", "股东", "回购", "减持", "业绩预告", "半年报", "年报")
_EVENT_ORIGIN_APIS: Dict[str, Sequence[str]] = {
    "realtime_quote": ("realtime_quote",), "market_open_snapshot": ("realtime_quote",), "market_snapshot": ("daily", "daily_basic"),
    "news": ("news",), "institution_forecast": ("report_rc",), "institution_survey": ("stk_surv",),
    "dragon_tiger": ("top_list",), "institution_seat": ("top_inst",), "price_limit": ("limit_list_d",),
    "broker_recommendation": ("broker_recommend",), "block_trade": ("block_trade",), "suspension": ("suspend_d",),
    "market_theme_flow": ("moneyflow_cnt_ths", "moneyflow_ind_ths"),
    "limit_pool": ("limit_list_ths",),
    "earnings_forecast": ("forecast",), "earnings_express": ("express",), "repurchase": ("repurchase",),
    "holder_trade": ("stk_holdertrade",), "share_unlock": ("share_float",), "company_profile": ("stock_company",),
    "holder_number": ("stk_holdernumber",), "top_shareholders": ("top10_holders",),
    "top_float_shareholders": ("top10_floatholders",), "dividend": ("dividend",),
    "executive_rewards": ("stk_rewards",), "executive_roster": ("stk_managers",),
    "audit_opinion": ("fina_audit",),
    "fundamental_snapshot": ("fina_indicator", "income", "balancesheet", "cashflow", "forecast"),
    "capital_chip_snapshot": ("cyq_perf", "cyq_chips", "moneyflow", "margin_detail", "margin", "hk_hold"),
    "technical_factor": ("stk_factor", "stk_nineturn"), "ownership_snapshot": ("pledge_stat", "share_float", "stk_holdertrade", "repurchase"),
    "research_report_pdf": ("research_report",), "long_news": ("major_news", "cctv_news"),
    "company_announcement": ("cninfo_announcement",), "enterprise_registration": ("registration-info",),
    "enterprise_risk": ("risk-overview",), "enterprise_credit": ("credit-evaluation",),
    "enterprise_ipr": ("ipr-score",), "enterprise_history": ("historical-overview",), "essay": ("zsxq_mcp",),
    "stock_comment_snapshot": ("akshare.stock_comment_em",),
    "stock_forum_post": ("mguba_public_list",),
}

def _source(source_key: str, name: str, adapter_type: str, provider: str, category: str,
            cadence: int, *, level: str = "licensed", apis: Sequence[str] = (),
            config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if cadence <= 60:
        refresh_mode = "near_realtime"
    elif cadence <= 3600:
        refresh_mode = "intraday"
    else:
        refresh_mode = "scheduled"
    evidence = {
        "evidence_level": level,
        "origin_apis": list(apis),
        "refresh_mode": refresh_mode,
        "target_refresh_seconds": cadence,
    }
    return {"source_key": source_key, "name": name, "adapter_type": adapter_type,
            "provider": provider, "category": category, "poll_interval_seconds": cadence,
            "config": {**evidence, **(config or {})}}


BUILTIN_MONITORING_SOURCES = [
    _source("realtime.quotes", "自选股开盘快照", "realtime", "multi-provider", "market", 60,
            apis=("realtime_quote",)),
    _source("tushare.market", "Tushare 收盘行情与估值", "tushare", "tushare", "market", 60,
            apis=("daily", "daily_basic")),
    *[
        _source(f"tushare.news.{key}", f"Tushare {label}快讯", "tushare", key, "news", 15,
                level="reported", apis=("news",), config={"src": key})
        for key, label in (
            ("cls", "财联社"), ("sina", "新浪财经"), ("wallstreetcn", "华尔街见闻"),
            ("10jqka", "同花顺"), ("eastmoney", "东方财富"), ("yicai", "第一财经"),
        )
    ],
    _source("tushare.institution", "机构预测与调研", "tushare", "tushare", "institution", 120,
            apis=("report_rc", "stk_surv")),
    _source("tushare.market_activity", "席位、异动与券商金股", "tushare", "tushare", "capital", 120,
            apis=("top_list", "top_inst", "block_trade", "broker_recommend", "limit_list_d", "suspend_d")),
    _source("tushare.market_themes", "市场题材资金与涨跌停结构", "tushare", "tushare", "market", 60,
            apis=("moneyflow_cnt_ths", "moneyflow_ind_ths", "limit_list_ths")),
    _source("tushare.company", "上市公司事项", "tushare", "tushare", "company", 120,
            apis=("forecast", "express", "repurchase", "stk_holdertrade", "share_float")),
    _source("tushare.company_profile", "公司治理与股东事实", "tushare", "tushare", "governance", 600,
            apis=("stock_company", "stk_managers", "stk_holdernumber", "top10_holders", "top10_floatholders", "dividend", "stk_rewards", "fina_audit")),
    _source("tushare.fundamentals", "财务质量与业绩", "tushare", "tushare", "fundamental", 300,
            apis=("fina_indicator", "income", "balancesheet", "cashflow", "forecast")),
    _source("tushare.capital", "筹码、资金与北向持股", "tushare", "tushare", "capital", 120,
            apis=("cyq_perf", "cyq_chips", "moneyflow", "margin_detail", "margin", "hk_hold")),
    _source("tushare.technical", "技术因子与神奇九转", "tushare", "tushare", "technical", 60,
            apis=("stk_factor", "stk_nineturn")),
    _source("tushare.ownership", "股权与资本动作", "tushare", "tushare", "ownership", 300,
            apis=("pledge_stat", "share_float", "stk_holdertrade", "repurchase")),
    _source("tushare.research_pdf", "完整券商研报 PDF", "tushare", "tushare", "research", 300,
            level="reported", apis=("research_report",)),
    _source("tushare.long_news", "长篇新闻与新闻联播", "tushare", "tushare", "news", 120,
            level="reported", apis=("major_news", "cctv_news")),
    _source("cninfo.announcements", "巨潮资讯上市公司公告", "cninfo", "cninfo", "company", 60,
            level="official", apis=("cninfo_announcement",)),
    _source("tianyancha.enterprise", "天眼查企业事实与风险", "cli", "tianyancha", "enterprise", 600,
            apis=("registration-info", "risk-overview", "credit-evaluation", "ipr-score", "historical-overview")),
    _source("zsxq.essays", "知识星球小作文（待核验）", "mcp", "zsxq", "essay", 10,
            level="unverified", apis=("zsxq_mcp",)),
    _source("akshare.stock_comments", "东方财富千股千评指标", "akshare", "eastmoney", "comment", 300,
            level="reported", apis=("stock_comment_em",)),
    _source("eastmoney.guba_posts", "东方财富股吧公开股评", "html", "eastmoney_guba", "comment", 30,
            level="unverified", apis=("mguba_public_list",)),
    _source("feeds.intelligence", "RSS / NewsNow 媒体流", "feed", "configurable", "news", 15,
            level="reported", apis=("rss", "atom", "newsnow")),
]


class InvestmentMonitorError(ValueError):
    """Safe validation or upstream error for monitoring APIs."""


class MonitoringSourceNotConfigured(InvestmentMonitorError):
    """The adapter exists, but no upstream source has been enabled yet."""


class InvestmentMonitorService:
    """Normalize heterogeneous evidence and build watchlist-centered views."""

    def __init__(
        self,
        repository: Optional[InvestmentMonitorRepository] = None,
        tushare: Optional[TushareGatewayService] = None,
        stock_service: Optional[StockService] = None,
        cninfo: Optional[CninfoAnnouncementService] = None,
        announcement_artifacts: Optional[AnnouncementArtifactService] = None,
        guba: Optional[EastmoneyGubaService] = None,
    ):
        self.repo = repository or InvestmentMonitorRepository()
        self.tushare = tushare or TushareGatewayService()
        self.stock_service = stock_service or StockService()
        self.cninfo = cninfo or CninfoAnnouncementService()
        self.announcement_artifacts = announcement_artifacts or AnnouncementArtifactService()
        self.guba = guba or EastmoneyGubaService()
        self.repo.ensure_sources(BUILTIN_MONITORING_SOURCES)
        self.repo.backfill_zsxq_topic_urls()
        self._name_cache: Dict[str, str] = {}
        self._watchlist_override: Optional[List[str]] = None
        self._backfill_days: Optional[int] = None

    def list_sources(self) -> Dict[str, Any]:
        items = self.repo.list_sources()
        freshness = self.repo.source_event_freshness()
        now = utc_naive_now()
        for item in items:
            observed = freshness.get(item["source_key"], {})
            item.update(observed)
            cadence = max(10, int(item.get("poll_interval_seconds") or 300))
            last_check_raw = item.get("last_success_at")
            last_check_at = (
                datetime.fromisoformat(str(last_check_raw).replace("Z", "+00:00")).replace(tzinfo=None)
                if last_check_raw else None
            )
            check_age = max(0, int((now - last_check_at).total_seconds())) if last_check_at else None
            monitoring_sla = max(30, cadence * 2 + 10)
            item["last_check_at"] = last_check_raw
            item["last_check_age_seconds"] = check_age
            item["monitoring_sla_seconds"] = monitoring_sla
            item["next_check_at"] = (
                (last_check_at + timedelta(seconds=cadence)).isoformat() + "Z"
                if last_check_at else None
            )
            if item["last_status"] == "not_configured":
                item["monitoring_status"] = "not_configured"
            elif item["last_status"] == "failed":
                item["monitoring_status"] = "failed"
            elif check_age is None:
                item["monitoring_status"] = "pending"
            else:
                item["monitoring_status"] = "live" if check_age <= monitoring_sla else "delayed"
            item["last_run_state"] = (
                "failed" if item["last_status"] == "failed"
                else "not_configured" if item["last_status"] == "not_configured"
                else "received" if int(item.get("last_received_count") or 0) > 0
                else "empty"
            )
            latest_raw = observed.get("latest_event_at")
            if not latest_raw:
                item["freshness_status"] = "empty"
                item["data_age_seconds"] = None
                item["freshness_sla_seconds"] = self._freshness_sla_seconds(item)
                item["data_state"] = "not_configured" if item["last_status"] == "not_configured" else "empty"
                item["upstream_state"] = "no_data"
                continue
            latest_at = datetime.fromisoformat(str(latest_raw).replace("Z", "+00:00")).replace(tzinfo=None)
            age_seconds = max(0, int((now - latest_at).total_seconds()))
            # This describes the age of the newest fact, not whether the API call
            # succeeded. Three polling intervals with a one-day floor tolerates
            # quiet periods while still exposing genuinely old or empty channels.
            threshold = self._freshness_sla_seconds(item)
            item["data_age_seconds"] = age_seconds
            item["freshness_status"] = "fresh" if age_seconds <= threshold else "stale"
            item["data_state"] = item["freshness_status"]
            item["freshness_sla_seconds"] = threshold
            item["upstream_state"] = (
                "current" if item["freshness_status"] == "fresh"
                else "quiet" if item["monitoring_status"] == "live"
                else "stale"
            )
        return {
            "items": items,
            "total": len(items),
            "healthy": sum(1 for item in items if item["last_status"] == "success" and item["freshness_status"] != "empty"),
            "operational": sum(1 for item in items if item["last_status"] == "success"),
            "not_configured": sum(1 for item in items if item["last_status"] == "not_configured"),
            "enabled": sum(1 for item in items if item["enabled"]),
            "with_data": sum(1 for item in items if item["freshness_status"] != "empty"),
            "fresh": sum(1 for item in items if item["freshness_status"] == "fresh"),
            "stale": sum(1 for item in items if item["freshness_status"] == "stale"),
            "empty": sum(1 for item in items if item["freshness_status"] == "empty"),
            "monitoring_live": sum(1 for item in items if item["monitoring_status"] == "live"),
            "monitoring_delayed": sum(1 for item in items if item["monitoring_status"] in {"delayed", "failed"}),
        }

    @staticmethod
    def _freshness_sla_seconds(source: Dict[str, Any]) -> int:
        """Describe fact freshness by data domain instead of one universal one-day floor."""
        category = str(source.get("category") or "")
        cadence = max(30, int(source.get("poll_interval_seconds") or 300))
        if category in {"news", "essay", "comment"}:
            return max(900, cadence * 5)
        if source.get("source_key") == "cninfo.announcements":
            return max(172800, cadence * 5)
        if category in {"market", "technical", "capital"}:
            return max(172800, cadence * 5)
        if category in {"research", "company", "institution", "ownership"}:
            return max(259200, cadence * 5)
        return max(604800, cadence * 5)

    def source_bi(self, *, days: int = 30) -> Dict[str, Any]:
        """Source-backed BI: inventory, freshness, activity and direct-use contracts."""
        source_summary = self.list_sources()
        sources = source_summary["items"]
        activity = self.repo.source_activity(days=days)
        dates = [
            (utc_naive_now().date() - timedelta(days=offset)).isoformat()
            for offset in range(max(1, days) - 1, -1, -1)
        ]
        activity_by_key_date = {
            (row["source_key"], row["date"]): int(row["count"])
            for row in activity
        }
        category_totals: Counter[str] = Counter()
        provider_totals: Counter[str] = Counter()
        daily_totals: Counter[str] = Counter()
        for source in sources:
            count = int(source.get("stored_event_count") or 0)
            category_totals[str(source.get("category") or "other")] += count
            provider_totals[str(source.get("provider") or "other")] += count
        for row in activity:
            daily_totals[row["date"]] += int(row["count"])

        source_rows = []
        for source in sources:
            key = source["source_key"]
            source_rows.append({
                **source,
                "period_event_count": sum(activity_by_key_date.get((key, date), 0) for date in dates),
                "daily_activity": [{"date": date, "count": activity_by_key_date.get((key, date), 0)} for date in dates],
                "direct_use": {
                    "events_api": f"/api/v1/investment-monitor/events?source_key={key}",
                    "sync_api": f"/api/v1/investment-monitor/sources/{key}/sync",
                    "origin_apis": list((source.get("config") or {}).get("origin_apis") or []),
                    "local_store": "SQLite.monitoring_events",
                },
            })
        return {
            "days": days,
            "generated_at": utc_naive_now().isoformat() + "Z",
            "summary": {
                **{key: value for key, value in source_summary.items() if key != "items"},
                "stored_event_count": sum(int(item.get("stored_event_count") or 0) for item in sources),
                "period_event_count": sum(daily_totals.values()),
                "last_run_received": sum(int(item.get("last_received_count") or 0) for item in sources),
                "last_run_created": sum(int(item.get("last_created_count") or 0) for item in sources),
            },
            "daily_trend": [{"date": date, "count": daily_totals[date]} for date in dates],
            "categories": [{"name": key, "count": value} for key, value in category_totals.most_common()],
            "providers": [{"name": key, "count": value} for key, value in provider_totals.most_common()],
            "sources": source_rows,
        }

    def compact_event(self, event: Dict[str, Any], *, summary_limit: int = 600) -> Dict[str, Any]:
        """Keep the evidence contract while omitting repeated structured snapshots."""
        compact = {
            key: event.get(key) for key in (
                "id", "source_key", "source_name", "source_type", "external_id", "event_type",
                "perspective", "title", "url", "symbols", "sentiment", "importance_score",
                "confidence_score", "tags", "actors", "event_at", "ingested_at",
            )
        }
        compact["summary"] = str(event.get("summary") or "")[:max(0, summary_limit)] or None
        metrics = {"_evidence": self._event_evidence(event)}
        if event.get("event_type") == "stock_forum_post":
            raw_metrics = dict(event.get("metrics") or {})
            metrics.update({
                key: raw_metrics.get(key)
                for key in ("post_id", "author", "views", "reply_count", "like_count", "image_urls", "time_text")
            })
        compact["metrics"] = metrics
        return compact

    def create_external_source(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        key = str(payload.get("source_key") or "").strip().lower()
        if not _SOURCE_KEY_RE.fullmatch(key):
            raise InvestmentMonitorError("source_key must match [a-z][a-z0-9_.-]{2,99}")
        if self.repo.get_source(key):
            raise InvestmentMonitorError(f"monitoring source already exists: {key}")
        config = payload.get("config") or {}
        if not isinstance(config, dict):
            raise InvestmentMonitorError("config must be an object")
        forbidden = {"token", "api_key", "apikey", "password", "secret", "authorization"}
        if any(str(name).lower() in forbidden for name in config):
            raise InvestmentMonitorError("credentials must be provided through environment variables, not source config")
        return self.repo.create_source({
            "source_key": key,
            "name": str(payload.get("name") or key).strip()[:120],
            "adapter_type": str(payload.get("adapter_type") or "api").strip()[:32],
            "provider": str(payload.get("provider") or "external").strip()[:64],
            "category": str(payload.get("category") or "news").strip()[:32],
            "enabled": bool(payload.get("enabled", True)),
            "poll_interval_seconds": max(10, min(int(payload.get("poll_interval_seconds") or 300), 86400)),
            "config": config,
        })

    def ingest_external_events(self, source_key: str, events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        source = self.repo.get_source(source_key)
        if source is None:
            raise InvestmentMonitorError(f"monitoring source not found: {source_key}")
        normalized = [self._normalize_external_event(source, event) for event in events]
        result = self.repo.upsert_events(normalized)
        self.repo.update_source_status(
            source_key,
            status="success",
            item_count=result["created"],
            success_at=utc_naive_now(),
        )
        return {"source_key": source_key, **result}

    def sync_due_sources(self) -> Dict[str, Any]:
        sources = self.repo.due_sources()
        return self._sync_sources(sources)

    def sync_all(self, *, categories: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        selected = set(categories or [])
        sources = [
            source for source in self.repo.list_sources()
            if source["enabled"] and (not selected or source["category"] in selected)
        ]
        return self._sync_sources(sources)

    def sync_source(self, source_key: str) -> Dict[str, Any]:
        source = self.repo.get_source(source_key)
        if source is None:
            raise InvestmentMonitorError(f"monitoring source not found: {source_key}")
        return self._sync_one(source)

    def announcement_categories(self) -> Dict[str, Any]:
        items = self.cninfo.categories()
        return {"items": items, "total": len(items)}

    def sync_announcements(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        source = self.repo.get_source("cninfo.announcements")
        if source is None:
            raise InvestmentMonitorError("CNInfo monitoring source is unavailable")
        try:
            start = datetime.strptime(str(payload.get("start_date")), "%Y-%m-%d").date()
            end = datetime.strptime(str(payload.get("end_date")), "%Y-%m-%d").date()
        except ValueError as exc:
            raise InvestmentMonitorError("start_date and end_date must use YYYY-MM-DD") from exc
        try:
            rows = self.cninfo.fetch(
                start_date=start, end_date=end, symbols=payload.get("symbols") or [],
                categories=payload.get("categories") or [], keyword=str(payload.get("keyword") or ""),
                max_pages=int(payload.get("max_pages") or 20),
            )
        except CninfoAnnouncementError as exc:
            raise InvestmentMonitorError(str(exc)) from exc
        saved = self.repo.upsert_events(self._announcement_events(source, rows))
        self.repo.update_source_status("cninfo.announcements", status="success",
                                       item_count=saved["created"], success_at=utc_naive_now())
        return {"source_key": "cninfo.announcements", "fetched": len(rows), **saved}

    def list_announcements(self, **filters: Any) -> Dict[str, Any]:
        now = datetime.now(timezone(timedelta(hours=8)))
        start_raw = str(filters.get("start_date") or "").strip()
        end_raw = str(filters.get("end_date") or "").strip()
        try:
            end_date = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else now.date()
            if start_raw:
                start_date = datetime.strptime(start_raw, "%Y-%m-%d").date()
            else:
                start_date = end_date - timedelta(days=max(1, min(int(filters.get("days") or 30), 3650)) - 1)
        except ValueError as exc:
            raise InvestmentMonitorError("start_date and end_date must use YYYY-MM-DD") from exc
        if end_date < start_date:
            raise InvestmentMonitorError("end_date cannot be earlier than start_date")
        symbol = self._canonical_ts_code(filters.get("symbol")) if filters.get("symbol") else None
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_size") or 50), 500))
        rows, total = self.repo.list_announcements(
            start_at=datetime.combine(start_date, datetime.min.time()),
            end_at=datetime.combine(end_date, datetime.max.time()),
            symbol=symbol, category=str(filters.get("category") or "").strip() or None,
            query=str(filters.get("query") or "").strip() or None,
            page=page, page_size=page_size,
        )
        return {"items": rows, "total": total, "page": page, "page_size": page_size,
                "start_date": start_date.isoformat(), "end_date": end_date.isoformat()}

    def export_announcements(self, **filters: Any) -> bytes:
        result = self.list_announcements(**filters, page=1, page_size=500)
        if not result["items"]:
            raise InvestmentMonitorError("没有符合条件的已入库公告")
        return self.announcement_artifacts.excel_bytes(result["items"])

    def package_announcements(self, event_ids: Sequence[int], *, include_text: bool = True) -> Dict[str, Any]:
        events = self.repo.get_events_by_ids(event_ids)
        selected = [
            event for event in events
            if event["source_key"] == "cninfo.announcements" and event["event_type"] == "company_announcement"
        ]
        if len(selected) != len(set(int(value) for value in event_ids)):
            raise InvestmentMonitorError("部分公告不存在或不属于巨潮公告数据源")
        try:
            return self.announcement_artifacts.package(selected, include_text=include_text)
        except AnnouncementArtifactError as exc:
            raise InvestmentMonitorError(str(exc)) from exc

    def list_events(self, **filters: Any) -> Dict[str, Any]:
        days = max(1, min(int(filters.get("days") or 7), 3650))
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_size") or 50), 200))
        symbol = self._canonical_ts_code(filters.get("symbol")) if filters.get("symbol") else None
        rows, total = self.repo.list_events(
            days=days,
            symbol=symbol,
            perspective=str(filters.get("perspective") or "").strip() or None,
            event_type=str(filters.get("event_type") or "").strip() or None,
            source_key=str(filters.get("source_key") or "").strip() or None,
            channel=str(filters.get("channel") or "").strip() or None,
            evidence_level=str(filters.get("evidence_level") or "").strip() or None,
            query=str(filters.get("query") or "").strip() or None,
            min_importance=filters.get("min_importance"),
            page=page,
            page_size=page_size,
        )
        return {"items": rows, "total": total, "page": page, "page_size": page_size}

    def sync_dragon_tiger_day(self, trade_date: str, *, include_seats: bool = True) -> Dict[str, Any]:
        """Fetch and persist the full-market exchange leaderboard for one day."""
        normalized_date = self._validate_trade_date(trade_date)
        source = self.repo.get_source("tushare.market_activity")
        if source is None:
            raise InvestmentMonitorError("Tushare 龙虎榜数据源未注册")
        started_at = utc_naive_now()
        try:
            top_rows = self.tushare.query("top_list", params={"trade_date": normalized_date})["rows"]
            inst_rows = self.tushare.query("top_inst", params={"trade_date": normalized_date})["rows"] if include_seats else []
            events = self._dragon_tiger_events(source, normalized_date, top_rows, inst_rows)
            saved = self.repo.upsert_events(events)
            self.repo.update_source_status(
                source["source_key"], status="success", item_count=len(events),
                started_at=started_at, success_at=utc_naive_now(),
            )
            return {
                "trade_date": normalized_date,
                "top_list_count": len(top_rows),
                "seat_count": len(inst_rows),
                "include_seats": include_seats,
                **saved,
            }
        except Exception as exc:
            self.repo.update_source_status(source["source_key"], status="failed", error=str(exc), started_at=started_at)
            if isinstance(exc, InvestmentMonitorError):
                raise
            raise InvestmentMonitorError(f"Tushare 龙虎榜获取失败：{type(exc).__name__}") from exc

    def sync_dragon_tiger_range(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Backfill daily summaries for open days; seat detail stays on-demand by day."""
        start = self._validate_trade_date(start_date)
        end = self._validate_trade_date(end_date)
        start_day = datetime.strptime(start, "%Y%m%d")
        end_day = datetime.strptime(end, "%Y%m%d")
        if end_day < start_day:
            raise InvestmentMonitorError("结束日期不能早于开始日期")
        if (end_day - start_day).days > 120:
            raise InvestmentMonitorError("单次最多补齐 120 个自然日")
        calendar = self.tushare.query("trade_cal", params={
            "exchange": "SSE", "start_date": start, "end_date": end, "is_open": "1",
        })["rows"]
        dates = sorted({str(row.get("cal_date") or "") for row in calendar if row.get("cal_date")})
        results = [self.sync_dragon_tiger_day(value, include_seats=False) for value in dates]
        return {
            "start_date": start,
            "end_date": end,
            "trade_days": len(dates),
            "top_list_count": sum(int(item["top_list_count"]) for item in results),
            "created": sum(int(item["created"]) for item in results),
            "updated": sum(int(item["updated"]) for item in results),
            "dates": dates,
        }

    def dragon_tiger_daily(self, *, trade_date: Optional[str] = None, refresh: bool = False) -> Dict[str, Any]:
        requested = self._validate_trade_date(trade_date) if trade_date else None
        if requested:
            candidates = [requested]
        else:
            now = datetime.now(timezone(timedelta(hours=8)))
            start = (now - timedelta(days=14)).strftime("%Y%m%d")
            end = now.strftime("%Y%m%d")
            calendar = self.tushare.query("trade_cal", params={
                "exchange": "SSE", "start_date": start, "end_date": end, "is_open": "1",
            })["rows"]
            candidates = sorted(
                {str(row.get("cal_date") or "") for row in calendar if row.get("cal_date")}, reverse=True,
            )
        selected = candidates[0] if candidates else datetime.now().strftime("%Y%m%d")
        fetched = False
        for candidate in candidates[:6] or [selected]:
            start_at, end_at = self._trade_date_bounds(candidate)
            top = self.repo.all_events_between(
                event_types=("dragon_tiger",), start_at=start_at, end_at=end_at,
            )
            seats = self.repo.all_events_between(
                event_types=("institution_seat",), start_at=start_at, end_at=end_at,
            )
            if refresh or not top or not seats:
                self.sync_dragon_tiger_day(candidate, include_seats=True)
                fetched = True
                top = self.repo.all_events_between(
                    event_types=("dragon_tiger",), start_at=start_at, end_at=end_at,
                )
                seats = self.repo.all_events_between(
                    event_types=("institution_seat",), start_at=start_at, end_at=end_at,
                )
            if top or requested:
                selected = candidate
                break
        return self._dragon_tiger_daily_payload(selected, top, seats, fetched=fetched)

    def dragon_tiger_history(self, **filters: Any) -> Dict[str, Any]:
        start = self._validate_trade_date(filters.get("start_date"))
        end = self._validate_trade_date(filters.get("end_date"))
        start_day = datetime.strptime(start, "%Y%m%d")
        end_day = datetime.strptime(end, "%Y%m%d")
        if end_day < start_day:
            raise InvestmentMonitorError("结束日期不能早于开始日期")
        if (end_day - start_day).days > 366:
            raise InvestmentMonitorError("历史查询最多覆盖 366 个自然日")
        start_at, _ = self._trade_date_bounds(start)
        _, end_at = self._trade_date_bounds(end)
        symbol = self._canonical_ts_code(filters.get("symbol")) if filters.get("symbol") else None
        query = str(filters.get("query") or "").strip() or None
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_size") or 50), 200))
        rows, total = self.repo.list_events_between(
            event_types=("dragon_tiger",), start_at=start_at, end_at=end_at,
            symbol=symbol, query=query, page=page, page_size=page_size,
        )
        all_rows = self.repo.all_events_between(
            event_types=("dragon_tiger",), start_at=start_at, end_at=end_at,
        )
        by_date: Dict[str, Dict[str, Any]] = {}
        for event in all_rows:
            metrics = event.get("metrics") or {}
            trade = str(metrics.get("trade_date") or "")
            bucket = by_date.setdefault(trade, {"trade_date": trade, "rows": 0, "symbols": set(), "net_amount": 0.0})
            bucket["rows"] += 1
            bucket["symbols"].update(event.get("symbols") or [])
            bucket["net_amount"] += self._number(metrics.get("net_amount"))
        trend = [
            {**bucket, "symbols": len(bucket["symbols"]), "net_amount": round(bucket["net_amount"], 2)}
            for _, bucket in sorted(by_date.items())
        ]
        return {
            "items": [self._dragon_tiger_record(event) for event in rows],
            "total": total, "page": page, "page_size": page_size,
            "start_date": start, "end_date": end, "trend": trend,
            "cached_trade_days": len(trend),
        }

    def event_detail(self, event_id: int) -> Dict[str, Any]:
        event = self.repo.get_event(event_id)
        if event is None:
            raise InvestmentMonitorError("情报事件不存在")
        return event

    def dashboard(self, *, days: int = 7) -> Dict[str, Any]:
        safe_days = max(1, min(int(days), 90))
        watchlist = self.watchlist()
        events = [event for event in self.repo.all_recent_events(days=safe_days) if event["event_type"] != "realtime_quote"]
        perspective_counts = Counter(event["perspective"] for event in events)
        type_counts = Counter(event["event_type"] for event in events)
        source_counts = Counter(event["source_key"] for event in events)
        evidence_counts = Counter(self._event_evidence(event)["evidence_level"] for event in events)
        channel_counts = Counter(self._event_evidence(event)["channel"] for event in events)
        original_count = sum(1 for event in events if event.get("url"))
        symbol_buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for event in events:
            for symbol in event["symbols"]:
                symbol_buckets[symbol].append(event)
        cards = [
            self._symbol_card(symbol, self._stock_name(symbol), symbol_buckets.get(symbol, []))
            for symbol in watchlist
        ]
        latest = sorted(events, key=lambda event: event.get("event_at") or "", reverse=True)
        return {
            "days": safe_days,
            "generated_at": utc_naive_now().isoformat() + "Z",
            "watchlist": cards,
            "summary": {
                "event_count": len(events),
                "watchlist_count": len(watchlist),
                "high_priority_count": sum(1 for event in events if event["importance_score"] >= 75),
                "bullish_count": sum(1 for event in events if event["sentiment"] == "bullish"),
                "bearish_count": sum(1 for event in events if event["sentiment"] == "bearish"),
                "active_source_count": len(source_counts),
                "factual_count": len(events) - evidence_counts["unverified"],
                "unverified_count": evidence_counts["unverified"],
                "original_link_count": original_count,
                "original_link_coverage": round(original_count * 100 / len(events), 1) if events else 0,
            },
            "perspectives": self._counter_rows(perspective_counts),
            "event_types": self._counter_rows(type_counts, 12),
            "source_activity": self._counter_rows(source_counts, 12),
            "evidence_levels": self._counter_rows(evidence_counts),
            "channels": self._counter_rows(channel_counts),
            "latest_events": [self.compact_event(event) for event in latest[:40]],
            "high_priority": [self.compact_event(event) for event in latest if event["importance_score"] >= 75][:20],
            "sources": self.list_sources(),
        }

    def intelligence_dashboard(self, *, days: int = 14) -> Dict[str, Any]:
        """Decision-oriented aggregates for the multi-page intelligence workbench."""
        safe_days = max(7, min(int(days), 90))
        events = [event for event in self.repo.all_recent_events(days=safe_days * 2) if event["event_type"] != "realtime_quote"]
        now = utc_naive_now()
        current_cutoff = now - timedelta(days=safe_days)
        previous_cutoff = now - timedelta(days=safe_days * 2)

        def parsed_at(event: Dict[str, Any]) -> datetime:
            raw = str(event.get("event_at") or "").replace("Z", "+00:00")
            try:
                value = datetime.fromisoformat(raw)
                if value.tzinfo is not None:
                    value = value.astimezone(timezone.utc).replace(tzinfo=None)
                return value
            except ValueError:
                return datetime.min

        current = [event for event in events if current_cutoff <= parsed_at(event) <= now]
        previous = [event for event in events if previous_cutoff <= parsed_at(event) < current_cutoff]

        # Repeated quote/factor snapshots remain available in the factual stream, but a
        # decision dashboard should not count the same state hundreds of times. Keep
        # only the latest derived snapshot per source/type/symbol for scoring; the
        # complete intraday and historical series stays available in the factual feed.
        decision_current: List[Dict[str, Any]] = []
        seen_snapshots = set()
        for event in sorted(current, key=parsed_at, reverse=True):
            evidence = self._event_evidence(event)
            is_snapshot = evidence.get("content_nature") == "derived_summary" or event["event_type"] in {
                "realtime_quote", "market_snapshot", "fundamental_snapshot", "capital_chip_snapshot",
                "ownership_snapshot", "technical_factor", "market_theme_flow", "limit_pool",
            }
            if is_snapshot:
                key = (event["source_key"], event["event_type"], tuple(event["symbols"]))
                if key in seen_snapshots:
                    continue
                seen_snapshots.add(key)
            decision_current.append(event)

        daily: Dict[str, Counter] = defaultdict(Counter)
        for offset in range(safe_days - 1, -1, -1):
            daily[(now - timedelta(days=offset)).date().isoformat()]
        channel_rows: Dict[str, Counter] = defaultdict(Counter)
        for event in current:
            day = parsed_at(event).date().isoformat()
            if day not in daily:
                continue
            evidence = self._event_evidence(event)
            level, channel = evidence["evidence_level"], evidence["channel"]
            daily[day]["total"] += 1
            daily[day]["high_priority"] += int(event["importance_score"] >= 75)
            daily[day]["unverified"] += int(level == "unverified")
            daily[day]["factual"] += int(level != "unverified")
            channel_rows[channel]["count"] += 1
            channel_rows[channel]["high_priority"] += int(event["importance_score"] >= 75)
            channel_rows[channel][event["sentiment"]] += 1

        current_channels = Counter(self._event_evidence(event)["channel"] for event in current)
        previous_channels = Counter(self._event_evidence(event)["channel"] for event in previous)
        channels = []
        for channel, count in current_channels.most_common():
            prior = previous_channels[channel]
            delta = round((count - prior) * 100 / prior, 1) if prior else (100.0 if count else 0.0)
            channels.append({"name": channel, **dict(channel_rows[channel]), "previous_count": prior, "change_pct": delta})

        def stock_name(symbol: str) -> str:
            for event in decision_current:
                if symbol not in event["symbols"]:
                    continue
                value = str((event.get("metrics") or {}).get("stock_name") or "").strip()
                if value:
                    return value
            return self._stock_name(symbol)

        contradictions = []
        for symbol in self.watchlist():
            related = [event for event in decision_current if symbol in event["symbols"] and self._event_evidence(event)["evidence_level"] != "unverified"]
            bulls = [event for event in related if event["sentiment"] == "bullish"]
            bears = [event for event in related if event["sentiment"] == "bearish"]
            if bulls and bears:
                contradictions.append({
                    "symbol": symbol, "name": stock_name(symbol),
                    "bullish_count": len(bulls), "bearish_count": len(bears),
                    "bullish_evidence": self.compact_event(bulls[0]),
                    "bearish_evidence": self.compact_event(bears[0]),
                })

        factual = [event for event in current if self._event_evidence(event)["evidence_level"] != "unverified"]
        decision_factual = [event for event in decision_current if self._event_evidence(event)["evidence_level"] != "unverified"]
        latest = sorted(current, key=parsed_at, reverse=True)
        signal_events = sorted(decision_factual, key=lambda item: (item["importance_score"], parsed_at(item)), reverse=True)
        return {
            "days": safe_days, "generated_at": now.isoformat() + "Z",
            "summary": {
                "event_count": len(current), "previous_event_count": len(previous),
                "event_change_pct": round((len(current) - len(previous)) * 100 / len(previous), 1) if previous else (100.0 if current else 0.0),
                "factual_count": len(factual),
                "high_priority_count": sum(1 for event in decision_current if event["importance_score"] >= 75),
                "watchlist_hits": sum(1 for event in decision_current if event["symbols"]),
                "source_count": len({event["source_key"] for event in current}),
            },
            "daily_trend": [{"date": day, **dict(bucket)} for day, bucket in sorted(daily.items())],
            "channels": channels,
            "watchlist": [self._symbol_card(symbol, stock_name(symbol), [event for event in decision_current if symbol in event["symbols"]]) for symbol in self.watchlist()],
            "signal_events": [self.compact_event(event) for event in signal_events[:12]],
            "pulse": [self.compact_event(event) for event in latest[:20]],
            "contradictions": contradictions,
            "sources": self.list_sources(),
        }

    def symbol_detail(self, symbol: str, *, days: int = 30) -> Dict[str, Any]:
        code = self._canonical_ts_code(symbol)
        result = self.list_events(days=days, symbol=code, page=1, page_size=200)
        events = result["items"]
        return {
            "symbol": code,
            "name": self._stock_name(code),
            "scorecard": self._symbol_card(code, self._stock_name(code), events),
            "perspectives": {
                perspective: [event for event in events if event["perspective"] == perspective]
                for perspective in ("investor", "company", "institution")
            },
            "events": events,
            "total": result["total"],
        }

    def super_watchlist(self, *, days: int = 365) -> Dict[str, Any]:
        """Build every watchlist card from shared stores, never page-local fetches."""
        safe_days = max(30, min(int(days), 3650))
        symbols = self.watchlist()
        try:
            quote_rows = MarketDataService().latest_quotes(symbols, refresh_missing=False)
        except Exception as exc:  # noqa: BLE001 - evidence must still render if quote cache is unavailable.
            logger.info("Shared quote cache unavailable for super watchlist: %s", type(exc).__name__)
            quote_rows = []
        quotes = {
            str(row.get("stock_code") or "").split(".", 1)[0]: row
            for row in quote_rows if row.get("stock_code")
        }
        source_states = {item["source_key"]: item for item in self.repo.list_sources()}
        stocks = [
            self._super_stock(
                symbol, days=safe_days,
                live_quote=quotes.get(symbol.split(".", 1)[0]),
                source_states=source_states,
            )
            for symbol in symbols
        ]
        return {
            "version": "5.1-shared-store",
            "generated_at": utc_naive_now().isoformat() + "Z",
            "days": safe_days,
            "data_policy": {
                "market": "market_ticks + market_intraday + stock_daily",
                "evidence": "monitoring_events",
                "refresh": "background_workers",
                "page_fetches_upstream": False,
            },
            "stocks": stocks,
            "backfill_jobs": self.repo.list_backfill_jobs(stock["symbol"] for stock in stocks),
            "comparison": [
                {
                    "symbol": stock["symbol"], "name": stock["name"],
                    "price": stock["market"].get("price"),
                    "change_pct": stock["market"].get("change_pct"),
                    "pe_ttm": stock["valuation"].get("pe_ttm"),
                    "pb": stock["valuation"].get("pb"),
                    "revenue_yoy": stock["fundamentals"].get("revenue_yoy"),
                    "net_profit_yoy": stock["fundamentals"].get("net_profit_yoy"),
                    "roe": stock["fundamentals"].get("roe"),
                    "winner_rate": stock["capital"].get("winner_rate"),
                    "event_count": stock["evidence"]["event_count"],
                    "factual_count": stock["evidence"]["factual_count"],
                    "research_count": stock["institution"]["research_count"],
                    "announcement_count": stock["company"]["announcement_count"],
                }
                for stock in stocks
            ],
            "iterations": [
                {"version": "V1", "name": "数据归一", "result": "行情、估值、财务、资金、公告、研报、企业和另类情报统一到证据事件"},
                {"version": "V2", "name": "单股全景", "result": "每只新增自选股自动生成市场、经营、资本、机构、公司和消息画像"},
                {"version": "V3", "name": "证据去重", "result": "高频快照只保留最新状态参与评分，原始流水完整保留"},
                {"version": "V4", "name": "双股对照", "result": "同口径比较估值、增长、盈利质量、筹码和信息覆盖"},
                {"version": "V5", "name": "研判闭环", "result": "事实底稿、催化、风险、待验证问题和大模型证据包联动"},
            ],
        }

    def stock_workspace(
        self, symbol: str, *, days: int = 365, refresh: bool = False,
    ) -> Dict[str, Any]:
        """Return one shared, local-first stock context for every legacy module."""
        safe_days = max(30, min(int(days), 3650))
        code = self._canonical_ts_code(symbol)
        cache_key = f"{code}:{safe_days}"
        if not refresh:
            with _stock_workspace_cache_lock:
                cached = _stock_workspace_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] <= _STOCK_WORKSPACE_CACHE_TTL_SECONDS:
                return {**cached[1], "cache": {"hit": True, "ttl_seconds": _STOCK_WORKSPACE_CACHE_TTL_SECONDS}}

        try:
            quote_rows = MarketDataService().latest_quotes([code], refresh_missing=False)
        except Exception as exc:  # noqa: BLE001 - factual context remains useful without a quote.
            logger.info("Shared quote cache unavailable for %s workspace: %s", code, type(exc).__name__)
            quote_rows = []
        quote = next((row for row in quote_rows if row.get("stock_code")), None)
        source_states = {item["source_key"]: item for item in self.repo.list_sources()}
        stock = self._super_stock(code, days=safe_days, live_quote=quote, source_states=source_states)
        payload = {
            "version": "5.2-unified-decision-context",
            "generated_at": utc_naive_now().isoformat() + "Z",
            "days": safe_days,
            "stock": stock,
            "agent_context": self._agent_context_from_stock(stock),
            "data_policy": {
                "facts": "monitoring_events + shared market database",
                "upstream_fetch_on_read": False,
                "failure_mode": "stale-while-revalidate",
                "quote_precedence": "shared realtime cache, then latest Tushare snapshot",
            },
            "iterations": [
                {"version": "V1", "name": "统一事实底座", "result": "原有模块共用同一份个股事实与行情口径"},
                {"version": "V2", "name": "Tushare 深度接入", "result": "估值、财务、资金、筹码、机构和股东数据进入决策上下文"},
                {"version": "V3", "name": "非结构化证据", "result": "公告、研报、小作文、天眼查和股评保留时间与来源"},
                {"version": "V4", "name": "稳定性与预加载", "result": "本地优先、短时共享缓存、单源失败不阻断页面"},
                {"version": "V5", "name": "分析闭环", "result": "问股、持仓、信号、回测和告警共享可追溯输入"},
            ],
        }
        with _stock_workspace_cache_lock:
            _stock_workspace_cache[cache_key] = (time.monotonic(), payload)
            if len(_stock_workspace_cache) > 128:
                oldest = min(_stock_workspace_cache, key=lambda key: _stock_workspace_cache[key][0])
                _stock_workspace_cache.pop(oldest, None)
        return {**payload, "cache": {"hit": False, "ttl_seconds": _STOCK_WORKSPACE_CACHE_TTL_SECONDS}}

    @staticmethod
    def _agent_context_from_stock(stock: Dict[str, Any]) -> Dict[str, Any]:
        """Compact factual stock workspace into a bounded LLM input."""
        coverage = list(stock.get("coverage") or [])
        timeline = list(stock.get("timeline") or [])[:12]
        facts = [
            {
                "event_id": item.get("id"), "time": item.get("event_at"),
                "channel": item.get("channel"), "source": item.get("source_name"),
                "type": item.get("event_type"), "title": item.get("title"),
                "evidence_level": item.get("evidence_level"),
            }
            for item in timeline
        ]
        compact = {
            "symbol": stock.get("symbol"), "name": stock.get("name"),
            "market": stock.get("market") or {}, "valuation": stock.get("valuation") or {},
            "technical": stock.get("technical") or {}, "fundamentals": stock.get("fundamentals") or {},
            "capital": {
                key: (stock.get("capital") or {}).get(key)
                for key in ("winner_rate", "weighted_cost", "moneyflow", "margin", "northbound")
            },
            "consensus": stock.get("consensus") or {},
            "signals": list(stock.get("signals") or [])[:10],
            "evidence": stock.get("evidence") or {},
            "coverage": [
                {key: item.get(key) for key in ("name", "count", "latest_at", "freshness_status", "source_keys")}
                for item in coverage
            ],
            "latest_facts": facts,
        }
        summary = (
            "\n[全渠道统一个股事实底稿]\n"
            "以下数据来自本地共享数据库；引用结论时必须注明对应时间与来源，"
            "未核验小作文/股评不得表述为公司事实。\n"
            + json.dumps(compact, ensure_ascii=False, default=str)
        )
        return {
            "analysis_context_pack_summary": summary,
            "realtime_quote": stock.get("market") or {},
            "chip_distribution": stock.get("capital") or {},
            "news_context": json.dumps(facts, ensure_ascii=False, default=str),
            "fundamental_context": {
                "fundamentals": stock.get("fundamentals") or {},
                "valuation": stock.get("valuation") or {}, "coverage": coverage,
            },
            "evidence_count": (stock.get("evidence") or {}).get("event_count", 0),
            "source_count": (stock.get("evidence") or {}).get("source_count", 0),
        }

    def _super_stock(
        self, symbol: str, *, days: int,
        live_quote: Optional[Dict[str, Any]] = None,
        source_states: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        # Aggregates require the complete symbol dataset.  The previous paginated
        # read silently discarded events after row 200 and could hide an older but
        # still-current fundamental snapshot behind newer news and research rows.
        events = self.repo.all_symbol_events(symbol=symbol, days=days)
        source_states = source_states or {}

        def latest(event_type: str) -> Optional[Dict[str, Any]]:
            return next((item for item in events if item["event_type"] == event_type), None)

        def metrics(event_type: str) -> Dict[str, Any]:
            event = latest(event_type)
            return dict(event.get("metrics") or {}) if event else {}

        opening = metrics("market_open_snapshot")
        market = metrics("market_snapshot")
        technical = metrics("technical_factor")
        fundamental = metrics("fundamental_snapshot")
        indicator = dict(fundamental.get("indicator") or {})
        income = dict(fundamental.get("income") or {})
        cashflow = dict(fundamental.get("cashflow") or {})
        capital = metrics("capital_chip_snapshot")
        chip = dict(capital.get("chip_performance") or {})
        moneyflow = dict(capital.get("moneyflow") or {})
        margin = dict(capital.get("margin") or {})
        northbound = dict(capital.get("northbound") or {})
        ownership = metrics("ownership_snapshot")
        profile = metrics("company_profile")
        research = [item for item in events if item["event_type"] in {"research_report_pdf", "institution_forecast", "institution_survey", "broker_recommendation"}]
        announcements = [item for item in events if item["event_type"] == "company_announcement"]
        essays = [item for item in events if item["event_type"] == "essay"]
        stock_comments = [item for item in events if item["event_type"] == "stock_forum_post"]
        message_types = {
            "news", "long_news", "essay", "enterprise_registration", "enterprise_risk",
            "enterprise_credit", "enterprise_ipr", "enterprise_history",
        }
        messages = [item for item in events if item["event_type"] in message_types]

        snapshot_types = {
            "market_open_snapshot", "market_snapshot", "fundamental_snapshot", "capital_chip_snapshot",
            "ownership_snapshot", "technical_factor", "market_theme_flow", "limit_pool",
        }
        decision_events: List[Dict[str, Any]] = []
        seen = set()
        for event in events:
            key = (event["source_key"], event["event_type"], tuple(event["symbols"]))
            if event["event_type"] in snapshot_types and key in seen:
                continue
            if event["event_type"] in snapshot_types:
                seen.add(key)
            decision_events.append(event)

        levels = Counter(self._event_evidence(event)["evidence_level"] for event in decision_events)
        channels = Counter(self._event_evidence(event)["channel"] for event in decision_events)
        source_count = len({event["source_key"] for event in decision_events})
        original_count = sum(1 for event in decision_events if event.get("url"))
        signals: List[Dict[str, Any]] = []

        def signal(kind: str, title: str, detail: str, event_type: str) -> None:
            event = latest(event_type)
            signals.append({
                "kind": kind, "title": title, "detail": detail,
                "event_id": event.get("id") if event else None,
                "event_at": event.get("event_at") if event else None,
                "source_name": event.get("source_name") if event else None,
            })

        live_price = self._number(live_quote.get("current_price")) if live_quote else 0.0
        has_live_quote = live_price > 0
        price = live_price if has_live_quote else market.get("close", opening.get("open"))
        change_pct = live_quote.get("change_percent") if has_live_quote else market.get("pct_chg", opening.get("open_change_pct"))
        pe_ttm = market.get("valuation_pe_ttm")
        revenue_yoy = indicator.get("q_sales_yoy", indicator.get("tr_yoy"))
        profit_yoy = indicator.get("netprofit_yoy", indicator.get("q_netprofit_yoy"))
        if isinstance(change_pct, (int, float)) and abs(change_pct) >= 5:
            signal("risk" if change_pct < 0 else "catalyst", f"收盘涨跌 {change_pct:.2f}%", "收盘波动超过 5%，需要结合成交额和公告核验原因。", "market_snapshot")
        if isinstance(revenue_yoy, (int, float)):
            signal("catalyst" if revenue_yoy > 0 else "risk", f"单季收入同比 {revenue_yoy:.2f}%", "来自最新财务指标口径。", "fundamental_snapshot")
        if isinstance(profit_yoy, (int, float)):
            signal("catalyst" if profit_yoy > 0 else "risk", f"净利润同比 {profit_yoy:.2f}%", "增长方向用于经营趋势观察，不代表未来预测。", "fundamental_snapshot")
        if isinstance(pe_ttm, (int, float)) and pe_ttm > 80:
            signal("risk", f"滚动市盈率 {pe_ttm:.2f}", "估值处于高倍数区间，需要更高盈利增速消化。", "market_snapshot")
        if isinstance(chip.get("winner_rate"), (int, float)):
            signal("watch", f"筹码获利比例 {chip['winner_rate']:.2f}%", f"加权平均成本 {chip.get('weight_avg') or '—'}，用于观察筹码压力。", "capital_chip_snapshot")
        if isinstance(technical.get("rsi_6"), (int, float)):
            signal("watch", f"RSI(6) {technical['rsi_6']:.2f}", "技术指标只描述当前价格状态。", "technical_factor")

        essay_catalysts, essay_risks = [], []
        for event in essays:
            for key, target in (("catalysts", essay_catalysts), ("risks", essay_risks)):
                values = (event.get("metrics") or {}).get(key) or []
                if isinstance(values, list):
                    target.extend(str(value).strip() for value in values if str(value).strip())

        dimensions = {
            "market": {"types": ("market_open_snapshot", "market_snapshot", "technical_factor"), "sources": ("tushare.market", "tushare.technical")},
            "fundamental": {"types": ("fundamental_snapshot", "earnings_forecast", "earnings_express"), "sources": ("tushare.fundamentals", "tushare.company")},
            "capital": {"types": ("capital_chip_snapshot", "block_trade", "dragon_tiger", "institution_seat"), "sources": ("tushare.capital", "tushare.market_activity")},
            "institution": {"types": ("research_report_pdf", "institution_forecast", "institution_survey", "broker_recommendation"), "sources": ("tushare.institution", "tushare.research_pdf")},
            "company": {"types": ("company_announcement", "company_profile", "audit_opinion", "executive_roster"), "sources": ("cninfo.announcements", "tushare.company_profile")},
            "ownership": {"types": ("ownership_snapshot", "holder_trade", "share_unlock", "top_shareholders"), "sources": ("tushare.ownership", "tushare.company_profile")},
            "enterprise": {"types": ("enterprise_registration", "enterprise_risk", "enterprise_credit", "enterprise_ipr"), "sources": ("tianyancha.enterprise",)},
            "essay": {"types": ("essay",), "sources": ("zsxq.essays",)},
            "comment": {
                "types": ("stock_forum_post",),
                "sources": ("eastmoney.guba_posts",),
            },
        }
        coverage = []
        for name, definition in dimensions.items():
            related = [event for event in decision_events if event["event_type"] in definition["types"]]
            states = [source_states[key] for key in definition["sources"] if key in source_states]
            sync_times = [str(item.get("last_success_at") or "") for item in states if item.get("last_success_at")]
            latest_sync = max(sync_times, default=None)
            cadences = [int(item.get("poll_interval_seconds") or 0) for item in states if item.get("enabled", True)]
            target_seconds = min(cadences) if cadences else None
            sync_age = None
            if latest_sync:
                try:
                    parsed_sync = datetime.fromisoformat(latest_sync.replace("Z", "+00:00")).replace(tzinfo=None)
                    sync_age = max(0, int((utc_naive_now() - parsed_sync).total_seconds()))
                except ValueError:
                    sync_age = None
            freshness = "empty" if not related else "fresh" if sync_age is not None and sync_age <= max(60, (target_seconds or 86400) * 3) else "stale"
            coverage.append({
                "name": name, "count": len(related),
                "latest_at": related[0]["event_at"] if related else None,
                "available": bool(related),
                "freshness_status": freshness,
                "last_sync_at": latest_sync,
                "sync_age_seconds": sync_age,
                "target_refresh_seconds": target_seconds,
                "source_keys": list(definition["sources"]),
            })

        stock_name = self._stock_name(symbol)
        try:
            essay_consensus = EssayConsensusService().snapshot(symbol, stock_name, limit=20)
        except Exception as exc:  # noqa: BLE001 - an AI cache must never hide the factual stock workspace.
            logger.info("Essay consensus snapshot unavailable for %s: %s", symbol, type(exc).__name__)
            essay_consensus = {
                "status": "failed", "source_count": min(len(essays), 20), "analyzed_count": 0,
                "related_source_count": min(len(essays), 20), "dedicated_source_count": 0,
                "pending_count": 0, "summary": "", "estimates": [], "metric_counts": {},
                "consensus_points": [], "conflicts": [], "time_observations": [],
                "verification_conditions": [], "caveats": [], "source_notes": [],
                "error": "一致预期缓存暂时不可用",
            }
        cutoff = (utc_naive_now() - timedelta(days=days)).date()
        with DatabaseManager.get_instance().get_session() as session:
            bars = session.execute(
                select(StockDaily).where(
                    StockDaily.code == symbol.split(".")[0], StockDaily.date >= cutoff,
                ).order_by(StockDaily.date)
            ).scalars().all()
        return {
            "symbol": symbol, "name": stock_name,
            "history": [{
                "date": row.date.isoformat(), "open": row.open, "high": row.high,
                "low": row.low, "close": row.close, "volume": row.volume,
                "amount": row.amount, "pct_chg": row.pct_chg,
            } for row in bars],
            "market": {
                "price": price, "change_pct": change_pct,
                "open": live_quote.get("open") if has_live_quote else market.get("open", opening.get("open")),
                "high": live_quote.get("high") if has_live_quote else market.get("high"),
                "low": live_quote.get("low") if has_live_quote else market.get("low"),
                # Tushare daily.amount is 千元; the shared realtime cache stores 元.
                "amount": live_quote.get("amount") if has_live_quote else self._number(market.get("amount")) * 1000 or None,
                "updated_at": live_quote.get("update_time") if has_live_quote else (latest("market_snapshot") or latest("market_open_snapshot") or {}).get("event_at"),
                "source": live_quote.get("source") if has_live_quote else "monitoring_events:tushare.daily",
                "is_stale": bool(live_quote.get("is_stale")) if has_live_quote else True,
                "stale_seconds": live_quote.get("stale_seconds") if has_live_quote else None,
            },
            "valuation": {
                "pe": market.get("valuation_pe"), "pe_ttm": pe_ttm, "pb": market.get("valuation_pb"),
                "ps_ttm": market.get("valuation_ps_ttm"), "total_mv": market.get("valuation_total_mv"),
                "turnover_rate": market.get("valuation_turnover_rate"), "volume_ratio": market.get("valuation_volume_ratio"),
            },
            "technical": {key: technical.get(key) for key in ("rsi_6", "rsi_12", "rsi_24", "macd", "macd_dif", "macd_dea", "kdj_k", "kdj_d", "kdj_j", "boll_upper", "boll_mid", "boll_lower", "cci", "trade_date")},
            "fundamentals": {
                "period": indicator.get("end_date") or income.get("end_date"), "revenue": income.get("revenue"),
                "net_profit": income.get("n_income_attr_p"), "operating_cashflow": cashflow.get("n_cashflow_act"),
                "revenue_yoy": revenue_yoy, "net_profit_yoy": profit_yoy,
                "gross_margin": indicator.get("grossprofit_margin"), "net_margin": indicator.get("netprofit_margin"),
                "roe": indicator.get("roe"), "debt_ratio": indicator.get("debt_to_assets"),
                "current_ratio": indicator.get("current_ratio"), "eps": indicator.get("eps"),
            },
            "capital": {
                "winner_rate": chip.get("winner_rate"), "weighted_cost": chip.get("weight_avg"),
                "cost_50pct": chip.get("cost_50pct"), "cost_85pct": chip.get("cost_85pct"),
                "moneyflow": moneyflow, "margin": margin, "northbound": northbound,
                "chip_distribution": list(capital.get("chip_distribution") or []),
            },
            "ownership": {
                "pledge": ownership.get("pledge") or {},
                "share_unlock": list(ownership.get("share_float") or [])[:20],
                "holder_trades": list(ownership.get("holder_trades") or [])[:20],
                "repurchases": list(ownership.get("repurchases") or [])[:20],
            },
            "institution": {
                "research_count": len(research), "latest": [self.compact_event(event) for event in research[:20]],
                "institutions": self._counter_rows(Counter(str((event.get("metrics") or {}).get("inst_csname") or event["source_name"]) for event in research), 10),
            },
            "company": {
                "profile": {key: profile.get(key) for key in ("com_name", "chairman", "manager", "secretary", "province", "city", "employees", "main_business", "website")},
                "announcement_count": len(announcements), "announcements": [self.compact_event(event) for event in announcements[:20]],
            },
            "alternative": {"essay_count": len(essays), "essays": [self.compact_event(event) for event in essays[:30]], "catalysts": list(dict.fromkeys(essay_catalysts))[:12], "risks": list(dict.fromkeys(essay_risks))[:12]},
            "consensus": self._consensus_payload(research, essays, essay_consensus),
            "messages": {
                "count": len(messages),
                "items": [self.compact_event(event) for event in messages[:40]],
                "channels": self._counter_rows(Counter(
                    "知识星球" if event["event_type"] == "essay"
                    else "天眼查" if event["event_type"].startswith("enterprise_")
                    else "相关新闻"
                    for event in messages
                )),
            },
            "stock_comments": {
                "count": len(stock_comments),
                "items": [self.compact_event(event) for event in stock_comments[:20]],
                "source_note": "只展示东方财富股吧真实公开帖子；保留作者、时间、正文摘录、互动数和图片并标记待核验，点击可在当前页面查看详情。",
            },
            "signals": signals,
            "coverage": coverage,
            "evidence": {
                "event_count": len(decision_events), "raw_event_count": len(events),
                "factual_count": len(decision_events) - levels["unverified"], "unverified_count": levels["unverified"],
                "source_count": source_count, "original_link_count": original_count,
                "original_link_coverage": round(original_count * 100 / len(decision_events), 1) if decision_events else 0,
                "channels": self._counter_rows(channels),
            },
            "timeline": [self.compact_event(event) for event in decision_events[:40]],
        }

    def watchlist(self) -> List[str]:
        if self._watchlist_override is not None:
            return list(self._watchlist_override)
        raw = list(getattr(get_config(), "stock_list", None) or [])
        return list(dict.fromkeys(self._canonical_ts_code(value) for value in raw if str(value).strip()))

    def reindex_watchlist_keywords(self, *, days: int = 3650) -> Dict[str, Any]:
        """Rebuild local essay-to-watchlist links without refetching upstream data."""
        aliases = {
            symbol: self._stock_aliases(self._stock_name(symbol))
            for symbol in self.watchlist()
        }
        result = self.repo.reindex_keyword_symbols(
            aliases_by_symbol=aliases, days=days, event_types=("essay",),
        )
        return {**result, "symbols": len(aliases), "days": max(1, int(days))}

    def equity_watchlist(self) -> List[str]:
        """Return stock-only symbols for company/fundamental APIs, excluding bonds and funds."""
        result = []
        for symbol in self.watchlist():
            match = _A_SHARE_RE.fullmatch(symbol)
            if not match:
                continue
            code = match.group(1)
            if code[0] in {"0", "2", "3", "4", "6", "8", "9"}:
                result.append(symbol)
        return result

    def request_backfill(self, symbol: str, *, days: int = 183) -> Dict[str, Any]:
        code = self._canonical_ts_code(symbol)
        return self.repo.create_backfill_job(code, stock_name=self._stock_name(code), days=max(30, min(days, 365)))

    def run_backfill_job(self, job_id: int, symbol: str, *, days: int = 183) -> Dict[str, Any]:
        """Fetch each configured channel independently and persist its facts idempotently."""
        self._watchlist_override = [self._canonical_ts_code(symbol)]
        self._backfill_days = max(30, min(int(days), 365))
        channels: Dict[str, Dict[str, Any]] = {}
        self.repo.update_backfill_job(job_id, status="running", progress=1, started_at=utc_naive_now(), channel_status=channels)
        source_map = {item["source_key"]: item for item in self.repo.list_sources()}
        groups = [
            ("tushare_market", ["tushare.market"]),
            ("tushare_fundamental", ["tushare.company_profile", "tushare.fundamentals", "tushare.company"]),
            ("tushare_capital", ["tushare.capital", "tushare.technical", "tushare.ownership", "tushare.market_activity"]),
            ("tushare_research", ["tushare.institution", "tushare.research_pdf"]),
            ("tushare_news", [key for key in source_map if key.startswith("tushare.news.")] + ["tushare.long_news"]),
            ("cninfo", ["cninfo.announcements"]),
            ("zsxq", ["zsxq.essays"]),
            ("tianyancha", ["tianyancha.enterprise"]),
            ("stock_comments", ["eastmoney.guba_posts"]),
        ]
        try:
            self._save_backfill_daily(self.watchlist()[0], self._backfill_days)
            for index, (channel, source_keys) in enumerate(groups, start=1):
                created = updated = received = 0
                errors: List[str] = []
                for key in source_keys:
                    source = source_map.get(key)
                    if not source or not source.get("enabled", True):
                        continue
                    if key == "cninfo.announcements":
                        end = utc_naive_now().date()
                        rows = self.cninfo.fetch(start_date=end - timedelta(days=self._backfill_days), end_date=end,
                                                 symbols=self.watchlist(), max_pages=100)
                        saved = self.repo.upsert_events(self._announcement_events(source, rows))
                        result = {**saved, "status": "success"}
                    else:
                        result = self._sync_one(source)
                    created += int(result.get("created") or 0)
                    updated += int(result.get("updated") or 0)
                    received += int(result.get("received") or 0)
                    if result.get("status") == "failed":
                        errors.append(str(result.get("error") or "unknown error"))
                status = "failed" if errors and not (created or updated or received) else "partial" if errors else "completed" if (created or updated or received) else "empty"
                channels[channel] = {"status": status, "created": created, "updated": updated,
                                     "received": received, "error": "; ".join(errors)[:500] or None,
                                     "note": None}
                self.repo.update_backfill_job(job_id, progress=round(index * 100 / len(groups)), channel_status=channels)
            channels["external_feeds"] = {"status": "not_supported", "created": 0,
                                            "note": "RSS/NewsNow 不提供可靠的半年历史接口，仅由增量任务持续更新"}
            final_status = "partial" if any(item["status"] in {"failed", "partial"} for item in channels.values()) else "completed"
            return self.repo.update_backfill_job(job_id, status=final_status, progress=100,
                                                 completed_at=utc_naive_now(), channel_status=channels)
        except Exception as exc:
            return self.repo.update_backfill_job(job_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}",
                                                 completed_at=utc_naive_now(), channel_status=channels)
        finally:
            self._watchlist_override = None
            self._backfill_days = None

    def _save_backfill_daily(self, symbol: str, days: int) -> int:
        end = utc_naive_now()
        rows = self.tushare.query("daily", params={"ts_code": symbol,
            "start_date": (end - timedelta(days=days)).strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")})["rows"]
        frame = pd.DataFrame([{
            "date": self._parse_date(row.get("trade_date")).date().isoformat(), "open": row.get("open"), "high": row.get("high"),
            "low": row.get("low"), "close": row.get("close"), "volume": row.get("vol"),
            "amount": (float(row.get("amount") or 0) * 1000), "pct_chg": row.get("pct_chg"),
        } for row in rows])
        return DatabaseManager.get_instance().save_daily_data(frame, symbol, "tushare:daily") if not frame.empty else 0

    def _history_window(self, default: int) -> int:
        return max(default, int(self._backfill_days or 0))

    def _sync_sources(self, sources: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        ordered = sorted(sources, key=lambda item: (int(item.get("poll_interval_seconds") or 300), item["source_key"]))
        max_workers = max(1, min(int(os.getenv("INVESTMENT_MONITOR_MAX_WORKERS", "16")), 24, len(ordered) or 1))
        if max_workers == 1 or len(ordered) <= 1:
            results = [self._sync_one(source) for source in ordered]
            return self._sync_summary(results, max_workers=1)

        # Fetch independent upstreams concurrently so a slow low-frequency API
        # cannot delay news/ZSXQ refreshes. Persist on this thread to keep SQLite
        # writes serialized even while WAL readers continue uninterrupted.
        for source in ordered:
            started = utc_naive_now()
            self.repo.update_source_status(source["source_key"], status="running", started_at=started)

        results = []
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="monitor-source") as executor:
            futures = {executor.submit(self._timed_source_fetch, source): source for source in ordered}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    value, duration_ms, fetch_error = future.result()
                except Exception as exc:  # noqa: BLE001 - one source must not stop other refreshes.
                    value, duration_ms, fetch_error = None, 0, exc
                # Persist each completed adapter immediately. A slow announcement
                # or enterprise API must never hold already-fetched news in memory
                # until the whole polling cycle finishes.
                key = source["source_key"]
                result = self._persist_fetched_source(
                    source, value=value, duration_ms=duration_ms, fetch_error=fetch_error,
                )
                results.append(result)
        results.sort(key=lambda item: item["source_key"])
        return self._sync_summary(results, max_workers=max_workers)

    def _persist_fetched_source(
        self, source: Dict[str, Any], *, value: Optional[List[Dict[str, Any]]],
        duration_ms: int, fetch_error: Optional[Exception],
    ) -> Dict[str, Any]:
        """Serialize one finished adapter into SQLite without waiting for its peers."""
        key = source["source_key"]
        if fetch_error is not None:
            if isinstance(fetch_error, MonitoringSourceNotConfigured):
                message = str(fetch_error)[:500]
                self.repo.update_source_status(
                    key, status="not_configured", error=message, received_count=0,
                    created_count=0, updated_count=0, duration_ms=duration_ms,
                )
                return {"source_key": key, "status": "not_configured", "created": 0,
                        "updated": 0, "received": 0, "duration_ms": duration_ms, "error": message}
            safe_error = f"{type(fetch_error).__name__}: {str(fetch_error)[:500]}"
            logger.warning("[investment-monitor] source %s failed: %s", key, safe_error)
            self.repo.update_source_status(
                key, status="failed", error=safe_error, received_count=0,
                created_count=0, updated_count=0, duration_ms=duration_ms,
            )
            return {"source_key": key, "status": "failed", "created": 0, "updated": 0,
                    "received": 0, "duration_ms": duration_ms, "error": safe_error}
        try:
            saved = self.repo.upsert_events(value or [])
            self.repo.update_source_status(
                key, status="success", item_count=saved["created"],
                received_count=saved["received"], created_count=saved["created"],
                updated_count=saved["updated"], duration_ms=duration_ms,
                success_at=utc_naive_now(),
            )
            return {"source_key": key, "status": "success", "duration_ms": duration_ms, **saved}
        except Exception as exc:  # noqa: BLE001 - persistence failure remains source-scoped.
            safe_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            logger.warning("[investment-monitor] source %s persistence failed: %s", key, safe_error)
            self.repo.update_source_status(
                key, status="failed", error=safe_error, received_count=0,
                created_count=0, updated_count=0, duration_ms=duration_ms,
            )
            return {"source_key": key, "status": "failed", "created": 0, "updated": 0,
                    "received": 0, "duration_ms": duration_ms, "error": safe_error}

    def _timed_source_fetch(self, source: Dict[str, Any]) -> tuple[Optional[List[Dict[str, Any]]], int, Optional[Exception]]:
        """Measure one adapter's upstream work without including other workers' queue time."""
        started = time.monotonic()
        try:
            return self._events_for_source(source), round((time.monotonic() - started) * 1000), None
        except Exception as exc:  # noqa: BLE001 - returned to the serialized status writer.
            return None, round((time.monotonic() - started) * 1000), exc

    @staticmethod
    def _sync_summary(results: Sequence[Dict[str, Any]], *, max_workers: int) -> Dict[str, Any]:
        totals = Counter()
        for result in results:
            totals["sources"] += 1
            totals[result["status"]] += 1
            totals["created"] += int(result.get("created") or 0)
            totals["updated"] += int(result.get("updated") or 0)
        totals["max_workers"] = max_workers
        return {"totals": dict(totals), "sources": list(results)}

    def _sync_one(self, source: Dict[str, Any]) -> Dict[str, Any]:
        key = source["source_key"]
        started = utc_naive_now()
        monotonic_started = time.monotonic()
        self.repo.update_source_status(key, status="running", started_at=started)
        try:
            events = self._events_for_source(source)
            saved = self.repo.upsert_events(events)
            duration_ms = round((time.monotonic() - monotonic_started) * 1000)
            self.repo.update_source_status(
                key,
                status="success",
                item_count=saved["created"],
                received_count=saved["received"], created_count=saved["created"],
                updated_count=saved["updated"], duration_ms=duration_ms,
                success_at=utc_naive_now(),
            )
            return {"source_key": key, "status": "success", "duration_ms": duration_ms, **saved}
        except MonitoringSourceNotConfigured as exc:
            duration_ms = round((time.monotonic() - monotonic_started) * 1000)
            message = str(exc)[:500]
            self.repo.update_source_status(
                key, status="not_configured", error=message, received_count=0,
                created_count=0, updated_count=0, duration_ms=duration_ms,
            )
            return {"source_key": key, "status": "not_configured", "created": 0,
                    "updated": 0, "received": 0, "duration_ms": duration_ms, "error": message}
        except Exception as exc:  # noqa: BLE001 - one adapter must not stop other sources.
            safe_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            duration_ms = round((time.monotonic() - monotonic_started) * 1000)
            logger.warning("[investment-monitor] source %s failed: %s", key, safe_error)
            self.repo.update_source_status(
                key, status="failed", error=safe_error, received_count=0,
                created_count=0, updated_count=0, duration_ms=duration_ms,
            )
            return {"source_key": key, "status": "failed", "created": 0, "updated": 0,
                    "received": 0, "duration_ms": duration_ms, "error": safe_error}

    def _events_for_source(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        key = source["source_key"]
        if key == "realtime.quotes":
            return self._realtime_quote_events(source)
        if key == "tushare.market":
            return self._tushare_market_events(source)
        if key.startswith("tushare.news."):
            return self._tushare_news_events(source)
        if key == "tushare.institution":
            return self._tushare_institution_events(source)
        if key == "tushare.market_activity":
            return self._tushare_market_activity_events(source)
        if key == "tushare.market_themes":
            return self._tushare_market_theme_events(source)
        if key == "tushare.company":
            return self._tushare_company_events(source)
        if key == "tushare.company_profile":
            return self._tushare_company_profile_events(source)
        if key == "tushare.fundamentals":
            return self._tushare_fundamental_events(source)
        if key == "tushare.capital":
            return self._tushare_capital_events(source)
        if key == "tushare.technical":
            return self._tushare_technical_events(source)
        if key == "tushare.ownership":
            return self._tushare_ownership_events(source)
        if key == "tushare.research_pdf":
            return self._tushare_research_report_events(source)
        if key == "tushare.long_news":
            return self._tushare_long_news_events(source)
        if key == "tianyancha.enterprise":
            return self._tianyancha_events(source)
        if key == "cninfo.announcements":
            return self._announcement_events(source, self.cninfo.fetch_recent_market(days=2, max_pages=30))
        if key == "zsxq.essays":
            return self._zsxq_events(source)
        if key == "akshare.stock_comments":
            return self._akshare_stock_comment_events(source)
        if key == "eastmoney.guba_posts":
            return self._eastmoney_guba_events(source)
        if key == "feeds.intelligence":
            return self._feed_events(source)
        if source["adapter_type"] in {"api", "webhook", "mcp"}:
            return []
        raise InvestmentMonitorError(f"unsupported monitoring adapter: {source['adapter_type']}")

    def _announcement_events(self, source: Dict[str, Any], rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events = []
        for row in rows:
            code = str(row.get("code") or "")
            symbol = self._canonical_ts_code(code) if code else ""
            title = str(row.get("title") or "上市公司公告")
            categories = list(row.get("category_names") or [])
            events.append(self._base_event(
                source, external_id=str(row["announcement_id"]), event_type="company_announcement",
                perspective="company", title=title,
                summary=f"{row.get('name') or symbol}发布公告。文件类型 {row.get('file_type') or 'PDF'}，大小 {row.get('size_kb') or '—'} KB。",
                symbols=[symbol] if symbol else [], sentiment=self._sentiment(title),
                importance=self._text_importance(title, [symbol] if symbol else []),
                confidence=1.0, event_at=row["announcement_at"],
                tags=["公司公告", *categories], actors=[str(row.get("name") or "")],
                metrics={key: value for key, value in row.items() if key not in {"raw", "announcement_at", "title", "pdf_url"}},
                url=row.get("pdf_url"), raw=row.get("raw") or row,
            ))
        return events

    def _realtime_quote_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        local_now = utc_naive_now().replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
        if not (local_now.weekday() < 5 and (local_now.hour == 9 and local_now.minute >= 25 or local_now.hour == 10 and local_now.minute <= 15)):
            return []
        events = []
        for ts_code in self.watchlist():
            code = ts_code.split(".", 1)[0]
            quote = self.stock_service.get_realtime_quote(code)
            if not quote:
                continue
            event_at = self._parse_datetime(quote.get("update_time")) or utc_naive_now()
            local_at = event_at.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
            if not (local_at.weekday() < 5 and (local_at.hour == 9 and local_at.minute >= 25 or local_at.hour == 10 and local_at.minute <= 15)):
                continue
            open_price = self._number(quote.get("open") or quote.get("current_price"))
            previous_close = self._number(quote.get("prev_close"))
            change = ((open_price - previous_close) * 100 / previous_close) if previous_close else self._number(quote.get("change_percent"))
            sentiment = "bullish" if change >= 1 else "bearish" if change <= -1 else "neutral"
            open_at = local_at.replace(hour=9, minute=30, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
            opening = {
                "stock_code": code, "stock_name": quote.get("stock_name") or ts_code,
                "open": open_price, "prev_close": previous_close or None,
                "open_change_pct": round(change, 4), "trade_date": local_at.strftime("%Y%m%d"),
            }
            events.append(self._base_event(
                source,
                external_id=f"{ts_code}:{local_at.strftime('%Y%m%d')}:open",
                event_type="market_open_snapshot",
                perspective="investor",
                title=f"{quote.get('stock_name') or ts_code} 开盘 {open_price}（{change:+.2f}%）",
                summary=f"开盘价 {open_price}，前收盘 {previous_close or '—'}。当日收盘由 Tushare 日线快照单独记录。",
                symbols=[ts_code],
                sentiment=sentiment,
                importance=min(90, 45 + int(abs(change) * 8)),
                event_at=open_at,
                tags=["开盘快照", "上涨" if change > 0 else "下跌" if change < 0 else "平盘"],
                metrics=opening,
                raw=opening,
            ))
        return events

    def _tushare_market_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = []
        start = (utc_naive_now() - timedelta(days=self._history_window(10))).strftime("%Y%m%d")
        end = utc_naive_now().strftime("%Y%m%d")
        for ts_code in self.equity_watchlist():
            daily = self.tushare.query("daily", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            basic = self.tushare.query("daily_basic", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            if not daily:
                continue
            quote = daily[0]
            valuation = basic[0] if basic else {}
            trade_date = str(quote.get("trade_date") or end)
            pct = float(quote.get("pct_chg") or 0)
            metrics = {**quote, **{f"valuation_{key}": value for key, value in valuation.items() if key not in {"ts_code", "trade_date", "close"}}}
            events.append(self._base_event(
                source,
                external_id=f"{ts_code}:{trade_date}",
                event_type="market_snapshot",
                perspective="investor",
                title=f"{self._stock_name(ts_code)} 收盘 {quote.get('close')}（{pct:+.2f}%）",
                summary=f"成交额 {quote.get('amount')}，PE(TTM) {valuation.get('pe_ttm')}，PB {valuation.get('pb')}，换手率 {valuation.get('turnover_rate')}%。",
                symbols=[ts_code],
                sentiment="bullish" if pct >= 1 else "bearish" if pct <= -1 else "neutral",
                importance=min(85, 45 + int(abs(pct) * 7)),
                event_at=self._parse_date(trade_date),
                tags=["日线行情", "估值", "成交"],
                metrics=metrics,
                raw={"daily": quote, "daily_basic": valuation},
            ))
        return events

    def _tushare_news_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        now = datetime.now()
        if self._backfill_days:
            start = now - timedelta(days=self._backfill_days)
        else:
            live_overlap = max(5, min(int(os.getenv("INVESTMENT_MONITOR_NEWS_LIVE_OVERLAP_MINUTES", "10")), 60))
            recovery_overlap = max(
                live_overlap,
                min(int(os.getenv("INVESTMENT_MONITOR_NEWS_OVERLAP_MINUTES", "360")), 1440),
            )
            last_success_raw = source.get("last_success_at")
            last_success = self._utc_iso_to_market_naive(last_success_raw)
            # Normal cycles only reread a small overlap for late/unsorted items.
            # After downtime the cursor automatically expands to cover the gap,
            # capped by the recovery window so requests stay bounded.
            recovery_start = now - timedelta(minutes=recovery_overlap)
            cursor_start = last_success - timedelta(minutes=live_overlap) if last_success else recovery_start
            start = max(recovery_start, cursor_start)
        src = str(source.get("config", {}).get("src") or source["provider"])
        rows: List[Dict[str, Any]] = []
        window_start = start
        while window_start < now:
            window_end = min(window_start + timedelta(days=30), now)
            rows.extend(self._paged_tushare_rows("news", params={
                "src": src,
                "start_date": window_start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_date": window_end.strftime("%Y-%m-%d %H:%M:%S"),
            }, limit=1500, max_pages=20 if self._backfill_days else 1))
            window_start = window_end
        events = []
        names = {symbol: self._stock_name(symbol) for symbol in self.watchlist()}
        for row in rows:
            content = str(row.get("content") or "").strip()
            title = str(row.get("title") or "").strip() or content[:80]
            text = f"{title} {content}"
            symbols = self._symbols_for_text(text, names)
            if self._backfill_days:
                symbols = [symbol for symbol in symbols if symbol in names]
            if self._backfill_days and not symbols:
                continue
            perspective = "company" if any(word in text for word in _COMPANY_WORDS) else "investor"
            sentiment = self._sentiment(text)
            event_at = self._parse_datetime(row.get("datetime")) or utc_naive_now()
            digest = hashlib.sha256(f"{src}|{row.get('datetime')}|{title}|{content}".encode()).hexdigest()[:32]
            events.append(self._base_event(
                source,
                external_id=digest,
                event_type="news",
                perspective=perspective,
                title=title,
                summary=content[:3000],
                symbols=symbols,
                sentiment=sentiment,
                importance=self._text_importance(text, symbols),
                event_at=event_at,
                tags=self._text_tags(text),
                actors=[src],
                raw=row,
            ))
        return events

    def _tushare_institution_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = []
        start = (utc_naive_now() - timedelta(days=self._history_window(30))).strftime("%Y%m%d")
        end = utc_naive_now().strftime("%Y%m%d")
        for ts_code in self.equity_watchlist():
            report_rows = self.tushare.query("report_rc", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for row in report_rows:
                key = "|".join(str(row.get(field) or "") for field in ("report_date", "report_title", "org_name", "author_name"))
                grouped[key].append(row)
            for key, rows in grouped.items():
                row = rows[0]
                rating = str(row.get("rating") or "")
                targets = [value for item in rows for value in (item.get("min_price"), item.get("max_price")) if value is not None]
                metrics = {
                    "rating": rating,
                    "target_price_min": min(targets) if targets else None,
                    "target_price_max": max(targets) if targets else None,
                    "forecasts": [{field: item.get(field) for field in ("quarter", "eps", "pe", "np", "op_rt", "roe")} for item in rows],
                }
                events.append(self._base_event(
                    source,
                    external_id=hashlib.sha256(key.encode()).hexdigest()[:32],
                    event_type="institution_forecast",
                    perspective="institution",
                    title=str(row.get("report_title") or f"{self._stock_name(ts_code)} 机构盈利预测"),
                    summary=f"{row.get('org_name') or '机构'}，评级 {rating or '未评级'}，预测期 {', '.join(str(item.get('quarter') or '') for item in rows[:5])}。",
                    symbols=[ts_code],
                    sentiment="bullish" if any(word in rating for word in ("买入", "增持", "推荐")) else "bearish" if "卖出" in rating else "neutral",
                    importance=75 if rating else 65,
                    event_at=self._parse_date(row.get("report_date")),
                    tags=["机构预测", rating or "未评级"],
                    actors=[str(row.get("org_name") or ""), str(row.get("author_name") or "")],
                    metrics=metrics,
                    raw=rows,
                ))
            surveys = self.tushare.query("stk_surv", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            for row in surveys:
                external = "|".join(str(row.get(field) or "") for field in ("ts_code", "surv_date", "rece_org", "fund_visitors"))
                events.append(self._base_event(
                    source,
                    external_id=hashlib.sha256(external.encode()).hexdigest()[:32],
                    event_type="institution_survey",
                    perspective="institution",
                    title=f"{self._stock_name(ts_code)} 机构调研：{row.get('rece_org') or row.get('fund_visitors') or '机构'}",
                    summary=f"接待方式 {row.get('rece_mode') or '-'}，地点 {row.get('rece_place') or '-'}，公司接待 {row.get('comp_rece') or '-'}。",
                    symbols=[ts_code], sentiment="neutral", importance=70,
                    event_at=self._parse_date(row.get("surv_date")), tags=["机构调研"],
                    actors=[str(row.get("rece_org") or ""), str(row.get("fund_visitors") or "")], raw=row,
                ))
        return events

    def _tushare_market_activity_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Persist exchange activity and broker-selection records without fabricating interpretations."""
        events: List[Dict[str, Any]] = []
        now = utc_naive_now()
        end = now.strftime("%Y%m%d")
        start = (now - timedelta(days=self._history_window(120))).strftime("%Y%m%d")
        symbols = set(self.equity_watchlist())
        trade_date = end
        if symbols:
            daily = self.tushare.query("daily", params={
                "ts_code": next(iter(symbols)), "start_date": (now - timedelta(days=10)).strftime("%Y%m%d"),
                "end_date": end,
            })["rows"]
            if daily:
                trade_date = str(daily[0].get("trade_date") or end)

        # 龙虎榜 is a market-wide exchange disclosure. Persisting only the
        # watchlist made the capital channel look connected while discarding
        # nearly all rows, so the normal worker now materializes the full day.
        top_rows = self.tushare.query("top_list", params={"trade_date": trade_date})["rows"]
        inst_rows = self.tushare.query("top_inst", params={"trade_date": trade_date})["rows"]
        limit_rows = [row for row in self.tushare.query("limit_list_d", params={"trade_date": trade_date})["rows"]
                      if row.get("ts_code") in symbols]
        events.extend(self._dragon_tiger_events(source, trade_date, top_rows, inst_rows))
        for row in limit_rows:
            code = str(row.get("ts_code"))
            direction = str(row.get("limit") or "")
            events.append(self._base_event(
                source, external_id=f"limit:{trade_date}:{code}", event_type="price_limit",
                perspective="investor", title=f"{self._stock_name(code)} {direction or '涨跌停异动'}",
                summary=f"收盘 {row.get('close')}，涨跌幅 {row.get('pct_chg')}%，封单金额 {row.get('fd_amount')}，开板次数 {row.get('open_times')}。",
                symbols=[code], sentiment="bearish" if "跌" in direction else "bullish", importance=86,
                event_at=self._parse_date(trade_date), tags=["涨跌停", direction], metrics=row, raw=row,
            ))

        month = trade_date[:6]
        recommendations = [row for row in self.tushare.query("broker_recommend", params={"month": month})["rows"]
                           if row.get("ts_code") in symbols]
        for row in recommendations:
            code = str(row.get("ts_code"))
            broker = str(row.get("broker") or "券商")
            events.append(self._base_event(
                source, external_id=f"broker_pick:{month}:{code}:{broker}", event_type="broker_recommendation",
                perspective="institution", title=f"{broker}月度金股：{self._stock_name(code)}",
                summary=f"{month} 月券商金股名单记录，证券代码 {code}。", symbols=[code],
                sentiment="bullish", importance=73, event_at=self._parse_date(month + "01"),
                tags=["券商金股"], actors=[broker], metrics=row, raw=row,
            ))
        for code in symbols:
            for row in self.tushare.query("block_trade", params={
                "ts_code": code, "start_date": start, "end_date": end,
            })["rows"]:
                external = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:28]
                events.append(self._base_event(
                    source, external_id=f"block:{external}", event_type="block_trade", perspective="institution",
                    title=f"{self._stock_name(code)} 大宗交易",
                    summary=f"成交价 {row.get('price')}，成交量 {row.get('vol')}，成交金额 {row.get('amount')}，买方 {row.get('buyer') or '-'}，卖方 {row.get('seller') or '-'}。",
                    symbols=[code], sentiment="neutral", importance=72,
                    event_at=self._parse_date(row.get("trade_date")), tags=["大宗交易"],
                    actors=[str(row.get("buyer") or ""), str(row.get("seller") or "")], metrics=row, raw=row,
                ))
            for row in self.tushare.query("suspend_d", params={
                "ts_code": code, "start_date": (now - timedelta(days=365)).strftime("%Y%m%d"), "end_date": end,
            })["rows"]:
                external = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:28]
                events.append(self._base_event(
                    source, external_id=f"suspend:{external}", event_type="suspension", perspective="company",
                    title=f"{self._stock_name(code)} 停复牌记录",
                    summary=f"停牌时间 {row.get('suspend_time') or row.get('trade_date') or '-'}，复牌时间 {row.get('resume_time') or row.get('resume_date') or '-'}，原因 {row.get('suspend_reason') or '-'}。",
                    symbols=[code], sentiment="neutral", importance=78,
                    event_at=self._parse_date(row.get("trade_date") or row.get("suspend_date")),
                    tags=["停复牌"], actors=[self._stock_name(code)], metrics=row, raw=row,
                ))
        return events

    def _dragon_tiger_events(
        self, source: Dict[str, Any], trade_date: str,
        top_rows: Sequence[Dict[str, Any]], inst_rows: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        names = {
            str(row.get("ts_code") or ""): str(row.get("name") or row.get("ts_code") or "")
            for row in top_rows
        }
        for row in top_rows:
            code = self._canonical_ts_code(row.get("ts_code"))
            reason = str(row.get("reason") or "交易异动")
            identity = hashlib.sha256(f"{trade_date}|{code}|{reason}".encode()).hexdigest()[:20]
            events.append(self._base_event(
                source, external_id=f"top_list:{identity}", event_type="dragon_tiger",
                perspective="institution", title=f"{row.get('name') or code} 龙虎榜：{reason}",
                summary=(
                    f"净额 {row.get('net_amount')} 元，买入 {row.get('l_buy')} 元，"
                    f"卖出 {row.get('l_sell')} 元，龙虎榜成交占比 {row.get('amount_rate')}%。"
                ),
                symbols=[code], sentiment="bullish" if self._number(row.get("net_amount")) > 0 else "bearish",
                importance=82, event_at=self._parse_date(trade_date), tags=["龙虎榜", "交易所披露"],
                actors=["沪深交易所"], metrics=row, raw=row,
            ))
        for row in inst_rows:
            code = self._canonical_ts_code(row.get("ts_code"))
            reason = str(row.get("reason") or "交易异动")
            exalter = str(row.get("exalter") or "披露席位")
            side = str(row.get("side") or "")
            identity = hashlib.sha256(
                f"{trade_date}|{code}|{reason}|{exalter}|{side}".encode(),
            ).hexdigest()[:24]
            events.append(self._base_event(
                source, external_id=f"top_inst:{identity}", event_type="institution_seat",
                perspective="institution", title=f"{names.get(code) or code} 席位：{exalter}",
                summary=(
                    f"买卖类型 {side or '-'}，买入 {row.get('buy')} 元，"
                    f"卖出 {row.get('sell')} 元，净额 {row.get('net_buy')} 元。"
                ),
                symbols=[code], sentiment="bullish" if self._number(row.get("net_buy")) > 0 else "bearish",
                importance=80, event_at=self._parse_date(trade_date), tags=["机构席位", "龙虎榜"],
                actors=[exalter], metrics=row, raw=row,
            ))
        return events

    def _tushare_market_theme_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Persist market-wide THS theme flows and limit-pool structure as factual snapshots."""
        now = utc_naive_now()
        end = now.strftime("%Y%m%d")
        trade_date = end
        watchlist = self.watchlist()
        if watchlist:
            daily = self.tushare.query("daily", params={
                "ts_code": watchlist[0],
                "start_date": (now - timedelta(days=10)).strftime("%Y%m%d"),
                "end_date": end,
            })["rows"]
            if daily:
                trade_date = str(daily[0].get("trade_date") or end)

        concept_rows = self.tushare.query("moneyflow_cnt_ths", params={"trade_date": trade_date})["rows"]
        industry_rows = self.tushare.query("moneyflow_ind_ths", params={"trade_date": trade_date})["rows"]

        def ranked(rows: Sequence[Dict[str, Any]], *, reverse: bool) -> List[Dict[str, Any]]:
            return sorted(rows, key=lambda row: self._number(row.get("net_amount")), reverse=reverse)[:10]

        events: List[Dict[str, Any]] = []
        if concept_rows or industry_rows:
            concept_inflow = ranked(concept_rows, reverse=True)
            concept_outflow = ranked(concept_rows, reverse=False)
            industry_inflow = ranked(industry_rows, reverse=True)
            industry_outflow = ranked(industry_rows, reverse=False)

            def labels(rows: Sequence[Dict[str, Any]]) -> str:
                return "、".join(
                    str(row.get("name") or row.get("concept_name") or row.get("industry") or row.get("ts_name") or "-")
                    for row in rows[:5]
                )

            events.append(self._base_event(
                source,
                external_id=f"theme_flow:{trade_date}",
                event_type="market_theme_flow",
                perspective="investor",
                title=f"{trade_date} 题材与行业资金流结构",
                summary=(
                    f"概念净流入前列：{labels(concept_inflow) or '-'}；"
                    f"行业净流入前列：{labels(industry_inflow) or '-'}。"
                    "该事件仅记录同花顺口径的市场资金流事实，不构成投资建议。"
                ),
                symbols=[], sentiment="neutral", importance=72,
                event_at=self._parse_date(trade_date), tags=["题材资金", "行业资金", "同花顺口径"],
                actors=["同花顺"],
                metrics={
                    "trade_date": trade_date,
                    "concept": {"inflow_top10": concept_inflow, "outflow_top10": concept_outflow},
                    "industry": {"inflow_top10": industry_inflow, "outflow_top10": industry_outflow},
                },
                raw={"concept": concept_rows, "industry": industry_rows},
            ))

        pools: Dict[str, List[Dict[str, Any]]] = {}
        for pool_name in ("涨停池", "炸板池", "跌停池"):
            pools[pool_name] = self.tushare.query("limit_list_ths", params={
                "trade_date": trade_date, "limit_type": pool_name,
            })["rows"]
        if any(pools.values()):
            reasons = Counter(
                str(row.get("tag") or row.get("reason") or row.get("industry") or "").strip()
                for rows in pools.values() for row in rows
                if str(row.get("tag") or row.get("reason") or row.get("industry") or "").strip()
            )
            leading_reasons = [name for name, _ in reasons.most_common(8)]
            counts = {name: len(rows) for name, rows in pools.items()}
            events.append(self._base_event(
                source,
                external_id=f"limit_pool:{trade_date}",
                event_type="limit_pool",
                perspective="investor",
                title=f"{trade_date} 涨跌停与炸板结构",
                summary=(
                    f"涨停池 {counts['涨停池']} 家，炸板池 {counts['炸板池']} 家，"
                    f"跌停池 {counts['跌停池']} 家；高频题材/原因：{'、'.join(leading_reasons[:5]) or '-'}。"
                    "该事件仅汇总公开行情结构，不构成投资建议。"
                ),
                symbols=[], sentiment="neutral", importance=76,
                event_at=self._parse_date(trade_date), tags=["涨停池", "炸板池", "跌停池"],
                actors=["同花顺"],
                metrics={
                    "trade_date": trade_date, "counts": counts,
                    "leading_reasons": leading_reasons,
                    "pools": {name: rows[:100] for name, rows in pools.items()},
                },
                raw=pools,
            ))
        return events

    def _tushare_company_profile_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Company registry, shareholder, governance, dividend and audit facts."""
        events: List[Dict[str, Any]] = []
        now = utc_naive_now()
        end = now.strftime("%Y%m%d")
        start = (now - timedelta(days=730)).strftime("%Y%m%d")
        for code in self.equity_watchlist():
            company_rows = self.tushare.query("stock_company", params={"ts_code": code})["rows"]
            if company_rows:
                row = company_rows[0]
                events.append(self._base_event(
                    source, external_id=f"company:{code}:{hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:20]}",
                    event_type="company_profile", perspective="company",
                    title=f"{row.get('com_name') or self._stock_name(code)} 公司资料",
                    summary=f"主营业务：{row.get('main_business') or '-'}；注册资本 {row.get('reg_capital') or '-'} 万元；员工 {row.get('employees') or '-'} 人；办公地 {row.get('office') or '-'}。",
                    symbols=[code], sentiment="neutral", importance=64, event_at=now,
                    tags=["公司资料", "主营业务"], actors=[str(row.get("com_name") or "")], metrics=row, raw=row,
                ))
            managers = self.tushare.query("stk_managers", params={"ts_code": code})["rows"]
            if managers:
                active = [row for row in managers if not str(row.get("end_date") or "").strip() or str(row.get("end_date")) >= end]
                selected = active or sorted(
                    managers, key=lambda row: str(row.get("begin_date") or row.get("ann_date") or ""), reverse=True,
                )[:20]
                selected = selected[:80]
                roster_names = "、".join(
                    f"{row.get('name') or '-'}（{row.get('title') or row.get('lev') or '-'}）"
                    for row in selected[:8]
                )
                roster_hash = hashlib.sha256(json.dumps(selected, sort_keys=True, default=str).encode()).hexdigest()[:20]
                latest_date = max(
                    (str(row.get("ann_date") or row.get("begin_date") or "") for row in selected), default=end,
                )
                events.append(self._base_event(
                    source, external_id=f"managers:{code}:{roster_hash}", event_type="executive_roster",
                    perspective="company", title=f"{self._stock_name(code)} 管理层与任职结构",
                    summary=f"当前或最近披露管理层 {len(selected)} 人：{roster_names or '-'}。",
                    symbols=[code], sentiment="neutral", importance=70,
                    event_at=self._parse_date(latest_date), tags=["管理层", "公司治理", "任职结构"],
                    actors=[str(row.get("name") or "") for row in selected[:10]],
                    metrics={"active_count": len(active), "records": selected}, raw=selected,
                ))
            holders = self.tushare.query("stk_holdernumber", params={
                "ts_code": code, "start_date": start, "end_date": end,
            })["rows"]
            for row in holders[:12]:
                events.append(self._base_event(
                    source, external_id=f"holder_num:{code}:{row.get('end_date')}", event_type="holder_number",
                    perspective="company", title=f"{self._stock_name(code)} 股东户数 {row.get('holder_num')}",
                    summary=f"报告期 {row.get('end_date')}，披露日 {row.get('ann_date')}，股东户数 {row.get('holder_num')}。",
                    symbols=[code], sentiment="neutral", importance=68,
                    event_at=self._parse_date(row.get("ann_date") or row.get("end_date")), tags=["股东户数"],
                    actors=[self._stock_name(code)], metrics=row, raw=row,
                ))
            for api_name, event_type, label in (
                ("top10_holders", "top_shareholders", "十大股东"),
                ("top10_floatholders", "top_float_shareholders", "十大流通股东"),
            ):
                rows = self.tushare.query(api_name, params={"ts_code": code, "start_date": start, "end_date": end})["rows"]
                if rows:
                    latest_period = max(str(row.get("end_date") or "") for row in rows)
                    latest = [row for row in rows if str(row.get("end_date") or "") == latest_period][:10]
                    names = "、".join(str(row.get("holder_name") or "") for row in latest[:4])
                    events.append(self._base_event(
                        source, external_id=f"{api_name}:{code}:{latest_period}", event_type=event_type,
                        perspective="company", title=f"{self._stock_name(code)} {latest_period} {label}",
                        summary=f"披露 {len(latest)} 名股东；前列股东包括 {names or '-'}。",
                        symbols=[code], sentiment="neutral", importance=70,
                        event_at=self._parse_date(latest[0].get("ann_date") or latest_period), tags=[label],
                        actors=[str(row.get("holder_name") or "") for row in latest],
                        metrics={"period": latest_period, "holders": latest}, raw=latest,
                    ))
            dividends = self.tushare.query("dividend", params={"ts_code": code})["rows"]
            for row in [item for item in dividends if str(item.get("ann_date") or "") >= start][:20]:
                external = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:28]
                events.append(self._base_event(
                    source, external_id=f"dividend:{external}", event_type="dividend", perspective="company",
                    title=f"{self._stock_name(code)} 分红方案：{row.get('div_proc') or '已披露'}",
                    summary=f"每股现金分红 {row.get('cash_div_tax')}，送股 {row.get('stk_bo_rate')}，转增 {row.get('stk_co_rate')}，除权除息日 {row.get('ex_date') or '-'}。",
                    symbols=[code], sentiment="bullish" if self._number(row.get("cash_div_tax")) > 0 else "neutral",
                    importance=72, event_at=self._parse_date(row.get("ann_date") or row.get("end_date")),
                    tags=["分红送股"], actors=[self._stock_name(code)], metrics=row, raw=row,
                ))
            rewards = self.tushare.query("stk_rewards", params={"ts_code": code})["rows"]
            if rewards:
                latest_period = max(str(row.get("end_date") or "") for row in rewards)
                latest = [row for row in rewards if str(row.get("end_date") or "") == latest_period][:50]
                events.append(self._base_event(
                    source, external_id=f"rewards:{code}:{latest_period}", event_type="executive_rewards",
                    perspective="company", title=f"{self._stock_name(code)} {latest_period} 董监高薪酬与持股",
                    summary=f"披露 {len(latest)} 名董监高薪酬/持股记录。",
                    symbols=[code], sentiment="neutral", importance=62,
                    event_at=self._parse_date(latest[0].get("ann_date") or latest_period), tags=["董监高", "薪酬持股"],
                    actors=[str(row.get("name") or "") for row in latest[:10]], metrics={"records": latest}, raw=latest,
                ))
            for row in self.tushare.query("fina_audit", params={
                "ts_code": code, "start_date": start, "end_date": end,
            })["rows"][:6]:
                external = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:28]
                result = str(row.get("audit_result") or "")
                events.append(self._base_event(
                    source, external_id=f"audit:{external}", event_type="audit_opinion", perspective="institution",
                    title=f"{self._stock_name(code)} 审计意见：{result or '已披露'}",
                    summary=f"审计机构 {row.get('audit_agency') or '-'}，签字会计师 {row.get('audit_sign') or '-'}，审计费用 {row.get('audit_fees') or '-'}。",
                    symbols=[code], sentiment="bearish" if result and "标准无保留" not in result else "neutral",
                    importance=82, event_at=self._parse_date(row.get("ann_date") or row.get("end_date")),
                    tags=["审计意见"], actors=[str(row.get("audit_agency") or "")], metrics=row, raw=row,
                ))
        return events

    def _tushare_company_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = []
        start = (utc_naive_now() - timedelta(days=self._history_window(180))).strftime("%Y%m%d")
        end = utc_naive_now().strftime("%Y%m%d")
        endpoints = [
            ("forecast", "earnings_forecast", 82, "业绩预告"),
            ("express", "earnings_express", 85, "业绩快报"),
            ("repurchase", "repurchase", 78, "股份回购"),
            ("stk_holdertrade", "holder_trade", 76, "股东增减持"),
            ("share_float", "share_unlock", 72, "限售解禁"),
        ]
        for ts_code in self.equity_watchlist():
            for api_name, event_type, importance, label in endpoints:
                rows = self.tushare.query(api_name, params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
                for row in rows:
                    date_value = row.get("ann_date") or row.get("end_date") or row.get("float_date") or end
                    external = json.dumps({key: row.get(key) for key in sorted(row)}, ensure_ascii=False, default=str)
                    text = external
                    events.append(self._base_event(
                        source,
                        external_id=f"{api_name}:{hashlib.sha256(external.encode()).hexdigest()[:28]}",
                        event_type=event_type,
                        perspective="company",
                        title=f"{self._stock_name(ts_code)}：{label}",
                        summary=self._company_summary(api_name, row),
                        symbols=[ts_code],
                        sentiment=self._company_sentiment(api_name, row, text),
                        importance=importance,
                        event_at=self._parse_date(date_value),
                        tags=[label], actors=[self._stock_name(ts_code)], metrics=row, raw=row,
                    ))
        return events

    def _tushare_fundamental_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = []
        start = (utc_naive_now() - timedelta(days=550)).strftime("%Y%m%d")
        end = utc_naive_now().strftime("%Y%m%d")
        for ts_code in self.equity_watchlist():
            indicator = self.tushare.query("fina_indicator", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            income = self.tushare.query("income", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            balance = self.tushare.query("balancesheet", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            cashflow = self.tushare.query("cashflow", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            forecast = self.tushare.query("forecast", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            latest = indicator[0] if indicator else {}
            latest_income = income[0] if income else {}
            latest_balance = balance[0] if balance else {}
            latest_cash = cashflow[0] if cashflow else {}
            latest_forecast = forecast[0] if forecast else {}
            period = str(latest.get("end_date") or latest_income.get("end_date") or latest_forecast.get("end_date") or end)
            metrics = {"indicator": latest, "income": latest_income, "balance_sheet": latest_balance, "cashflow": latest_cash, "forecast": latest_forecast}
            summary = (
                f"报告期 {period}；营收同比 {latest.get('q_sales_yoy') or latest.get('revenue_yoy') or '-'}%，"
                f"归母净利同比 {latest.get('q_netprofit_yoy') or latest.get('netprofit_yoy') or '-'}%，"
                f"ROE {latest.get('roe') or latest.get('roe_waa') or '-'}%，毛利率 {latest.get('grossprofit_margin') or '-'}%，"
                f"经营现金流 {latest_cash.get('n_cashflow_act') or '-'}。"
            )
            yoy = self._number(latest.get("q_netprofit_yoy") or latest.get("netprofit_yoy"))
            events.append(self._base_event(
                source, external_id=f"{ts_code}:{period}", event_type="fundamental_snapshot", perspective="company",
                title=f"{self._stock_name(ts_code)} 财务质量快照", summary=summary, symbols=[ts_code],
                sentiment="bullish" if yoy > 10 else "bearish" if yoy < -10 else "neutral",
                importance=78 if latest_forecast else 70, event_at=self._parse_date(latest.get("ann_date") or latest_income.get("ann_date") or period),
                tags=["财务指标", "利润表", "资产负债表", "现金流", *( ["业绩预告"] if latest_forecast else [])],
                actors=[self._stock_name(ts_code)], metrics=metrics, raw=metrics,
            ))
        return events

    def _tushare_capital_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = []
        start = (utc_naive_now() - timedelta(days=self._history_window(45))).strftime("%Y%m%d")
        end = utc_naive_now().strftime("%Y%m%d")
        market_margin_rows = self.tushare.query(
            "margin", params={"start_date": (utc_naive_now() - timedelta(days=10)).strftime("%Y%m%d"), "end_date": end},
        )["rows"]
        market_margin_date = max((str(row.get("trade_date") or "") for row in market_margin_rows), default="")
        market_margin = [row for row in market_margin_rows if str(row.get("trade_date") or "") == market_margin_date]
        market_margin_total = {
            "trade_date": market_margin_date,
            "financing_balance": sum(self._number(row.get("rzye")) for row in market_margin),
            "securities_lending_balance": sum(self._number(row.get("rqye")) for row in market_margin),
            "margin_balance": sum(self._number(row.get("rzrqye")) for row in market_margin),
            "exchanges": market_margin,
        }
        for ts_code in self.equity_watchlist():
            perf = self.tushare.query("cyq_perf", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            margin = self.tushare.query("margin_detail", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            moneyflow = self.tushare.query("moneyflow", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            north = self.tushare.query("hk_hold", params={"ts_code": ts_code, "start_date": (utc_naive_now() - timedelta(days=180)).strftime("%Y%m%d"), "end_date": end})["rows"]
            latest = perf[0] if perf else {}
            trade_date = str(latest.get("trade_date") or (margin[0].get("trade_date") if margin else end))
            chips = self.tushare.query("cyq_chips", params={"ts_code": ts_code, "trade_date": trade_date})["rows"] if trade_date else []
            latest_margin = margin[0] if margin else {}
            latest_moneyflow = moneyflow[0] if moneyflow else {}
            latest_north = north[0] if north else {}
            metrics = {"chip_performance": latest, "chip_distribution": chips[:100], "margin": latest_margin,
                       "market_margin": market_margin_total, "moneyflow": latest_moneyflow, "northbound": latest_north}
            winner = self._number(latest.get("winner_rate"))
            summary = (
                f"{trade_date} 筹码平均成本 {latest.get('weight_avg') or '-'}，获利比例 {latest.get('winner_rate') or '-'}；"
                f"融资余额 {latest_margin.get('rzye') or '-'}，融资买入 {latest_margin.get('rzmre') or '-'}；"
                f"全市场两融余额 {market_margin_total.get('margin_balance') or '-'}；"
                f"主力净流入 {latest_moneyflow.get('net_mf_amount') or '-'}；最近披露北向持股比例 {latest_north.get('ratio') or '-'}。"
            )
            events.append(self._base_event(
                source, external_id=f"{ts_code}:{trade_date}", event_type="capital_chip_snapshot", perspective="investor",
                title=f"{self._stock_name(ts_code)} 筹码与资金快照", summary=summary, symbols=[ts_code],
                sentiment="bullish" if winner >= 0.7 else "bearish" if winner and winner <= 0.3 else "neutral",
                importance=72, event_at=self._parse_date(trade_date), tags=["筹码分布", "个股资金流", "融资融券", "北向持股"],
                metrics=metrics, raw=metrics,
            ))
        return events

    def _tushare_technical_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = []
        start = (utc_naive_now() - timedelta(days=self._history_window(30))).strftime("%Y%m%d")
        end = utc_naive_now().strftime("%Y%m%d")
        for ts_code in self.equity_watchlist():
            rows = self.tushare.query("stk_factor", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            nine_rows = self.tushare.query("stk_nineturn", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            if not rows:
                continue
            row = max(rows, key=lambda item: str(item.get("trade_date") or ""))
            nine = max(nine_rows, key=lambda item: str(item.get("trade_date") or "")) if nine_rows else {}
            trade_date = str(row.get("trade_date") or end)
            rsi = self._number(row.get("rsi_6") or row.get("rsi_bfq_6"))
            macd = self._number(row.get("macd") or row.get("macd_bfq"))
            sentiment = "bullish" if macd > 0 and rsi < 80 else "bearish" if macd < 0 and rsi > 20 else "neutral"
            summary = (
                f"收盘 {row.get('close') or row.get('close_qfq') or '-'}，RSI6 {rsi or '-'}，MACD {macd or '-'}，"
                f"KDJ K/D {row.get('kdj_k') or row.get('kdj_bfq_k') or '-'}/{row.get('kdj_d') or row.get('kdj_bfq_d') or '-'}；"
                f"九转上序列 {nine.get('up_count') if nine else '-'}、下序列 {nine.get('down_count') if nine else '-'}，"
                f"上九转完成 {'是' if nine.get('nine_up_turn') else '否'}、下九转完成 {'是' if nine.get('nine_down_turn') else '否'}。"
            )
            metrics = {**row, "nine_turn": nine}
            events.append(self._base_event(
                source, external_id=f"{ts_code}:{trade_date}", event_type="technical_factor", perspective="investor",
                title=f"{self._stock_name(ts_code)} 技术因子快照", summary=summary, symbols=[ts_code], sentiment=sentiment,
                importance=62 if nine.get("nine_up_turn") or nine.get("nine_down_turn") else 58,
                event_at=self._parse_date(trade_date), tags=["技术因子", "RSI", "MACD", "KDJ", "神奇九转"],
                metrics=metrics, raw={"factor": row, "nine_turn": nine},
            ))
        return events

    def _tushare_ownership_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = []
        start = (utc_naive_now() - timedelta(days=365)).strftime("%Y%m%d")
        end = utc_naive_now().strftime("%Y%m%d")
        for ts_code in self.equity_watchlist():
            pledge = self.tushare.query("pledge_stat", params={"ts_code": ts_code})["rows"]
            unlocks = self.tushare.query("share_float", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            trades = self.tushare.query("stk_holdertrade", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            repurchases = self.tushare.query("repurchase", params={"ts_code": ts_code, "start_date": start, "end_date": end})["rows"]
            latest_pledge = pledge[0] if pledge else {}
            snapshot_date = str(latest_pledge.get("end_date") or latest_pledge.get("ann_date") or end)
            metrics = {"pledge": latest_pledge, "share_float": unlocks[:30], "holder_trades": trades[:30], "repurchases": repurchases[:30]}
            summary = (
                f"质押比例 {latest_pledge.get('pledge_ratio') or '-'}%，近一年解禁 {len(unlocks)} 条、"
                f"股东增减持 {len(trades)} 条、回购 {len(repurchases)} 条。"
            )
            events.append(self._base_event(
                source, external_id=f"{ts_code}:{snapshot_date}", event_type="ownership_snapshot", perspective="company",
                title=f"{self._stock_name(ts_code)} 股权与资本动作", summary=summary, symbols=[ts_code],
                sentiment="mixed" if unlocks or trades else "neutral", importance=74 if unlocks or trades or repurchases else 60,
                event_at=self._parse_date(snapshot_date), tags=["股权质押", "限售解禁", "股东增减持", "股份回购"],
                actors=[self._stock_name(ts_code)], metrics=metrics, raw=metrics,
            ))
        return events

    def _tushare_research_report_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Scan report abstracts so industry/strategy reports mentioning a watchlist company are not lost."""
        end_dt = utc_naive_now()
        start_dt = end_dt - timedelta(days=self._history_window(60))
        fields = ["trade_date", "abstr", "title", "report_type", "author", "name", "ts_code", "inst_csname", "ind_name", "url"]
        reports: Dict[str, Dict[str, Any]] = {}
        cursor = start_dt
        while cursor <= end_dt:
            chunk_end = min(cursor + timedelta(days=29), end_dt)
            rows = self._paged_tushare_rows("research_report", params={
                "start_date": cursor.strftime("%Y%m%d"), "end_date": chunk_end.strftime("%Y%m%d"),
            }, limit=1000, max_pages=20, fields=fields)
            for row in rows:
                url = str(row.get("url") or "")
                key = url or hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()
                reports[key] = row
            cursor = chunk_end + timedelta(days=1)
        names = {symbol: self._stock_name(symbol) for symbol in self.equity_watchlist()}
        events = []
        for key, row in reports.items():
            haystack = " ".join(str(row.get(field) or "") for field in ("title", "abstr", "name", "ts_code", "ind_name"))
            symbols = [symbol for symbol, name in names.items() if symbol in haystack or symbol.split(".")[0] in haystack or name in haystack]
            if not symbols:
                continue
            title = str(row.get("title") or "券商研究报告")
            events.append(self._base_event(
                source, external_id=hashlib.sha256(key.encode()).hexdigest()[:32], event_type="research_report_pdf",
                perspective="institution", title=title, summary=str(row.get("abstr") or "")[:3000], symbols=symbols,
                sentiment=self._sentiment(f"{title} {row.get('abstr') or ''}"), importance=76,
                event_at=self._parse_date(row.get("trade_date")), tags=["券商研报", str(row.get("report_type") or "研报"), str(row.get("ind_name") or "")],
                actors=[str(row.get("inst_csname") or ""), str(row.get("author") or "")], metrics=row,
                url=str(row.get("url") or "") or None, raw=row,
            ))
        return events

    def _tushare_long_news_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        now = datetime.now()
        start = now - timedelta(days=self._backfill_days) if self._backfill_days else now - timedelta(hours=30)
        rows: List[Dict[str, Any]] = []
        window_start = start
        while window_start < now:
            window_end = min(window_start + timedelta(days=30), now)
            rows.extend(self._paged_tushare_rows("major_news", params={
                "src": "新浪财经", "start_date": window_start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_date": window_end.strftime("%Y-%m-%d %H:%M:%S"),
            }, limit=800, max_pages=20 if self._backfill_days else 1))
            window_start = window_end
        cctv = []
        cursor_day = start.date() if self._backfill_days else (now - timedelta(days=1)).date()
        while cursor_day <= now.date():
            cctv.extend(self.tushare.query("cctv_news", params={"date": cursor_day.strftime("%Y%m%d")})["rows"])
            cursor_day += timedelta(days=1)
        names = {symbol: self._stock_name(symbol) for symbol in self.watchlist()}
        events = []
        for provider, items in (("新浪财经", rows), ("新闻联播", cctv)):
            for row in items:
                title = str(row.get("title") or row.get("content") or "")[:500]
                content = str(row.get("content") or "")
                symbols = self._symbols_for_text(f"{title} {content}", names)
                if self._backfill_days:
                    symbols = [symbol for symbol in symbols if symbol in names]
                if not symbols:
                    continue
                external = hashlib.sha256(f"{provider}|{row.get('pub_time') or row.get('date')}|{title}".encode()).hexdigest()[:32]
                events.append(self._base_event(
                    source, external_id=external, event_type="long_news", perspective="investor",
                    title=title, summary=content[:3000], symbols=symbols, sentiment=self._sentiment(f"{title} {content}"),
                    importance=self._text_importance(f"{title} {content}", symbols),
                    event_at=self._parse_datetime(row.get("pub_time")) or self._parse_date(row.get("date")),
                    tags=["长篇新闻", provider, *self._text_tags(f"{title} {content}")], actors=[provider], raw=row,
                ))
        return events

    def _paged_tushare_rows(
        self, api_name: str, *, params: Dict[str, Any], limit: int,
        max_pages: int, fields: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Read bounded Tushare pages and stop if an upstream ignores offset."""
        collected: List[Dict[str, Any]] = []
        previous_signature = ""
        for page in range(max(1, int(max_pages))):
            page_rows = self.tushare.query(
                api_name,
                params={**params, "limit": limit, "offset": page * limit},
                fields=list(fields) if fields else None,
            )["rows"]
            signature = hashlib.sha256(json.dumps(page_rows, sort_keys=True, default=str).encode()).hexdigest()
            if page and signature == previous_signature:
                break
            collected.extend(page_rows)
            if len(page_rows) < limit:
                break
            previous_signature = signature
        return collected

    def _tianyancha_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch licensed enterprise facts through the authenticated Tianyancha CLI."""
        if shutil.which("tyc") is None:
            raise InvestmentMonitorError("天眼查 tyc CLI 未安装")
        facets = (
            (("company", "registration-info"), "enterprise_registration", "企业登记信息", "company"),
            (("risk", "overview"), "enterprise_risk", "企业风险总览", "company"),
            (("operation", "credit-evaluation"), "enterprise_credit", "企业信用评价", "company"),
            (("intellectual_property", "ipr-score"), "enterprise_ipr", "知识产权能力", "institution"),
            (("history", "historical-overview"), "enterprise_history", "历史工商变更", "company"),
        )
        events: List[Dict[str, Any]] = []
        queried_at = utc_naive_now()
        for code in self.equity_watchlist():
            company_rows = self.tushare.query("stock_company", params={"ts_code": code})["rows"]
            company_name = str((company_rows[0] if company_rows else {}).get("com_name") or self._stock_name(code))
            for command, event_type, label, perspective in facets:
                completed = subprocess.run(
                    ["tyc", "--compact", *command, company_name], capture_output=True, text=True,
                    timeout=45, check=False,
                )
                if completed.returncode != 0:
                    raise InvestmentMonitorError(f"天眼查 {label} 查询失败：{completed.stderr.strip()[:180]}")
                if len(completed.stdout) > 2_000_000:
                    raise InvestmentMonitorError(f"天眼查 {label} 返回数据超过安全上限")
                try:
                    payload = json.loads(completed.stdout)
                except json.JSONDecodeError as exc:
                    raise InvestmentMonitorError(f"天眼查 {label} 返回非 JSON 数据") from exc
                external = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:28]
                summary = self._tianyancha_summary(label, payload)
                events.append(self._base_event(
                    source, external_id=f"{event_type}:{code}:{external}", event_type=event_type,
                    perspective=perspective, title=f"{company_name} {label}", summary=summary,
                    symbols=[code], sentiment="neutral", importance=82 if event_type == "enterprise_risk" else 68,
                    confidence=0.98, event_at=queried_at, tags=["天眼查", label, "企业事实"],
                    actors=[company_name, "天眼查"], metrics={"query_company": company_name, "result": payload}, raw=payload,
                ))
        return events

    @staticmethod
    def _tianyancha_summary(label: str, payload: Any) -> str:
        if isinstance(payload, dict):
            summary = payload.get("_summary")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()[:3000]
            if label == "企业风险总览":
                detail = payload.get("detailRisks") or []
                relation = payload.get("relationRiskNotes") or []
                return f"天眼查当前返回自身风险 {len(detail)} 条、周边或关联风险 {len(relation)} 条；请在结构化字段中核对具体记录。"
            populated = [key for key, value in payload.items() if value not in (None, "", [], {})]
            return f"天眼查返回 {label}，包含 {len(populated)} 个非空数据维度：{'、'.join(populated[:12]) or '无'}。"
        if isinstance(payload, list):
            return f"天眼查返回 {label} 共 {len(payload)} 条记录。"
        return f"天眼查返回 {label} 数据。"

    def _zsxq_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        # The MCP worker already owns the upstream cursor. This adapter only
        # mirrors notes/analyses changed since its previous successful pass into
        # the unified event table; it must not re-project the whole three-day
        # corpus every ten seconds.
        last_success_raw = source.get("last_success_at")
        last_success = self._utc_iso_to_utc_naive(last_success_raw)
        cutoff = (
            utc_naive_now() - timedelta(days=self._history_window(3))
            if self._backfill_days or last_success is None
            else last_success - timedelta(seconds=60)
        )
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            query = (
                select(EssayAnalysisRecord, ResearchNote)
                .select_from(ResearchNote)
                .outerjoin(EssayAnalysisRecord, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .order_by(desc(ResearchNote.created_at))
            )
            if self._backfill_days:
                query = query.where(ResearchNote.created_at >= cutoff)
            else:
                query = query.where(or_(
                    ResearchNote.synced_at >= cutoff,
                    EssayAnalysisRecord.updated_at >= cutoff,
                ))
            rows = session.execute(query).all()
        names = {symbol: self._stock_name(symbol) for symbol in self.watchlist()}
        events = []
        for analysis, note in rows:
            completed = analysis is not None and analysis.status == "completed"
            mentions = self._json(analysis.stock_mentions_json, []) if completed else []
            symbols = [item.get("ts_code") for item in mentions if item.get("ts_code")]
            symbols.extend(value for value in (note.symbol_codes or "").split(",") if value)
            symbols.extend(self._symbols_for_text(f"{note.title}\n{note.content or ''}", names))
            symbols = list(dict.fromkeys(self._canonical_ts_code(value) for value in symbols if value))
            if self._backfill_days and not set(symbols).intersection(self.watchlist()):
                continue
            category = analysis.primary_category if completed and analysis.primary_category else "other"
            perspective = "institution" if category in {"broker_view", "company_research"} else "company" if category in {"earnings", "risk_warning"} else "investor"
            files = self._json(note.files_json, [])
            images = self._json(note.images_json, [])
            analysis_payload = self._json(analysis.raw_response, {}) if completed else {}
            topic_url = (
                f"https://wx.zsxq.com/group/{note.group_id}/topic/{note.topic_id}"
                if note.group_id and note.topic_id else None
            )
            events.append(self._base_event(
                source,
                external_id=note.topic_id,
                event_type="essay",
                perspective=perspective,
                title=note.title,
                summary=(analysis.summary if completed else None) or note.content or "",
                symbols=symbols,
                sentiment=(analysis.sentiment if completed else None) or "neutral",
                importance=int((analysis.importance_score if completed else None) or (65 if note.digested else 50)),
                confidence=float((analysis.confidence_score if completed else None) or 0.5),
                event_at=note.created_at,
                tags=(self._json(analysis.tags_json, []) if completed else []) + (["含图片"] if images else []) + (["含附件"] if files else []),
                actors=[note.author_name or "", note.group_name],
                metrics={
                    "category": category,
                    "analysis_status": analysis.status if analysis else "pending",
                    "industries": self._json(analysis.industries_json, []) if completed else [],
                    "themes": self._json(analysis.themes_json, []) if completed else [],
                    "stock_mentions": mentions,
                    "catalysts": self._json(analysis.catalysts_json, []) if completed else [],
                    "risks": self._json(analysis.risks_json, []) if completed else [],
                    "earnings_impact": str(analysis_payload.get("earnings_impact") or "")[:1000],
                    "valuation_impact": str(analysis_payload.get("valuation_impact") or "")[:1000],
                    "information_type": str(analysis_payload.get("information_type") or "unknown"),
                    "source_quality": str(analysis_payload.get("source_quality") or "unknown"),
                    "images": images, "files": files,
                },
                url=topic_url,
                raw={"topic_id": note.topic_id, "group_id": note.group_id, "group_name": note.group_name},
            ))
        return events

    def _akshare_stock_comment_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Persist Eastmoney's structured post-close 千股千评 indicators.

        This source intentionally does not fetch or expose Xueqiu discussion-heat
        rankings. Genuine public user comments are handled separately by the
        bounded ``eastmoney.guba_posts`` adapter.
        """
        try:
            import akshare as ak
            from data_provider.akshare_fetcher import _akshare_call_with_timeout
        except Exception as exc:  # noqa: BLE001 - optional provider
            raise InvestmentMonitorError(f"AKShare 股评接口不可用：{type(exc).__name__}") from exc

        symbols = {symbol.split(".")[0]: symbol for symbol in self.equity_watchlist()}
        if not symbols:
            return []

        comment_frame = _akshare_call_with_timeout(
            ak.stock_comment_em, timeout=25, call_name="stock_comment_em",
        )
        events: List[Dict[str, Any]] = []
        for raw_row in self._frame_records(comment_frame):
            code = str(raw_row.get("代码") or raw_row.get("股票代码") or "").zfill(6)
            symbol = symbols.get(code)
            if not symbol:
                continue
            name = str(raw_row.get("名称") or self._stock_name(symbol))
            score = self._optional_number(raw_row.get("综合得分"))
            rank = self._optional_number(raw_row.get("目前排名"))
            focus = self._optional_number(raw_row.get("关注指数"))
            trade_date = raw_row.get("交易日")
            title = f"{name} 东方财富千股千评"
            summary = "，".join(value for value in (
                f"综合得分 {score:g}" if score is not None else "",
                f"当前排名 {int(rank)}" if rank is not None else "",
                f"关注指数 {focus:g}" if focus is not None else "",
            ) if value) or "东方财富千股千评已更新。"
            external = hashlib.sha256(f"comment|{code}|{trade_date}".encode()).hexdigest()[:32]
            events.append(self._base_event(
                source, external_id=external, event_type="stock_comment_snapshot",
                perspective="investor", title=title, summary=summary, symbols=[symbol],
                sentiment="neutral", importance=55,
                event_at=self._parse_date(trade_date), tags=["千股千评", "市场评论指标"],
                actors=["东方财富"], metrics=raw_row,
                url=f"https://data.eastmoney.com/stockcomment/stock/{code}.html", raw=raw_row,
            ))

        return events

    def _eastmoney_guba_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Persist genuine public forum-post excerpts for the current equity watchlist."""
        try:
            rows = self.guba.fetch_latest(self.equity_watchlist(), limit_per_symbol=20)
        except EastmoneyGubaError as exc:
            raise InvestmentMonitorError(str(exc)) from exc
        symbols = {symbol.split(".")[0]: symbol for symbol in self.equity_watchlist()}
        events: List[Dict[str, Any]] = []
        for row in rows:
            code = str(row.get("code") or "").zfill(6)
            symbol = symbols.get(code)
            content = str(row.get("content") or "").strip()
            post_id = str(row.get("post_id") or "").strip()
            if not symbol or not content or not post_id:
                continue
            author = str(row.get("author") or "东方财富股吧用户").strip()
            views = int(row.get("views") or 0)
            replies = int(row.get("reply_count") or 0)
            likes = int(row.get("like_count") or 0)
            images = list(row.get("image_urls") or [])[:9]
            importance = max(35, min(68, 42 + min(replies, 12) + min(views // 500, 8)))
            title = content if len(content) <= 120 else content[:117].rstrip() + "…"
            metrics = {
                "post_id": post_id,
                "author": author,
                "views": views,
                "reply_count": replies,
                "like_count": likes,
                "image_urls": images,
                "time_text": str(row.get("time_text") or ""),
            }
            events.append(self._base_event(
                source, external_id=f"guba:{post_id}", event_type="stock_forum_post",
                perspective="investor", title=title, summary=content, symbols=[symbol],
                sentiment=self._sentiment(content), importance=importance, confidence=0.35,
                event_at=row.get("published_at") or utc_naive_now(),
                tags=["东方财富股吧", "公开股评", "待核验"] + (["含图片"] if images else []),
                actors=[author, "东方财富股吧"], metrics=metrics,
                url=str(row.get("url") or "") or None, raw=row,
            ))
        return events

    @staticmethod
    def _frame_records(frame: Any) -> List[Dict[str, Any]]:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return []
        records = []
        for row in frame.to_dict(orient="records"):
            records.append({
                str(key): None if pd.isna(value) else value.isoformat() if hasattr(value, "isoformat") else value
                for key, value in row.items()
            })
        return records

    @staticmethod
    def _optional_number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if pd.notna(number) else None

    def _feed_events(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        intelligence = IntelligenceService()
        enabled = intelligence.list_sources(enabled=True, page=1, page_size=1)
        if int(enabled.get("total") or 0) == 0:
            raise MonitoringSourceNotConfigured("尚未启用 RSS、Atom 或 NewsNow 上游来源")
        fetch_result = intelligence.fetch_enabled_sources()
        failed = [item for item in fetch_result.get("results") or [] if not item.get("ok")]
        if failed and len(failed) == int(fetch_result.get("source_count") or 0):
            raise InvestmentMonitorError(f"全部外部媒体源拉取失败：{failed[0].get('error') or 'upstream unavailable'}")
        cutoff = utc_naive_now() - timedelta(days=7)
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            rows = session.execute(
                select(IntelligenceItem)
                .where(IntelligenceItem.fetched_at >= cutoff)
                .order_by(desc(IntelligenceItem.fetched_at))
            ).scalars().all()
        names = {symbol: self._stock_name(symbol) for symbol in self.watchlist()}
        events = []
        for row in rows:
            text = f"{row.title} {row.summary or ''}"
            symbols = self._symbols_for_text(text, names)
            if row.scope_type == "symbol" and row.scope_value:
                symbols.append(self._canonical_ts_code(row.scope_value))
            events.append(self._base_event(
                source,
                external_id=f"intel:{row.id}", event_type="news", perspective="investor",
                title=row.title, summary=row.summary or "", symbols=symbols,
                sentiment=self._sentiment(text), importance=self._text_importance(text, symbols),
                event_at=row.published_at or row.fetched_at, tags=self._text_tags(text),
                url=row.url, actors=[row.source_name or row.source or ""],
                raw={"intelligence_item_id": row.id, "market": row.market},
            ))
        return events

    def _normalize_external_event(self, source: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(event, dict):
            raise InvestmentMonitorError("each event must be an object")
        title = str(event.get("title") or "").strip()
        if not title:
            raise InvestmentMonitorError("event title is required")
        perspective = str(event.get("perspective") or "investor").strip().lower()
        if perspective not in PERSPECTIVES:
            raise InvestmentMonitorError(f"unsupported perspective: {perspective}")
        sentiment = str(event.get("sentiment") or "neutral").strip().lower()
        if sentiment not in SENTIMENTS:
            raise InvestmentMonitorError(f"unsupported sentiment: {sentiment}")
        external_id = str(event.get("external_id") or "").strip()
        if not external_id:
            external_id = hashlib.sha256(json.dumps(event, sort_keys=True, default=str).encode()).hexdigest()[:32]
        symbols = [self._canonical_ts_code(value) for value in event.get("symbols") or [] if str(value).strip()]
        return self._base_event(
            source, external_id=external_id, event_type=str(event.get("event_type") or source["category"]),
            perspective=perspective, title=title, summary=str(event.get("summary") or ""),
            symbols=symbols, sentiment=sentiment, importance=int(event.get("importance_score") or 50),
            confidence=float(event.get("confidence_score") or 0.5),
            event_at=self._parse_datetime(event.get("event_at")) or utc_naive_now(),
            tags=list(event.get("tags") or []), actors=list(event.get("actors") or []),
            metrics=dict(event.get("metrics") or {}), url=str(event.get("url") or "") or None, raw=event,
        )

    @staticmethod
    def _base_event(
        source: Dict[str, Any], *, external_id: str, event_type: str, perspective: str,
        title: str, summary: str, symbols: Sequence[str], sentiment: str, importance: int,
        event_at: datetime, tags: Sequence[str], actors: Sequence[str] = (), confidence: float = 0.8,
        metrics: Optional[Dict[str, Any]] = None, url: Optional[str] = None, raw: Any = None,
    ) -> Dict[str, Any]:
        config = source.get("config") or {}
        level = str(config.get("evidence_level") or "unverified")
        origin_apis = list(_EVENT_ORIGIN_APIS.get(event_type) or config.get("origin_apis") or [])
        if metrics is not None:
            event_metrics = dict(metrics)
        elif isinstance(raw, dict):
            event_metrics = dict(raw)
        elif isinstance(raw, list):
            event_metrics = {"records": raw}
        else:
            event_metrics = {}
        event_metrics["_evidence"] = {
            "evidence_level": level,
            "channel": str(source.get("category") or "other"),
            "provider": str(source.get("provider") or source.get("name") or ""),
            "origin_apis": origin_apis,
            "has_original_link": bool(url),
            "content_nature": "derived_summary" if event_type.endswith("snapshot") or event_type in {
                "technical_factor", "realtime_quote"
            } else "source_record",
            "data_timestamp": event_at.isoformat() + "Z" if event_at.tzinfo is None else event_at.isoformat(),
        }
        return {
            "source_key": source["source_key"], "source_name": source["name"],
            "source_type": source["adapter_type"], "external_id": external_id,
            "event_type": event_type, "perspective": perspective, "title": title,
            "summary": summary, "url": url, "symbols": sorted(set(symbols)),
            "sentiment": sentiment if sentiment in SENTIMENTS else "neutral",
            "importance_score": importance, "confidence_score": confidence,
            "tags": [str(value) for value in tags if str(value).strip()][:12],
            "actors": [str(value) for value in actors if str(value).strip()][:10],
            "metrics": event_metrics, "raw_payload": raw or {}, "event_at": event_at,
        }

    def _symbol_card(self, symbol: str, name: str, events: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        perspective = Counter(event["perspective"] for event in events)
        sentiment = Counter(event["sentiment"] for event in events)
        weighted = sum((event["importance_score"] - 40) * (1 if event["sentiment"] == "bullish" else -1 if event["sentiment"] == "bearish" else 0) for event in events)
        opportunity = max(0, min(100, 50 + round(weighted / max(8, len(events) ** 0.5))))
        risk = max(0, min(100, 35 + sentiment["bearish"] * 7 + sum(1 for event in events if "风险" in event["title"]) * 5))
        quote = next((event for event in events if event["event_type"] == "realtime_quote"), None)
        forecasts = [event for event in events if event["event_type"] == "institution_forecast"]
        today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        return {
            "symbol": symbol, "name": name, "event_count": len(events),
            "high_priority_count": sum(1 for event in events if event["importance_score"] >= 75),
            "opportunity_score": opportunity, "risk_score": risk,
            "perspectives": {key: perspective[key] for key in PERSPECTIVES},
            "sentiment": {key: sentiment[key] for key in SENTIMENTS},
            "latest_quote": quote["metrics"] if quote else None,
            "institution_rating_count": len(forecasts),
            "latest_rating": (forecasts[0].get("metrics") or {}).get("rating") if forecasts else None,
            "latest_event_at": max((event.get("event_at") or "" for event in events), default=None),
            "today_event_count": sum(1 for event in events if str(event.get("event_at") or "")[:10] == today),
        }

    @staticmethod
    def _event_evidence(event: Dict[str, Any]) -> Dict[str, Any]:
        evidence = dict((event.get("metrics") or {}).get("_evidence") or {})
        if evidence:
            return evidence
        key = str(event.get("source_key") or "")
        level = "unverified" if key == "zsxq.essays" else "official" if key == "cninfo.announcements" else "reported" if key.startswith("feeds.") else "licensed"
        return {"evidence_level": level, "channel": "essay" if key == "zsxq.essays" else "other"}

    def _stock_name(self, ts_code: str) -> str:
        if ts_code in self._name_cache:
            return self._name_cache[ts_code]
        try:
            rows = self.tushare.query("stock_basic", params={"ts_code": ts_code}, fields=["ts_code", "name"])["rows"]
            name = str(rows[0].get("name") or ts_code) if rows else ts_code
        except Exception:
            name = ts_code
        self._name_cache[ts_code] = name
        return name

    def _dragon_tiger_daily_payload(
        self, trade_date: str, top: Sequence[Dict[str, Any]],
        seats: Sequence[Dict[str, Any]], *, fetched: bool,
    ) -> Dict[str, Any]:
        seat_records = [self._dragon_tiger_seat_record(event) for event in seats]
        by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for seat in seat_records:
            by_symbol[seat["ts_code"]].append(seat)
        records = []
        for event in top:
            record = self._dragon_tiger_record(event)
            same_symbol = by_symbol.get(record["ts_code"], [])
            same_reason = [seat for seat in same_symbol if seat["reason"] == record["reason"]]
            record["seats"] = same_reason or same_symbol
            records.append(record)
        records.sort(key=lambda item: abs(self._number(item.get("net_amount"))), reverse=True)
        net_total = sum(self._number(item.get("net_amount")) for item in records)
        return {
            "trade_date": trade_date,
            "generated_at": utc_naive_now().isoformat() + "Z",
            "source": {
                "provider": "Tushare Pro",
                "apis": ["top_list", "top_inst"],
                "fetched": fetched,
                "amount_unit": "yuan",
                "update_note": "交易所披露口径，Tushare 通常于交易日晚间更新",
            },
            "summary": {
                "row_count": len(records),
                "symbol_count": len({item["ts_code"] for item in records}),
                "seat_count": len(seat_records),
                "positive_count": sum(self._number(item.get("net_amount")) > 0 for item in records),
                "negative_count": sum(self._number(item.get("net_amount")) < 0 for item in records),
                "net_amount": round(net_total, 2),
                "buy_amount": round(sum(self._number(item.get("l_buy")) for item in records), 2),
                "sell_amount": round(sum(self._number(item.get("l_sell")) for item in records), 2),
            },
            "items": records,
        }

    @staticmethod
    def _dragon_tiger_record(event: Dict[str, Any]) -> Dict[str, Any]:
        metrics = dict(event.get("metrics") or {})
        metrics.pop("_evidence", None)
        symbol = str((event.get("symbols") or [metrics.get("ts_code") or ""])[0])
        return {
            "event_id": event.get("id"), "trade_date": str(metrics.get("trade_date") or ""),
            "ts_code": symbol, "name": str(metrics.get("name") or symbol),
            "close": metrics.get("close"), "pct_change": metrics.get("pct_change"),
            "turnover_rate": metrics.get("turnover_rate"), "amount": metrics.get("amount"),
            "l_sell": metrics.get("l_sell"), "l_buy": metrics.get("l_buy"),
            "l_amount": metrics.get("l_amount"), "net_amount": metrics.get("net_amount"),
            "net_rate": metrics.get("net_rate"), "amount_rate": metrics.get("amount_rate"),
            "float_values": metrics.get("float_values"),
            "reason": str(metrics.get("reason") or "交易异动"),
        }

    @staticmethod
    def _dragon_tiger_seat_record(event: Dict[str, Any]) -> Dict[str, Any]:
        metrics = dict(event.get("metrics") or {})
        metrics.pop("_evidence", None)
        symbol = str((event.get("symbols") or [metrics.get("ts_code") or ""])[0])
        return {
            "event_id": event.get("id"), "trade_date": str(metrics.get("trade_date") or ""),
            "ts_code": symbol, "exalter": str(metrics.get("exalter") or "披露席位"),
            "buy": metrics.get("buy"), "buy_rate": metrics.get("buy_rate"),
            "sell": metrics.get("sell"), "sell_rate": metrics.get("sell_rate"),
            "net_buy": metrics.get("net_buy"), "side": str(metrics.get("side") or ""),
            "reason": str(metrics.get("reason") or "交易异动"),
        }

    @staticmethod
    def _validate_trade_date(value: Any) -> str:
        raw = str(value or "").strip().replace("-", "")
        try:
            parsed = datetime.strptime(raw, "%Y%m%d")
        except ValueError as exc:
            raise InvestmentMonitorError("交易日期必须使用 YYYY-MM-DD 或 YYYYMMDD") from exc
        if parsed.year < 2005 or parsed.year > datetime.now().year + 1:
            raise InvestmentMonitorError("龙虎榜日期超出可查询范围")
        return parsed.strftime("%Y%m%d")

    @staticmethod
    def _trade_date_bounds(trade_date: str) -> tuple[datetime, datetime]:
        local_start = datetime.strptime(trade_date, "%Y%m%d").replace(
            tzinfo=timezone(timedelta(hours=8)),
        )
        start_at = local_start.astimezone(timezone.utc).replace(tzinfo=None)
        return start_at, start_at + timedelta(days=1)

    @staticmethod
    def _canonical_ts_code(value: Any) -> str:
        raw = str(value or "").strip().upper().replace("SS", "SH")
        match = _A_SHARE_RE.fullmatch(raw)
        if not match:
            return raw
        digits, suffix = match.groups()
        if not suffix:
            suffix = "BJ" if digits.startswith(("4", "8")) else "SH" if digits.startswith(("6", "9")) else "SZ"
        return f"{digits}.{suffix}"

    def _symbols_for_text(self, text: str, names: Dict[str, str]) -> List[str]:
        symbols = {self._canonical_ts_code(match.group(0)) for match in _A_SHARE_RE.finditer(text)}
        for symbol, name in names.items():
            if any(alias in text for alias in self._stock_aliases(name)):
                symbols.add(symbol)
        return sorted(symbols)

    @staticmethod
    def _stock_aliases(name: str) -> List[str]:
        """Return conservative watchlist-only aliases, e.g. 华懋科技 -> 华懋.

        Two-character aliases are allowed because matching is limited to the
        user's watchlist rather than the entire A-share universe.
        """
        normalized = re.sub(r"^(?:\*?ST|S\*ST)", "", str(name or "").strip(), flags=re.I)
        if not normalized:
            return []
        aliases = [normalized]
        suffixes = (
            "科技", "股份", "集团", "控股", "实业", "电子", "材料", "新材",
            "生物", "医药", "能源", "工业", "电气", "信息", "网络", "软件",
        )
        for suffix in suffixes:
            if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 2:
                aliases.append(normalized[:-len(suffix)])
                break
        return list(dict.fromkeys(alias for alias in aliases if len(alias) >= 2))

    @staticmethod
    def _sentiment(text: str) -> str:
        positive = sum(text.count(word) for word in _POSITIVE_WORDS)
        negative = sum(text.count(word) for word in _NEGATIVE_WORDS)
        if positive and negative:
            return "mixed"
        return "bullish" if positive else "bearish" if negative else "neutral"

    @staticmethod
    def _text_importance(text: str, symbols: Sequence[str]) -> int:
        score = 45 + min(len(symbols) * 8, 16)
        if any(word in text for word in ("公告", "业绩", "监管", "处罚", "重大", "停牌")):
            score += 18
        if any(word in text for word in ("传闻", "网传", "未经证实")):
            score -= 12
        return max(20, min(score, 95))

    @staticmethod
    def _text_tags(text: str) -> List[str]:
        vocabulary = ("业绩", "回购", "增减持", "监管", "并购", "涨价", "AI算力", "机器人", "政策", "订单", "风险", "机构调研")
        return [word for word in vocabulary if word in text]

    @staticmethod
    def _company_summary(api_name: str, row: Dict[str, Any]) -> str:
        if api_name == "forecast":
            return str(row.get("summary") or row.get("change_reason") or "公司发布业绩预告。")
        if api_name == "express":
            return f"营业收入 {row.get('revenue')}，归母净利润 {row.get('n_income')}，同比 {row.get('diluted_roe')}。"
        if api_name == "repurchase":
            return f"进度 {row.get('proc')}，金额 {row.get('amount')}，数量 {row.get('vol')}。"
        if api_name == "stk_holdertrade":
            return f"股东 {row.get('holder_name')}，方向 {row.get('in_de')}，变动数量 {row.get('change_vol')}。"
        if api_name == "share_float":
            return f"解禁日期 {row.get('float_date')}，解禁数量 {row.get('float_share')}。"
        return "上市公司事件。"

    def _company_sentiment(self, api_name: str, row: Dict[str, Any], text: str) -> str:
        if api_name == "repurchase":
            return "bullish"
        if api_name == "share_float":
            return "bearish"
        if api_name == "stk_holdertrade":
            direction = str(row.get("in_de") or "")
            return "bullish" if "增" in direction else "bearish" if "减" in direction else "neutral"
        return self._sentiment(text)

    @staticmethod
    def _parse_date(value: Any) -> datetime:
        raw = str(value or "").replace("-", "")[:8]
        try:
            local = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone(timedelta(hours=8)))
            return local.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return utc_naive_now()

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value or "").strip()
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        # Tushare news and the project's realtime quote adapters return naive
        # China-market timestamps. Persist all monitoring timestamps as UTC.
        return parsed.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _utc_iso_to_market_naive(value: Any) -> Optional[datetime]:
        """Convert persisted UTC scheduler timestamps to Tushare's Shanghai query clock."""
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)

    @staticmethod
    def _utc_iso_to_utc_naive(value: Any) -> Optional[datetime]:
        """Parse an API UTC timestamp into the database's UTC-naive clock."""
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return parsed
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)

    def essay_consensus(self, symbol: str) -> Dict[str, Any]:
        code = self._canonical_ts_code(symbol)
        name = self._stock_name(code)
        return {"symbol": code, "name": name, "consensus": EssayConsensusService().snapshot(code, name, limit=20)}

    def request_essay_consensus(self, symbol: str) -> Dict[str, Any]:
        code = self._canonical_ts_code(symbol)
        if code not in self.watchlist():
            raise InvestmentMonitorError("只能分析当前自选股的小作文一致预期")
        name = self._stock_name(code)
        try:
            snapshot = EssayConsensusService().enqueue(code, name, limit=20, force=True)
            worker = EssayConsensusWorker.get_instance().start()
        except EssayConsensusError as exc:
            raise InvestmentMonitorError(str(exc)) from exc
        return {"symbol": code, "name": name, "consensus": snapshot, "worker": worker}

    @classmethod
    def _consensus_payload(
        cls, research: Sequence[Dict[str, Any]], essays: Sequence[Dict[str, Any]],
        essay_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        broker_rows = [event for event in research if event.get("event_type") == "institution_forecast"]
        ratings: Counter = Counter()
        target_prices: List[float] = []
        forecast_buckets: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        for event in broker_rows:
            metrics = dict(event.get("metrics") or {})
            rating = str(metrics.get("rating") or "未评级").strip() or "未评级"
            ratings[rating] += 1
            for key in ("target_price_min", "target_price_max"):
                value = cls._optional_number(metrics.get(key))
                if value is not None:
                    target_prices.append(value)
            for row in metrics.get("forecasts") or []:
                if not isinstance(row, dict):
                    continue
                period = str(row.get("quarter") or "未注明预测期")
                for field in ("eps", "pe", "np", "op_rt", "roe"):
                    value = cls._optional_number(row.get(field))
                    if value is not None:
                        forecast_buckets[period][field].append(value)

        forecast_rows = []
        for period, values in sorted(forecast_buckets.items()):
            row: Dict[str, Any] = {"period": period, "sample_count": max((len(items) for items in values.values()), default=0)}
            for field, items in values.items():
                row[f"{field}_median"] = cls._median(items)
                row[f"{field}_min"] = min(items)
                row[f"{field}_max"] = max(items)
            forecast_rows.append(row)

        essay_analysis = dict(essay_snapshot or {})
        essay_expectations = [{
            "event_id": item.get("event_id"), "topic_id": item.get("topic_id"),
            "title": item.get("title"), "text": item.get("value_text") or "",
            "event_at": item.get("event_at"), "proposed_at": item.get("proposed_at"),
            "source_kind": item.get("source_kind"), "metric": item.get("metric") or "other",
            "period": item.get("period") or "未注明", "unit": item.get("unit") or "",
            "value_low": item.get("value_low"), "value_high": item.get("value_high"),
            "direction": item.get("direction") or "unclear", "evidence": item.get("evidence") or "",
            "confidence": item.get("confidence"),
        } for item in essay_analysis.get("estimates") or []]

        as_of = max(
            (str(event.get("event_at") or "") for event in (*broker_rows, *essays)),
            default="",
        ) or None
        return {
            "broker_report_count": len(broker_rows),
            "ratings": cls._counter_rows(ratings),
            "target_price": {
                "sample_count": len(target_prices),
                "min": min(target_prices) if target_prices else None,
                "median": cls._median(target_prices),
                "max": max(target_prices) if target_prices else None,
            },
            "forecasts": forecast_rows,
            "essay_expectation_count": len(essay_expectations),
            "essay_expectations": essay_expectations[:20],
            "essay_analysis": essay_analysis,
            "as_of": as_of,
            "method": "券商数字采用 report_rc 同预测期中位数；小作文侧使用最多 20 篇相关原文与 5 篇股票标签仅含当前标的的单股专属原文，去重后由 DeepSeek 按观点提出时间和预测期提取预期。券商与小作文口径分栏展示、不混算。",
        }

    @staticmethod
    def _median(values: Sequence[float]) -> Optional[float]:
        ordered = sorted(float(value) for value in values)
        if not ordered:
            return None
        middle = len(ordered) // 2
        return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _json(value: Optional[str], fallback: Any) -> Any:
        try:
            return json.loads(value) if value else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _counter_rows(counter: Counter, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return [{"name": str(name), "count": int(count)} for name, count in counter.most_common(limit)]
