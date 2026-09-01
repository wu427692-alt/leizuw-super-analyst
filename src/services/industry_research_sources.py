# -*- coding: utf-8 -*-
"""Direct, source-backed inputs for industry and listed-company research.

The long-form research service keeps its durable task/report responsibilities in
``industry_research_service``.  This module owns the comparatively slow network
work so that every external channel has one small, testable adapter and one
explicit status in the final evidence ledger.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree

import requests
from sqlalchemy import and_, desc, or_, select

from src.data.stock_index_loader import get_index_stock_name, resolve_stock_mentions
from src.search_service import get_search_service
from src.services.announcement_artifact_service import AnnouncementArtifactService
from src.services.broker_report_artifact_service import (
    BrokerReportArtifactError,
    BrokerReportArtifactService,
)
from src.services.cninfo_announcement_service import (
    ANNOUNCEMENT_CATEGORIES,
    CninfoAnnouncementService,
)
from src.services.financial_data_service import TushareGatewayService
from src.services.market_data_service import MarketDataService
from src.services.web_article_artifact_service import (
    WebArticleArtifactError,
    WebArticleArtifactService,
)
from src.storage import (
    DatabaseManager,
    EssayAnalysisRecord,
    ResearchNote,
    ResearchReportRecord,
    utc_naive_now,
)

logger = logging.getLogger(__name__)

_PERIODIC_REPORT_CATEGORIES = (
    "category_ndbg_szsh",
    "category_bndbg_szsh",
    "category_yjdbg_szsh",
    "category_sjdbg_szsh",
)
_COMPANY_ANNOUNCEMENT_CATEGORIES = tuple(ANNOUNCEMENT_CATEGORIES)
_A_SHARE_RE = re.compile(r"(?<!\d)(\d{6})(?:\.(SH|SS|SZ|BJ))?(?!\d)", re.IGNORECASE)
_FILING_CATEGORY_TYPES = {
    "category_ndbg_szsh": "annual",
    "category_bndbg_szsh": "interim",
    "category_yjdbg_szsh": "q1",
    "category_sjdbg_szsh": "q3",
}
_FILING_TYPE_LABELS = {
    "annual": "年度报告",
    "interim": "半年度报告",
    "q1": "第一季度报告",
    "q3": "第三季度报告",
    "quarter": "季度报告",
    "material_transaction": "重大交易/重组报告书",
}
_WEB_SUBSTANTIVE_MIN_CHARS = 800


def _canonical_a_share(value: Any) -> str:
    raw = str(value or "").strip().upper().replace(".SS", ".SH")
    match = _A_SHARE_RE.fullmatch(raw)
    if not match:
        return ""
    code, suffix = match.group(1), match.group(2)
    suffix = (suffix or ("BJ" if code.startswith(("4", "8")) else "SH" if code.startswith(("6", "9")) else "SZ")).replace("SS", "SH")
    return f"{code}.{suffix}"


def _number(value: Any) -> Optional[float]:
    if value in (None, "", "--", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(row: Dict[str, Any], *keys: str) -> Optional[float]:
    """Return the first present numeric value without treating zero as empty."""
    for key in keys:
        parsed = _number(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _text(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


class IndustryResearchSourceCollector:
    """Collect current direct sources without hiding partial upstream failure."""

    _stock_basic_cache: Optional[List[Dict[str, Any]]] = None
    _stock_basic_lock = threading.RLock()
    _industry_peer_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
    _industry_peer_lock = threading.RLock()

    def __init__(
        self,
        *,
        tushare: Optional[TushareGatewayService] = None,
        market: Optional[MarketDataService] = None,
        cninfo: Optional[CninfoAnnouncementService] = None,
        announcement_artifacts: Optional[AnnouncementArtifactService] = None,
        broker_report_artifacts: Optional[BrokerReportArtifactService] = None,
        web_artifacts: Optional[WebArticleArtifactService] = None,
        db: Optional[DatabaseManager] = None,
    ) -> None:
        self.tushare = tushare or TushareGatewayService()
        self.market = market or MarketDataService(tushare=self.tushare)
        self.cninfo = cninfo or CninfoAnnouncementService()
        # Both dependencies are lazy in production: merely opening a research
        # page must not create attachment folders or touch the large essay DB.
        self.announcement_artifacts = announcement_artifacts
        self.broker_report_artifacts = broker_report_artifacts
        self.web_artifacts = web_artifacts
        self.db = db

    def collect(
        self,
        *,
        topic: str,
        research_type: str,
        terms: Sequence[str],
        lookback_days: int,
        progress: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        notify = progress or (lambda _message: None)
        result: Dict[str, Any] = {
            "subject": self.resolve_subject(topic, research_type),
            "evidence": [],
            "financial_series": [],
            "market_series": [],
            "valuation_series": [],
            "ownership_governance": [],
            "capital_market_activity": [],
            "filing_documents": [],
            "broker_report_documents": [],
            "web_documents": [],
            "industry_peer_matrix": {},
            "media_gallery": [],
            "source_status": [],
        }
        subject = result["subject"]
        symbol = str(subject.get("symbol") or "")

        if research_type == "company" and symbol:
            notify("正在直连 Tushare 获取公司资料、财务报表与行情")
            self._collect_company_tushare(result, symbol, lookback_days)
            notify("正在直连巨潮获取年报、半年报、季报与公告原文")
            self._collect_cninfo(result, symbol, lookback_days, progress=notify)
        elif research_type == "company":
            result["source_status"].append({
                "key": "company_identity",
                "name": "上市公司识别",
                "status": "missing",
                "count": 0,
                "message": "未能把输入解析为 A 股证券代码；仍会使用名称检索本地语料与互联网。",
            })

        notify("正在读取本地机构段子的原文与 AI 结构化结论")
        self._collect_structured_essays(
            result,
            topic=topic,
            terms=terms,
            lookback_days=lookback_days,
        )
        notify("正在从本地研报库选择高相关 PDF 并读取有限正文")
        self._collect_broker_report_text(
            result,
            topic=topic,
            terms=terms,
            lookback_days=lookback_days,
        )

        notify("正在检索互联网公开信息并保存可追溯链接")
        self._collect_web(result, topic, symbol, terms, research_type)
        result["evidence"] = self._deduplicate_evidence(result["evidence"])
        return result

    def collect_industry_peers(
        self,
        *,
        concept_context: Dict[str, Any],
        lookback_days: int,
    ) -> Dict[str, Any]:
        """Build a bounded, disclosure-based peer matrix for an industry task.

        Representative companies come only from the frozen concept constituent
        set.  The collector intentionally reads at most five companies and four
        low-frequency Tushare resources per company; it never fans out across
        every constituent.  Financial values are copied from structured APIs
        and revision-selected by deterministic code, never inferred by a model.
        """
        constituents = [
            item for item in (concept_context.get("constituents") or [])
            if isinstance(item, dict) and _canonical_a_share(item.get("ts_code"))
        ]
        try:
            max_companies = max(3, min(int(os.getenv("INDUSTRY_RESEARCH_PEER_MAX_COMPANIES", "5")), 5))
        except (TypeError, ValueError):
            max_companies = 5
        try:
            period_limit = max(2, min(int(os.getenv("INDUSTRY_RESEARCH_PEER_PERIODS", "4")), 8))
        except (TypeError, ValueError):
            period_limit = 4
        try:
            api_call_limit = max(12, min(int(os.getenv("INDUSTRY_RESEARCH_PEER_API_CALL_LIMIT", "20")), 24))
        except (TypeError, ValueError):
            api_call_limit = 20
        try:
            cache_ttl = max(300, min(int(os.getenv("INDUSTRY_RESEARCH_PEER_CACHE_TTL_SECONDS", "21600")), 86400))
        except (TypeError, ValueError):
            cache_ttl = 21600

        ranked: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(constituents):
            symbol = _canonical_a_share(item.get("ts_code"))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            weight = _number(item.get("weight_score"))
            source_count = max(0, int(_number(item.get("source_count")) or 0))
            confidence = str(item.get("confidence") or "").strip().lower()
            ranked.append({
                "symbol": symbol,
                "name": _text(item.get("name") or get_index_stock_name(symbol) or symbol, 80),
                "weight_score": weight,
                "source_count": source_count,
                "confidence": confidence or "unknown",
                "concept_reasons": [_text(value, 160) for value in (item.get("reasons") or []) if _text(value, 160)][:6],
                "original_rank": index + 1,
            })
        confidence_rank = {"high": 3, "medium": 2, "low": 1, "insufficient": 0, "unknown": 0}
        ranked.sort(key=lambda item: (
            _number(item.get("weight_score")) is not None,
            _number(item.get("weight_score")) or -1,
            int(item.get("source_count") or 0),
            confidence_rank.get(str(item.get("confidence") or "unknown"), 0),
            -int(item.get("original_rank") or 0),
        ), reverse=True)
        selected = ranked[:max_companies]

        empty_matrix = {
            "status": "missing", "minimum_required": 3, "selected_count": len(selected),
            "company_count": 0, "comparable_company_count": 0,
            "period_limit": period_limit, "common_period": None,
            "companies": [], "selection_method": "按概念成分权重、独立来源数、置信度排序；最多五家。",
        }
        if len(selected) < 3:
            return {
                "industry_peer_matrix": empty_matrix,
                "evidence": [],
                "source_status": [{
                    "key": "industry_peer_facts", "name": "代表企业结构化披露同业数据",
                    "status": "missing", "count": 0, "selected": len(selected), "attempted": 0,
                    "api_calls": 0, "message": f"当前概念成分中仅有 {len(selected)} 家可解析 A 股公司，低于三家同业比较门槛。",
                }],
            }
        if not self.tushare.available:
            empty_matrix["status"] = "unavailable"
            return {
                "industry_peer_matrix": empty_matrix,
                "evidence": [],
                "source_status": [{
                    "key": "industry_peer_facts", "name": "代表企业结构化披露同业数据",
                    "status": "unavailable", "count": 0, "selected": len(selected), "attempted": 0,
                    "api_calls": 0, "message": "Tushare 尚未配置，无法取得代表企业的结构化披露财务数据。",
                }],
            }

        bounded_lookback = max(730, min(int(lookback_days), 3650))
        cache_key = json.dumps({
            "date": date.today().isoformat(),
            "selected": [{
                "symbol": item["symbol"], "name": item["name"],
                "weight_score": item.get("weight_score"),
                "source_count": item.get("source_count"),
                "confidence": item.get("confidence"),
                "concept_reasons": item.get("concept_reasons") or [],
            } for item in selected],
            "period_limit": period_limit,
            "lookback_days": bounded_lookback,
            "api_call_limit": api_call_limit,
        }, sort_keys=True, separators=(",", ":"))
        now = time.monotonic()
        with self._industry_peer_lock:
            cached = self._industry_peer_cache.get(cache_key)
            if cached and now - cached[0] < cache_ttl:
                return deepcopy(cached[1])

        end = date.today()
        start = end - timedelta(days=bounded_lookback)
        companies: List[Dict[str, Any]] = []
        evidence: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        api_calls = 0
        attempted = 0
        specs = (
            ("stock_basic", ["ts_code", "symbol", "name", "industry", "market", "list_date"]),
            ("fina_indicator", [
                "ts_code", "ann_date", "end_date", "roe", "roe_waa", "grossprofit_margin",
                "or_yoy", "netprofit_yoy", "q_sales_yoy", "q_netprofit_yoy", "update_flag",
            ]),
            ("income", [
                "ts_code", "ann_date", "f_ann_date", "end_date", "revenue", "n_income_attr_p",
                "update_flag", "report_type",
            ]),
            ("cashflow", [
                "ts_code", "ann_date", "f_ann_date", "end_date", "n_cashflow_act",
                "update_flag", "report_type",
            ]),
        )
        for candidate in selected:
            if api_calls + len(specs) > api_call_limit:
                errors.append({"symbol": candidate["symbol"], "resource": "budget", "error": "api_call_limit"})
                break
            attempted += 1
            datasets: Dict[str, List[Dict[str, Any]]] = {}
            company_errors: List[str] = []
            for api_name, fields in specs:
                params: Dict[str, Any] = {"ts_code": candidate["symbol"]}
                if api_name != "stock_basic":
                    params.update({"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")})
                try:
                    api_calls += 1
                    datasets[api_name] = list(self.tushare.query(api_name, params=params, fields=fields).get("rows") or [])
                    if not datasets[api_name]:
                        company_errors.append(f"{api_name}:empty")
                        errors.append({
                            "symbol": candidate["symbol"], "resource": api_name,
                            "error": "empty",
                        })
                except Exception as exc:  # noqa: BLE001 - one peer/API must not abort the industry task.
                    datasets[api_name] = []
                    company_errors.append(f"{api_name}:{type(exc).__name__}")
                    errors.append({
                        "symbol": candidate["symbol"], "resource": api_name,
                        "error": type(exc).__name__,
                    })
            series = self._financial_series(datasets)[:period_limit]
            usable_series = [
                row for row in series
                if any(row.get(key) is not None for key in (
                    "revenue", "net_profit", "operating_cashflow", "roe", "gross_margin",
                    "revenue_yoy", "net_profit_yoy",
                ))
            ]
            if not usable_series:
                continue
            basic = (datasets.get("stock_basic") or [{}])[0]
            name = _text(basic.get("name") or candidate.get("name") or candidate["symbol"], 80)
            company = {
                **candidate,
                "name": name,
                "industry": _text(basic.get("industry"), 80) or None,
                "market": _text(basic.get("market"), 40) or None,
                "list_date": basic.get("list_date"),
                "periods": usable_series,
                "latest_period": usable_series[0].get("period"),
                "latest_announcement_date": usable_series[0].get("announcement_date"),
                "latest": dict(usable_series[0]),
                "source_errors": company_errors,
            }
            companies.append(company)
            for row in usable_series:
                period = str(row.get("period") or "")
                evidence_id = f"industry-peer:{candidate['symbol']}:{period}"
                evidence.append({
                    "evidence_id": evidence_id,
                    "kind": "industry_peer_fact",
                    "source": "Tushare Pro（上市公司披露口径）",
                    "title": f"{name} {period} 同业财务事实",
                    "summary": _text(
                        f"证券代码 {candidate['symbol']}；报告期 {period}；营业收入 {row.get('revenue')} 元；"
                        f"归母净利润 {row.get('net_profit')} 元；经营活动现金流量净额 {row.get('operating_cashflow')} 元；"
                        f"ROE {row.get('roe')}%；毛利率 {row.get('gross_margin')}%；"
                        f"累计营收同比 {row.get('revenue_yoy')}%；累计归母净利润同比 {row.get('net_profit_yoy')}%；"
                        f"单季度营收同比 {row.get('quarter_revenue_yoy')}%；"
                        f"单季度归母净利润同比 {row.get('quarter_net_profit_yoy')}%。"
                        "缺失值保持为空，关键数值未由 AI 推算。",
                        1400,
                    ),
                    "date": row.get("announcement_date") or period,
                    "url": None,
                    "symbol": candidate["symbol"],
                    "company": name,
                    "evidence_level": "factual",
                    "original_available": False,
                    "importance": 84,
                    "metric_units": {
                        "revenue": "元", "net_profit": "元", "operating_cashflow": "元",
                        "roe": "%", "gross_margin": "%", "revenue_yoy": "%", "net_profit_yoy": "%",
                        "quarter_revenue_yoy": "%", "quarter_net_profit_yoy": "%",
                    },
                })

        # Prefer the latest period disclosed by at least the three companies
        # required by the research contract.  Requiring every selected company
        # to share a period would let one newly listed or temporarily failing
        # peer erase an otherwise valid three-company comparison.
        period_company_counts: Dict[str, int] = {}
        for company in companies:
            for period in {
                str(row.get("period") or "")
                for row in company.get("periods") or []
                if row.get("period")
                and row.get("revenue") is not None
                and row.get("net_profit") is not None
            }:
                period_company_counts[period] = period_company_counts.get(period, 0) + 1
        comparable_periods = [
            period for period, count in period_company_counts.items()
            if period and count >= 3
        ]
        common_period = max(comparable_periods) if comparable_periods else None
        for company in companies:
            common = next((row for row in company.get("periods") or [] if row.get("period") == common_period), None)
            company["common_period_fact"] = dict(common) if isinstance(common, dict) else None

        company_count = len(companies)
        comparable_company_count = int(period_company_counts.get(str(common_period or "")) or 0)
        has_comparable_sample = comparable_company_count >= 3
        metric_coverage = {
            metric: sum(
                1 for company in companies
                if isinstance(company.get("common_period_fact"), dict)
                and company["common_period_fact"].get(metric) is not None
            )
            for metric in (
                "revenue", "net_profit", "operating_cashflow", "roe", "gross_margin",
                "revenue_yoy", "net_profit_yoy", "quarter_revenue_yoy", "quarter_net_profit_yoy",
            )
        }
        matrix_status = (
            "covered" if has_comparable_sample and comparable_company_count == company_count and not errors
            else "partial" if has_comparable_sample
            else "missing"
        )
        matrix = {
            "status": matrix_status,
            "minimum_required": 3,
            "selected_count": len(selected),
            "attempted_count": attempted,
            "company_count": company_count,
            "comparable_company_count": comparable_company_count,
            "period_limit": period_limit,
            "common_period": common_period,
            "metric_coverage": metric_coverage,
            "companies": companies,
            "selection_method": "仅从概念成分中按权重、独立来源数和置信度排序，固定抽取最多五家代表企业。",
            "numeric_method": "公司基础、财务指标、利润表与现金流量表由 Tushare 结构化接口直取；同报告期修订版本由代码确定性选择，AI 不计算关键数值。",
            "api_call_limit": api_call_limit,
            "api_calls": api_calls,
        }
        payload = {
            "industry_peer_matrix": matrix,
            "evidence": evidence,
            "source_status": [{
                "key": "industry_peer_facts", "name": "代表企业结构化披露同业数据",
                "status": matrix_status, "count": comparable_company_count,
                "company_count": company_count, "selected": len(selected),
                "attempted": attempted, "fact_count": len(evidence), "common_period": common_period,
                "api_calls": api_calls, "api_call_limit": api_call_limit,
                "failures": errors[:12],
                "message": (
                    f"从概念成分中选择 {len(selected)} 家代表企业，成功取得 {company_count} 家、"
                    f"{len(evidence)} 个报告期的结构化披露事实；共同报告期 {common_period or '缺失'} "
                    f"覆盖 {comparable_company_count} 家；单任务调用 {api_calls}/{api_call_limit} 次。"
                ),
            }],
        }
        if matrix_status == "covered":
            with self._industry_peer_lock:
                self._industry_peer_cache[cache_key] = (now, deepcopy(payload))
                if len(self._industry_peer_cache) > 48:
                    oldest = min(self._industry_peer_cache, key=lambda key: self._industry_peer_cache[key][0])
                    self._industry_peer_cache.pop(oldest, None)
        return payload

    def resolve_subject(self, topic: str, research_type: str) -> Dict[str, Any]:
        if research_type != "company":
            return {"research_type": "industry", "name": topic, "symbol": None, "resolved": True}

        symbol = _canonical_a_share(topic)
        name = get_index_stock_name(symbol) if symbol else None
        if not symbol:
            mentions = resolve_stock_mentions(topic, limit=3)
            exact = next((item for item in mentions if str(item.get("stock_name") or "").strip() == topic.strip()), None)
            chosen = exact or (mentions[0] if len(mentions) == 1 else None)
            if chosen:
                symbol = _canonical_a_share(chosen.get("stock_code"))
                name = str(chosen.get("stock_name") or "").strip() or None
        if not symbol and self.tushare.available:
            match = self._stock_basic_match(topic)
            if match:
                symbol = _canonical_a_share(match.get("ts_code") or match.get("symbol"))
                name = str(match.get("name") or "").strip() or None
        if symbol and not name:
            name = get_index_stock_name(symbol)
        return {
            "research_type": "company",
            "name": name or topic,
            "symbol": symbol or None,
            "resolved": bool(symbol),
            "input": topic,
        }

    def _stock_basic_match(self, query: str) -> Optional[Dict[str, Any]]:
        normalized = str(query or "").strip().casefold()
        if not normalized:
            return None
        with self._stock_basic_lock:
            if self._stock_basic_cache is None:
                try:
                    response = self.tushare.query(
                        "stock_basic",
                        params={"list_status": "L"},
                        fields=["ts_code", "symbol", "name", "industry", "market", "list_date"],
                    )
                    self._stock_basic_cache = list(response.get("rows") or [])
                except Exception as exc:  # noqa: BLE001 - name resolution has local fallbacks.
                    logger.info("Industry research stock_basic lookup unavailable: %s", type(exc).__name__)
                    self._stock_basic_cache = []
            rows = list(self._stock_basic_cache or [])
        exact = [row for row in rows if str(row.get("name") or "").strip().casefold() == normalized]
        return exact[0] if len(exact) == 1 else None

    def _collect_company_tushare(self, result: Dict[str, Any], symbol: str, lookback_days: int) -> None:
        if not self.tushare.available:
            result["source_status"].append({
                "key": "tushare_company",
                "name": "Tushare 公司与财务",
                "status": "unavailable",
                "count": 0,
                "message": "Tushare 尚未配置。",
            })
            return
        end = date.today()
        start = end - timedelta(days=max(365, min(int(lookback_days), 3650)))
        errors: List[str] = []
        profile_rows: List[Dict[str, Any]] = []
        datasets: Dict[str, List[Dict[str, Any]]] = {}
        for api_name, params in (
            ("stock_basic", {"ts_code": symbol}),
            ("stock_company", {"ts_code": symbol}),
            ("fina_indicator", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}),
            ("income", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}),
            ("balancesheet", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}),
            ("cashflow", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}),
            ("forecast", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}),
            ("report_rc", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}),
        ):
            try:
                rows = list(self.tushare.query(api_name, params=params).get("rows") or [])
            except Exception as exc:  # noqa: BLE001 - every API remains independently observable.
                errors.append(f"{api_name}:{type(exc).__name__}")
                rows = []
            datasets[api_name] = rows
            if api_name == "stock_company":
                profile_rows = rows

        basic = (datasets.get("stock_basic") or [{}])[0]
        profile = profile_rows[0] if profile_rows else {}
        if profile:
            result["subject"].update({
                "company_name": profile.get("com_name"),
                "industry": basic.get("industry") or profile.get("industry"),
                "market": basic.get("market"),
                "list_date": basic.get("list_date"),
                "main_business": profile.get("main_business"),
                "chairman": profile.get("chairman"),
                "employees": profile.get("employees"),
                "province": profile.get("province"),
                "website": profile.get("website"),
            })
            result["evidence"].append({
                "evidence_id": f"company:{symbol}",
                "kind": "company_profile",
                "source": "Tushare stock_company",
                "title": f"{result['subject'].get('name') or symbol} 公司基础资料",
                "summary": _text(
                    f"主营业务：{profile.get('main_business') or '未披露'}；行业：{basic.get('industry') or profile.get('industry') or '未披露'}；"
                    f"员工：{profile.get('employees') or '未披露'}；所在地区：{profile.get('province') or '未披露'}。",
                    1200,
                ),
                "date": end.isoformat(),
                "url": profile.get("website"),
                "symbol": symbol,
                "company": result["subject"].get("name"),
                "evidence_level": "factual",
                "original_available": bool(profile.get("website")),
            })

        result["financial_series"] = self._financial_series(datasets)
        # Keep the evidence universe aligned with the 16-period structured
        # series.  A structured row without its real evidence endpoint must
        # never become citable merely because it exists in a fact ledger.
        for item in result["financial_series"][:16]:
            result["evidence"].append({
                "evidence_id": f"financial:{symbol}:{item['period']}",
                "kind": "financial_statement",
                "source": "Tushare 财务报表与财务指标",
                "title": f"{result['subject'].get('name') or symbol} {item['period']} 财务快照",
                "summary": _text(
                    f"营业收入 {item.get('revenue')}；归母净利润 {item.get('net_profit')}；经营现金流 {item.get('operating_cashflow')}；"
                    f"ROE {item.get('roe')}%；毛利率 {item.get('gross_margin')}%；总资产 {item.get('total_assets')}；总负债 {item.get('total_liabilities')}；"
                    f"同报告期已排除 {int(item.get('revision_count') or 0)} 个旧/重复版本，选用各报表最新公告或修订口径。",
                    1200,
                ),
                "date": item.get("announcement_date") or item["period"],
                "url": None,
                "symbol": symbol,
                "company": result["subject"].get("name"),
                "evidence_level": "factual",
                "original_available": False,
                "authority_tier": "structured_disclosure_mapping",
                "content_verified": True,
                "support_scope": "structured_metric_direct",
                "supports_metrics": [
                    "revenue", "net_profit", "operating_cashflow", "roe",
                    "gross_margin", "total_assets", "total_liabilities",
                ],
            })

        forecasts = list(datasets.get("forecast") or [])[:12] + list(datasets.get("report_rc") or [])[:20]
        for index, item in enumerate(forecasts):
            period = str(item.get("end_date") or item.get("report_date") or item.get("date") or end.isoformat())
            summary = _text("；".join(
                f"{key}={value}" for key, value in item.items()
                if value not in (None, "") and key in {
                    "type", "summary", "p_change_min", "p_change_max", "net_profit_min", "net_profit_max",
                    "report_date", "quarter", "op_rt", "op_pr", "tp", "rating", "inst_csname", "author_name",
                    "org_name", "author", "np", "eps", "pe", "max_price", "min_price", "year",
                }
            ), 1400)
            result["evidence"].append({
                "evidence_id": f"forecast:{symbol}:{period}:{index}",
                "kind": "earnings_expectation",
                "source": "Tushare 业绩预告与机构盈利预测",
                "title": f"{result['subject'].get('name') or symbol} {period} 业绩/一致预期",
                "summary": summary or "已取得结构化预测记录，需结合原始披露口径核验。",
                "date": str(item.get("ann_date") or item.get("report_date") or period),
                "url": None,
                "symbol": symbol,
                "company": result["subject"].get("name"),
                "evidence_level": "reported",
                "original_available": False,
            })

        supplemental_specs = (
            ("daily_basic", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "market"),
            ("cyq_perf", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "market"),
            ("stk_factor", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "market"),
            ("stk_holdernumber", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "governance"),
            ("stk_managers", {"ts_code": symbol}, "governance"),
            ("pledge_stat", {"ts_code": symbol}, "governance"),
            ("share_float", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "capital"),
            ("repurchase", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "capital"),
            ("stk_holdertrade", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "capital"),
            ("dividend", {"ts_code": symbol}, "capital"),
            ("express", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "capital"),
            ("stk_surv", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "institution"),
            ("margin_detail", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "institution"),
            ("hk_hold", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "institution"),
            ("top_list", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}, "institution"),
        )
        supplemental: Dict[str, List[Dict[str, Any]]] = {}
        supplemental_groups: Dict[str, int] = {"market": 0, "governance": 0, "capital": 0, "institution": 0}
        supplemental_errors: Dict[str, List[str]] = {key: [] for key in supplemental_groups}
        for api_name, params, group in supplemental_specs:
            try:
                rows = list(self.tushare.query(api_name, params=params).get("rows") or [])
            except Exception as exc:  # noqa: BLE001 - premium permissions differ by account and endpoint.
                supplemental_errors[group].append(f"{api_name}:{type(exc).__name__}")
                rows = []
            supplemental[api_name] = rows
            supplemental_groups[group] += len(rows)

        result["valuation_series"] = self._valuation_series(supplemental)
        # Recent reporting-date valuation resets need adjacent daily endpoints
        # (for example, the trading day immediately before and after a half-
        # year report).  Preserve a bounded recent window rather than only the
        # latest quote so those changes can be explained without invented IDs.
        for item in result["valuation_series"][:24]:
            trade_date = str(item.get("date") or "").strip()
            if not trade_date:
                continue
            result["evidence"].append({
                "evidence_id": f"valuation:{symbol}:{trade_date}",
                "kind": "valuation_fact",
                "source": "Tushare daily_basic + cyq_perf + stk_factor",
                "title": f"{result['subject'].get('name') or symbol} {trade_date} 估值与筹码事实",
                "summary": _text(
                    f"收盘价 {item.get('close')} 元/股；PE(TTM) {item.get('pe_ttm')} 倍；"
                    f"PB {item.get('pb')} 倍；PS(TTM) {item.get('ps_ttm')} 倍；"
                    f"股息率(TTM) {item.get('dv_ttm')}%；换手率 {item.get('turnover_rate')}%；"
                    f"总市值 {item.get('total_market_value')} 万元；流通市值 {item.get('float_market_value')} 万元；"
                    f"筹码加权成本 {item.get('chip_cost')} 元；筹码获利比例 {item.get('winner_rate')}%；"
                    f"RSI(6) {item.get('rsi_6')}。缺失值保持为空，未由模型补齐。",
                    1600,
                ),
                "date": trade_date,
                "url": None,
                "symbol": symbol,
                "company": result["subject"].get("name"),
                "evidence_level": "factual",
                "original_available": False,
                "authority_tier": "structured_market_mapping",
                "content_verified": True,
                "support_scope": "structured_metric_direct",
                "supports_metrics": [
                    "close", "pe_ttm", "pb", "ps_ttm", "total_market_value",
                    "float_market_value", "chip_cost", "winner_rate", "rsi_6",
                ],
                "metric_units": {
                    "close": "元/股", "pe_ttm": "倍", "pb": "倍", "ps_ttm": "倍",
                    "dv_ttm": "%", "turnover_rate": "%", "total_market_value": "万元",
                    "float_market_value": "万元", "chip_cost": "元", "winner_rate": "%",
                    "rsi_6": "指数点",
                },
            })
        result["ownership_governance"] = self._structured_rows(
            supplemental, ("stk_holdernumber", "stk_managers", "pledge_stat"), limit_per_source=6,
        )
        result["capital_market_activity"] = self._structured_rows(
            supplemental,
            ("share_float", "repurchase", "stk_holdertrade", "dividend", "express", "stk_surv", "margin_detail", "hk_hold", "top_list"),
            limit_per_source=6,
        )
        for group, title, level in (
            ("governance", "股东人数、管理层与股权质押", "factual"),
            ("capital", "解禁、回购、增减持、分红与业绩快报", "factual"),
            ("institution", "机构调研、融资融券、北向与龙虎榜", "reported"),
        ):
            for api_name, _params, api_group in supplemental_specs:
                if api_group != group:
                    continue
                for index, item in enumerate(supplemental.get(api_name) or []):
                    if index >= 3:
                        break
                    event_date = self._row_date(item) or end.isoformat()
                    result["evidence"].append({
                        "evidence_id": f"tushare:{api_name}:{symbol}:{event_date}:{index}",
                        "kind": f"company_{group}",
                        "source": f"Tushare {api_name}",
                        "title": f"{result['subject'].get('name') or symbol} · {title}",
                        "summary": self._row_summary(item),
                        "date": event_date,
                        "url": None,
                        "symbol": symbol,
                        "company": result["subject"].get("name"),
                        "evidence_level": level,
                        "original_available": False,
                    })
        for group, name in (
            ("market", "估值、筹码与技术因子"),
            ("governance", "股东、管理层与质押"),
            ("capital", "公司行动与业绩快报"),
            ("institution", "机构行为与交易结构"),
        ):
            group_errors = supplemental_errors[group]
            group_count = supplemental_groups[group]
            result["source_status"].append({
                "key": f"tushare_{group}",
                "name": f"Tushare {name}",
                "status": "partial" if group_errors and group_count else "failed" if group_errors else "covered" if group_count else "missing",
                "count": group_count,
                "message": "；".join(group_errors[:6]) if group_errors else "已固化原始接口记录并提取最新事实。",
            })

        try:
            market = self.market.get_series(
                symbol,
                period="daily",
                days=max(365, min(int(lookback_days), 1825)),
                refresh=True,
                max_points=520,
            )
            result["market_series"] = list(market.get("data") or [])
            data = result["market_series"]
            if data:
                first_close = _number(data[0].get("close"))
                last_close = _number(data[-1].get("close"))
                total_return = ((last_close / first_close - 1) * 100) if first_close and last_close else None
                result["evidence"].append({
                    "evidence_id": f"market:{symbol}:daily",
                    "kind": "market_series",
                    "source": str(market.get("source") or "本地行情库"),
                    "title": f"{result['subject'].get('name') or symbol} 历史行情序列",
                    "summary": _text(
                        f"共 {len(data)} 个日线点，区间 {data[0].get('date')} 至 {data[-1].get('date')}，"
                        f"区间收益 {total_return:.2f}%" if total_return is not None else f"共 {len(data)} 个日线点。",
                        800,
                    ),
                    "date": str(data[-1].get("date") or end.isoformat()),
                    "url": None,
                    "symbol": symbol,
                    "company": result["subject"].get("name"),
                    "evidence_level": "factual",
                    "original_available": False,
                })
        except Exception as exc:  # noqa: BLE001 - report still includes financial statements.
            errors.append(f"market:{type(exc).__name__}")

        count = (
            len(result["financial_series"]) + len(result["market_series"]) + len(profile_rows)
            + len(forecasts) + sum(supplemental_groups.values())
        )
        result["source_status"].append({
            "key": "tushare_company",
            "name": "Tushare 公司、财务、预测与行情",
            "status": "partial" if errors and count else "failed" if errors else "covered",
            "count": count,
            "message": "；".join(errors[:5]) if errors else "已直接调用接口并固化任务快照。",
        })

    @staticmethod
    def _financial_row_rank(row: Dict[str, Any]) -> tuple:
        """Rank statement revisions deterministically within one report period.

        Tushare can return the original disclosure and one or more updated
        statements for the same ``end_date``.  API row order is not a revision
        contract, so prefer the latest actual/official announcement, an update
        flag, and an adjusted consolidated statement before completeness and a
        stable payload tie-breaker.
        """
        dates = [
            re.sub(r"\D", "", str(row.get(key) or ""))[:14]
            for key in ("f_ann_date", "ann_date", "update_time")
        ]
        latest_announcement = max((value for value in dates if value), default="")
        update_flag = 1 if str(row.get("update_flag") or "").strip().lower() in {"1", "true", "y", "yes"} else 0
        report_type = str(row.get("report_type") or "").strip()
        # Tushare report_type: adjusted consolidated (4) and consolidated
        # (1) are preferred over parent/single-quarter variants when their
        # disclosure dates are otherwise identical.
        report_type_rank = {"4": 4, "1": 3, "5": 2, "2": 1}.get(report_type, 0)
        populated = sum(value not in (None, "") for value in row.values())
        stable = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        return latest_announcement, update_flag, report_type_rank, populated, stable

    @classmethod
    def _latest_financial_rows(
        cls,
        rows: Sequence[Dict[str, Any]],
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, int]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            period = str(row.get("end_date") or "").strip()
            if period:
                grouped.setdefault(period, []).append(row)
        selected = {
            period: max(period_rows, key=cls._financial_row_rank)
            for period, period_rows in grouped.items()
        }
        return selected, {period: len(period_rows) for period, period_rows in grouped.items()}

    @classmethod
    def _financial_series(cls, datasets: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        by_period: Dict[str, Dict[str, Any]] = {}
        selected_by_dataset: Dict[str, Dict[str, Dict[str, Any]]] = {}
        versions_by_dataset: Dict[str, Dict[str, int]] = {}
        for dataset in ("fina_indicator", "income", "balancesheet", "cashflow"):
            selected, versions = cls._latest_financial_rows(datasets.get(dataset) or [])
            selected_by_dataset[dataset] = selected
            versions_by_dataset[dataset] = versions

        for period, row in selected_by_dataset["fina_indicator"].items():
            by_period.setdefault(period, {}).update({
                    "period": period,
                    "announcement_date": row.get("f_ann_date") or row.get("ann_date"),
                    "roe": _first_number(row, "roe", "roe_waa"),
                    "gross_margin": _number(row.get("grossprofit_margin")),
                    # ``or_yoy`` and ``netprofit_yoy`` match the cumulative
                    # income-statement values.  The q_* fields are single-
                    # quarter growth and therefore remain separate facts.
                    "revenue_yoy": _first_number(row, "or_yoy", "revenue_yoy"),
                    "net_profit_yoy": _first_number(row, "netprofit_yoy"),
                    "quarter_revenue_yoy": _first_number(row, "q_sales_yoy"),
                    "quarter_net_profit_yoy": _first_number(row, "q_netprofit_yoy"),
            })
        mappings = {
            "income": {"revenue": "revenue", "net_profit": "n_income_attr_p"},
            "balancesheet": {"total_assets": "total_assets", "total_liabilities": "total_liab"},
            "cashflow": {"operating_cashflow": "n_cashflow_act"},
        }
        for dataset, fields in mappings.items():
            for period, row in selected_by_dataset[dataset].items():
                target = by_period.setdefault(period, {"period": period})
                announcement = str(row.get("f_ann_date") or row.get("ann_date") or "").strip()
                target["announcement_date"] = max(
                    str(target.get("announcement_date") or ""), announcement,
                ) or None
                for output, source in fields.items():
                    target[output] = _number(row.get(source))

        for period, target in by_period.items():
            source_versions: Dict[str, Dict[str, Any]] = {}
            superseded = 0
            for dataset in ("fina_indicator", "income", "balancesheet", "cashflow"):
                count = int(versions_by_dataset[dataset].get(period) or 0)
                selected = selected_by_dataset[dataset].get(period) or {}
                if not count:
                    continue
                superseded += max(0, count - 1)
                source_versions[dataset] = {
                    "versions": count,
                    "selected_announcement_date": selected.get("f_ann_date") or selected.get("ann_date"),
                    "selected_update_flag": selected.get("update_flag"),
                    "selected_report_type": selected.get("report_type"),
                }
            target["revision_count"] = superseded
            target["source_versions"] = source_versions
            period_digits = re.sub(r"\D", "", str(period or ""))[:8]
            year = period_digits[:4]
            suffix = period_digits[4:]
            period_labels = {
                "0331": (f"{year}Q1累计", "YTD_Q1"),
                "0630": (f"{year}H1累计", "YTD_H1"),
                "0930": (f"{year}Q1-Q3累计", "YTD_9M"),
                "1231": (f"{year}年度", "ANNUAL"),
            }
            period_label, period_basis = period_labels.get(
                suffix, (str(period or "未知报告期"), "UNKNOWN"),
            )
            selected_report_types = {
                str((selected_by_dataset[dataset].get(period) or {}).get("report_type") or "")
                for dataset in ("fina_indicator", "income", "balancesheet", "cashflow")
            }
            target.update({
                "period_label": period_label,
                "period_basis": period_basis,
                "statement_scope": (
                    "consolidated"
                    if selected_report_types.intersection({"1", "4"})
                    else "scope_requires_source_verification"
                ),
                "metric_basis": "statutory",
                "verification_status": "direct_structured_record",
                "authority_tier": "structured_disclosure_mapping",
            })
        return [by_period[key] for key in sorted(by_period, reverse=True)[:16]]

    @staticmethod
    def _valuation_series(datasets: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        chips = {str(row.get("trade_date") or ""): row for row in datasets.get("cyq_perf") or []}
        factors = {str(row.get("trade_date") or ""): row for row in datasets.get("stk_factor") or []}
        output: List[Dict[str, Any]] = []
        for row in datasets.get("daily_basic") or []:
            day = str(row.get("trade_date") or "").strip()
            if not day:
                continue
            chip = chips.get(day, {})
            factor = factors.get(day, {})
            output.append({
                "date": day,
                "close": _number(row.get("close")),
                "pe_ttm": _number(row.get("pe_ttm")),
                "pb": _number(row.get("pb")),
                "ps_ttm": _number(row.get("ps_ttm")),
                "dv_ttm": _number(row.get("dv_ttm")),
                "turnover_rate": _number(row.get("turnover_rate")),
                "total_market_value": _number(row.get("total_mv")),
                "float_market_value": _number(row.get("circ_mv")),
                "chip_cost": _number(chip.get("weight_avg")),
                "winner_rate": _number(chip.get("winner_rate")),
                "rsi_6": _number(factor.get("rsi_6")),
            })
        return sorted(output, key=lambda item: str(item.get("date") or ""), reverse=True)[:520]

    @classmethod
    def _structured_rows(
        cls,
        datasets: Dict[str, List[Dict[str, Any]]],
        names: Sequence[str],
        *,
        limit_per_source: int,
    ) -> List[Dict[str, Any]]:
        output: List[Dict[str, Any]] = []
        for name in names:
            for row in (datasets.get(name) or [])[:limit_per_source]:
                output.append({"resource": name, "date": cls._row_date(row), "fields": row})
        return output

    @staticmethod
    def _row_date(row: Dict[str, Any]) -> str:
        for key in ("ann_date", "trade_date", "end_date", "report_date", "surv_date", "holder_date", "float_date", "imp_ann_date"):
            value = str(row.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _row_summary(row: Dict[str, Any], limit: int = 1800) -> str:
        ignored = {"ts_code", "symbol", "exchange", "name", "com_name"}
        parts = [f"{key}={value}" for key, value in row.items() if key not in ignored and value not in (None, "")]
        return _text("；".join(parts) or "接口返回记录缺少可展示字段，需回到结构化原始记录核验。", limit)

    def _collect_cninfo(
        self,
        result: Dict[str, Any],
        symbol: str,
        lookback_days: int,
        *,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        notify = progress or (lambda _message: None)
        end = date.today()
        start = end - timedelta(days=max(365, min(int(lookback_days), 3650)))
        try:
            # CNInfo's semicolon-separated ``category`` parameter is not a
            # reliable union.  A real company query containing all categories
            # can silently omit quarterly reports that are returned when the
            # exact same category is queried on its own.  Query each category
            # independently and merge by announcement id so the evidence
            # ledger represents the requested disclosure universe.
            rows_by_id: Dict[str, Dict[str, Any]] = {}
            category_failures: Dict[str, str] = {}
            category_truncated: Dict[str, Dict[str, int]] = {}
            category_states: Dict[str, Dict[str, Any]] = {}
            category_successes = 0
            category_total = len(_COMPANY_ANNOUNCEMENT_CATEGORIES)
            try:
                cninfo_deadline_seconds = max(
                    45.0,
                    min(float(os.getenv("INDUSTRY_RESEARCH_CNINFO_DEADLINE_SECONDS", "180")), 300.0),
                )
            except (TypeError, ValueError):
                cninfo_deadline_seconds = 180.0
            try:
                cninfo_request_limit = max(
                    30,
                    min(int(os.getenv("INDUSTRY_RESEARCH_CNINFO_REQUEST_LIMIT", "120")), 240),
                )
            except (TypeError, ValueError):
                cninfo_request_limit = 120
            try:
                cninfo_max_pages = max(
                    2,
                    min(int(os.getenv("INDUSTRY_RESEARCH_CNINFO_MAX_PAGES_PER_CATEGORY", "10")), 20),
                )
            except (TypeError, ValueError):
                cninfo_max_pages = 10
            try:
                category_interval = max(
                    0.0,
                    min(float(os.getenv("INDUSTRY_RESEARCH_CNINFO_CATEGORY_INTERVAL", "0.15")), 1.0),
                )
            except (TypeError, ValueError):
                category_interval = 0.15
            if not isinstance(self.cninfo, CninfoAnnouncementService):
                # Injected deterministic adapters do not make network calls;
                # keeping production politeness sleeps out of unit tests also
                # makes failure-budget regressions fast enough to run often.
                category_interval = 0.0
            deadline_monotonic = time.monotonic() + cninfo_deadline_seconds
            request_budget = {"remaining": cninfo_request_limit, "used": 0}
            ordered_categories = (
                *_PERIODIC_REPORT_CATEGORIES,
                *(
                    code
                    for code in _COMPANY_ANNOUNCEMENT_CATEGORIES
                    if code not in _PERIODIC_REPORT_CATEGORIES
                ),
            )
            for category_index, category_code in enumerate(ordered_categories, start=1):
                if time.monotonic() >= deadline_monotonic or request_budget["remaining"] <= 0:
                    for deferred_code in ordered_categories[category_index - 1:]:
                        category_failures[deferred_code] = "budget_exhausted"
                        category_states[deferred_code] = {
                            "status": "budget_exhausted",
                            "count": 0,
                            "pages_fetched": 0,
                            "total_pages": 0,
                        }
                    break
                if category_index > 1 and category_interval:
                    time.sleep(min(category_interval, max(0.0, deadline_monotonic - time.monotonic())))
                if category_code in _PERIODIC_REPORT_CATEGORIES:
                    notify(
                        f"正在核对巨潮定期报告分类 "
                        f"{category_index}/{category_total} · {ANNOUNCEMENT_CATEGORIES[category_code]}"
                    )
                diagnostics: Dict[str, Any] = {}
                try:
                    category_rows = self.cninfo.fetch(
                        start_date=start,
                        end_date=end,
                        symbols=[symbol],
                        categories=[category_code],
                        page_size=100,
                        max_pages=cninfo_max_pages,
                        exclude_noise=True,
                        deadline_monotonic=deadline_monotonic,
                        request_budget=request_budget,
                        diagnostics=diagnostics,
                    )
                    category_successes += 1
                except Exception as exc:  # noqa: BLE001 - retain other disclosure categories.
                    budget_exhausted = (
                        time.monotonic() >= deadline_monotonic
                        or request_budget["remaining"] <= 0
                        or "预算" in str(exc)
                    )
                    failure_reason = "budget_exhausted" if budget_exhausted else type(exc).__name__
                    category_failures[category_code] = failure_reason
                    category_states[category_code] = {
                        "status": failure_reason,
                        "count": 0,
                        "pages_fetched": int(diagnostics.get("pages_fetched") or 0),
                        "total_pages": int(diagnostics.get("total_pages") or 0),
                    }
                    logger.info(
                        "Industry research CNInfo category unavailable: %s %s",
                        category_code,
                        failure_reason,
                    )
                    if budget_exhausted:
                        for deferred_code in ordered_categories[category_index:]:
                            category_failures[deferred_code] = "budget_exhausted"
                            category_states[deferred_code] = {
                                "status": "budget_exhausted",
                                "count": 0,
                                "pages_fetched": 0,
                                "total_pages": 0,
                            }
                        break
                    continue
                is_truncated = bool(diagnostics.get("truncated"))
                if is_truncated:
                    category_truncated[category_code] = {
                        "pages_fetched": int(diagnostics.get("pages_fetched") or 0),
                        "total_pages": int(diagnostics.get("total_pages") or 0),
                    }
                category_states[category_code] = {
                    "status": "truncated" if is_truncated else "covered" if category_rows else "empty",
                    "count": len(category_rows),
                    "pages_fetched": int(diagnostics.get("pages_fetched") or 0),
                    "total_pages": int(diagnostics.get("total_pages") or 0),
                }
                for incoming in category_rows:
                    announcement_id = str(incoming.get("announcement_id") or "").strip()
                    if not announcement_id:
                        continue
                    existing = rows_by_id.get(announcement_id)
                    if existing is None:
                        rows_by_id[announcement_id] = deepcopy(incoming)
                        continue
                    existing["category_codes"] = list(dict.fromkeys([
                        *(existing.get("category_codes") or []),
                        *(incoming.get("category_codes") or []),
                    ]))
                    existing["category_names"] = list(dict.fromkeys([
                        *(existing.get("category_names") or []),
                        *(incoming.get("category_names") or []),
                    ]))
                    for scalar_key in (
                        "code", "name", "title", "announcement_at", "pdf_url",
                        "size_kb", "file_type", "org_id",
                    ):
                        if not existing.get(scalar_key) and incoming.get(scalar_key):
                            existing[scalar_key] = deepcopy(incoming[scalar_key])
                    if isinstance(existing.get("raw"), dict) and isinstance(incoming.get("raw"), dict):
                        existing["raw"] = {**incoming["raw"], **existing["raw"]}
            rows = sorted(
                rows_by_id.values(),
                key=lambda item: str(item.get("announcement_at") or ""),
                reverse=True,
            )
            selected_periodic = self._select_periodic_reports(rows)
            selected_material = self._select_material_transaction_reports(rows)
            selected_for_text = [*selected_periodic, *selected_material]
            extracted_count = 0
            extraction_failures = 0
            extraction_failure_reasons: Dict[str, int] = {}
            artifacts = self.announcement_artifacts
            if selected_for_text and artifacts is None:
                artifacts = AnnouncementArtifactService()
                self.announcement_artifacts = artifacts
            for row in rows[:120]:
                categories = self._announcement_category_names(row)
                filing_classification = self._classify_periodic_report(row)
                is_periodic = bool(filing_classification)
                result["evidence"].append({
                    "evidence_id": f"announcement:{row['announcement_id']}",
                    "kind": "financial_announcement" if is_periodic else "company_announcement",
                    "source": "巨潮资讯",
                    "title": row.get("title") or "公司公告",
                    "summary": f"公告类型：{' / '.join(categories) or '定期报告'}；PDF 原文可直接核验。",
                    "date": str(row.get("announcement_at") or ""),
                    "url": row.get("pdf_url"),
                    "symbol": symbol,
                    "company": result["subject"].get("name"),
                    "evidence_level": "factual",
                    "original_available": bool(row.get("pdf_url")),
                    "filing_type": filing_classification.get("filing_type") if is_periodic else None,
                    "filing_type_label": filing_classification.get("filing_type_label") if is_periodic else None,
                    "report_period": filing_classification.get("report_period") if is_periodic else None,
                })
            selected_count = len(selected_for_text)
            for selected_index, row in enumerate(selected_for_text, start=1):
                filing_title = _text(row.get("title") or "公司披露文件", 80)
                document_label = (
                    "重大交易公告"
                    if row.get("filing_type") == "material_transaction"
                    else "定期报告"
                )
                notify(f"正在读取巨潮{document_label}正文 {selected_index}/{selected_count} · {filing_title}")
                try:
                    event = {
                        "url": row.get("pdf_url"),
                        "external_id": row.get("announcement_id"),
                        "symbols": [symbol],
                        "metrics": {"announcement_id": row.get("announcement_id")},
                    }
                    if artifacts is None:
                        raise RuntimeError("公告正文提取器未初始化")
                    fetch_text = getattr(artifacts, "fetch_text", None)
                    if callable(fetch_text):
                        parsed = fetch_text(event)
                        full_text = str(parsed.get("text") or "")
                        cached = bool(parsed.get("pdf_cached"))
                        text_cached = bool(parsed.get("cached"))
                        document_hash = str(parsed.get("document_hash") or "")
                        document_bytes = int(parsed.get("document_bytes") or 0)
                        page_count = int(parsed.get("page_count") or 0)
                        pages_extracted = int(parsed.get("pages_extracted") or page_count)
                        extraction_complete = bool(parsed.get("extraction_complete", pages_extracted == page_count > 0))
                        extraction_status = str(parsed.get("extraction_status") or "complete")
                        extraction_method = str(parsed.get("extraction_method") or "pypdf_text")
                        extraction_engine_version = str(parsed.get("extraction_engine_version") or "unknown")
                        extraction_duration_ms = int(parsed.get("extraction_duration_ms") or 0)
                        extraction_fallback_reason = str(parsed.get("fallback_reason") or "")
                    else:
                        # Compatibility for injected test adapters and older
                        # embedders; production uses the hash-bound cache above.
                        pdf_path, cached = artifacts.download_pdf(event)
                        full_text = artifacts.extract_text(pdf_path)
                        text_cached = False
                        document_hash = sha256(pdf_path.read_bytes()).hexdigest() if pdf_path.is_file() else ""
                        document_bytes = pdf_path.stat().st_size if pdf_path.is_file() else 0
                        page_count = 0
                        pages_extracted = 0
                        extraction_complete = False
                        extraction_status = "legacy_adapter_unverified"
                        extraction_method = "pypdf_text"
                        extraction_engine_version = "legacy_adapter"
                        extraction_duration_ms = 0
                        extraction_fallback_reason = "legacy_adapter"
                    excerpts = self._filing_excerpts(full_text)
                    if not excerpts:
                        extraction_failures += 1
                        extraction_failure_reasons["no_selectable_text"] = (
                            extraction_failure_reasons.get("no_selectable_text", 0) + 1
                        )
                        notify(f"巨潮{document_label}正文 {selected_index}/{selected_count} 未提取到可用章节 · {filing_title}")
                        continue
                    extracted_count += 1
                    text_hash = str(parsed.get("text_hash") or "") if callable(fetch_text) else ""
                    if not text_hash:
                        text_hash = sha256(full_text.encode("utf-8", errors="ignore")).hexdigest()
                    filing = {
                        "announcement_id": row.get("announcement_id"),
                        "title": row.get("title"),
                        "date": str(row.get("announcement_at") or ""),
                        "url": row.get("pdf_url"),
                        "cached": cached,
                        "text_cached": text_cached,
                        "document_hash": document_hash,
                        "document_bytes": document_bytes,
                        "page_count": page_count,
                        "pages_extracted": pages_extracted,
                        "text_chars": len(full_text),
                        "text_hash": text_hash,
                        "text_status": "extracted",
                        "extraction_complete": extraction_complete,
                        "extraction_status": extraction_status,
                        "extraction_method": extraction_method,
                        "extraction_engine_version": extraction_engine_version,
                        "extraction_duration_ms": extraction_duration_ms,
                        "extraction_fallback_reason": extraction_fallback_reason,
                        "excerpt_chars": len(excerpts),
                        "excerpt": excerpts,
                        "filing_type": row.get("filing_type"),
                        "filing_type_label": row.get("filing_type_label"),
                        "report_year": row.get("report_year"),
                        "report_period": row.get("report_period"),
                        "classification_basis": row.get("classification_basis"),
                        "category_codes": list(row.get("category_codes") or []),
                        "category_names": self._announcement_category_names(row),
                    }
                    result["filing_documents"].append(filing)
                    result["evidence"].append({
                        "evidence_id": f"filing:{row['announcement_id']}",
                        "kind": "filing_text",
                        "source": "巨潮资讯 PDF 正文",
                        "title": f"{row.get('title') or '定期报告'} · 正文重点章节",
                        "summary": excerpts,
                        "date": str(row.get("announcement_at") or ""),
                        "url": row.get("pdf_url"),
                        "symbol": symbol,
                        "company": result["subject"].get("name"),
                        "evidence_level": "factual",
                        "original_available": True,
                        "document_text": full_text[:80_000],
                        "document_excerpt": excerpts,
                        "document_text_status": "extracted",
                        "document_text_chars": len(full_text),
                        "document_text_hash": text_hash,
                        "document_file_hash": document_hash,
                        "document_bytes": document_bytes,
                        "document_cached": text_cached,
                        "document_pages": page_count,
                        "document_pages_extracted": pages_extracted,
                        "document_extraction_complete": extraction_complete,
                        "document_extraction_status": extraction_status,
                        "extraction_method": extraction_method,
                        "extraction_engine_version": extraction_engine_version,
                        "extraction_duration_ms": extraction_duration_ms,
                        "extraction_fallback_reason": extraction_fallback_reason,
                        "filing_type": row.get("filing_type"),
                        "filing_type_label": row.get("filing_type_label"),
                        "report_year": row.get("report_year"),
                        "report_period": row.get("report_period"),
                        "filing_classification_basis": row.get("classification_basis"),
                    })
                    if text_cached:
                        cache_label = "复用正文缓存"
                    elif extraction_fallback_reason:
                        cache_label = f"完成限时降级解析（{extraction_fallback_reason}）"
                    else:
                        cache_label = "完成 PDFium 首次解析"
                    notify(f"巨潮{document_label}正文 {selected_index}/{selected_count} {cache_label} · {filing_title}")
                except Exception as exc:  # noqa: BLE001 - metadata remains valid when one PDF cannot be parsed.
                    extraction_failures += 1
                    failure_reason = re.sub(
                        r"[^a-z0-9_]+",
                        "_",
                        str(getattr(exc, "code", type(exc).__name__)).casefold(),
                    ).strip("_")[:48] or "unknown"
                    extraction_failure_reasons[failure_reason] = extraction_failure_reasons.get(failure_reason, 0) + 1
                    logger.info("Industry research filing text unavailable: %s", type(exc).__name__)
                    notify(
                        f"巨潮{document_label}正文 {selected_index}/{selected_count} 读取失败"
                        f"（{failure_reason}）· {filing_title}"
                    )
            filing_type_counts: Dict[str, int] = {}
            for document in result["filing_documents"]:
                filing_type = str(document.get("filing_type") or "unclassified")
                filing_type_counts[filing_type] = filing_type_counts.get(filing_type, 0) + 1
            periodic_category_status = {
                code: deepcopy(category_states.get(code) or {
                    "status": "not_attempted",
                    "count": 0,
                    "pages_fetched": 0,
                    "total_pages": 0,
                })
                for code in _PERIODIC_REPORT_CATEGORIES
            }
            periodic_incomplete = {
                code: str(state.get("status") or "unknown")
                for code, state in periodic_category_status.items()
                if str(state.get("status") or "") in {
                    "budget_exhausted", "truncated", "not_attempted",
                } or code in category_failures
            }
            if rows:
                cninfo_status = (
                    "covered"
                    if extracted_count and not extraction_failures
                    and not category_failures and not category_truncated
                    else "partial"
                )
            else:
                cninfo_status = "failed" if category_failures else "missing"
            if (
                selected_for_text
                and extracted_count == len(selected_for_text)
                and not periodic_incomplete
            ):
                filing_text_status = "covered"
            elif extracted_count or extraction_failures or periodic_incomplete:
                filing_text_status = "partial"
            else:
                filing_text_status = "missing"
            result["source_status"].append({
                "key": "cninfo_reports",
                "name": "巨潮全分类公司公告（含定期报告）",
                "status": cninfo_status,
                "count": len(rows),
                "content_count": extracted_count,
                "content_failures": extraction_failures,
                "category_requested": category_total,
                "category_succeeded": category_successes,
                "category_failures": category_failures,
                "category_truncated": category_truncated,
                "category_states": category_states,
                "request_budget": {
                    "limit": cninfo_request_limit,
                    "used": int(request_budget.get("used") or 0),
                    "deadline_seconds": cninfo_deadline_seconds,
                    "max_pages_per_category": cninfo_max_pages,
                },
                "message": (
                    f"检索范围 {start.isoformat()} 至 {end.isoformat()}；覆盖经营、治理、股权、融资、风险和定期报告等"
                    f" {len(ANNOUNCEMENT_CATEGORIES)} 类公告，逐类成功 {category_successes}/{category_total}，"
                    f"去重后取得 {len(rows)} 个 PDF 链接，"
                    f"并读取 {extracted_count} 份最新定期报告/重大交易报告书的业务、经营、风险和财务重点章节。"
                ),
            })
            result["source_status"].append({
                "key": "cninfo_report_text",
                "name": "巨潮定期报告与重大交易 PDF 正文",
                "status": filing_text_status,
                "count": extracted_count,
                "requested": len(selected_for_text),
                "filing_type_counts": filing_type_counts,
                "filing_periods": [
                    str(document.get("report_period") or "")
                    for document in result["filing_documents"]
                    if document.get("report_period")
                ],
                "failure_reasons": extraction_failure_reasons,
                "periodic_category_status": periodic_category_status,
                "periodic_category_incomplete": periodic_incomplete,
                "message": (
                    f"从 {len(selected_for_text)} 份最新且去重的定期报告中成功提取 {extracted_count} 份正文；"
                    "每份正文保留文件/内容哈希、页数完整性、提取引擎、字符数、重点章节和 PDF 原链；"
                    f"未完成项按原因明确记录：{extraction_failure_reasons or '无'}。"
                ),
            })
        except Exception as exc:  # noqa: BLE001 - preserve all other channels.
            result["source_status"].append({
                "key": "cninfo_reports",
                "name": "巨潮全分类公司公告（含定期报告）",
                "status": "failed",
                "count": 0,
                "message": f"{type(exc).__name__}，本次任务将保留失败状态供复核。",
            })
            result["source_status"].append({
                "key": "cninfo_report_text",
                "name": "巨潮定期报告 PDF 正文",
                "status": "failed",
                "count": 0,
                "message": f"{type(exc).__name__}，没有把 PDF 链接误报为已读取正文。",
            })

    @classmethod
    def _select_periodic_reports(cls, rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        max_docs = max(1, min(int(os.getenv("INDUSTRY_RESEARCH_FILING_MAX_DOCS", "6")), 12))
        ranked = sorted(rows, key=lambda row: str(row.get("announcement_at") or ""), reverse=True)
        classified: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in ranked:
            title = str(row.get("title") or "")
            if "摘要" in title or not row.get("pdf_url"):
                continue
            classification = cls._classify_periodic_report(row)
            if not classification:
                # The CNINFO query covers many announcement categories.  An
                # unclassified row is ordinary disclosure metadata, not a
                # periodic report body, and must never be promoted by a broad
                # "定期报告" fallback.
                continue
            normalized = {**row, **classification}
            key = str(classification.get("report_period") or "") or (
                f"{classification.get('report_year')}:{classification.get('filing_type')}"
            )
            if key in seen:
                continue
            seen.add(key)
            classified.append(normalized)

        # Reserve the first slots for one annual, one interim and one quarter
        # so a run cannot spend its bounded PDF budget on several adjacent
        # quarters while silently omitting the annual report.
        output: List[Dict[str, Any]] = []
        groups = (
            ("annual", {"annual"}),
            ("interim", {"interim"}),
            ("quarter", {"q1", "q3", "quarter"}),
        )
        for _name, filing_types in groups:
            match = next(
                (item for item in classified if str(item.get("filing_type") or "") in filing_types),
                None,
            )
            if match is not None and len(output) < max_docs:
                output.append(match)
        for item in classified:
            if len(output) >= max_docs:
                break
            if item not in output:
                output.append(item)
        return output

    @classmethod
    def _select_material_transaction_reports(
        cls,
        rows: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Select a bounded set of primary M&A/asset-purchase report bodies.

        Announcement metadata alone cannot substantiate the target company's
        financials, performance commitments or consolidation boundary.  The
        latest exchange-filed transaction report is therefore read alongside
        periodic reports.  Intermediary opinions, valuation appendices and
        summaries are excluded so the bounded PDF budget is spent on the
        issuer's primary report rather than dozens of supporting documents.
        """

        try:
            max_docs = max(0, min(
                int(os.getenv("INDUSTRY_RESEARCH_MATERIAL_FILING_MAX_DOCS", "1")), 2,
            ))
        except (TypeError, ValueError):
            max_docs = 1
        if not max_docs:
            return []
        primary_patterns = (
            "重大资产重组报告书",
            "发行股份及支付现金购买资产并募集配套资金暨关联交易报告书",
            "发行股份及支付现金购买资产并募集配套资金报告书",
            "发行股份及支付现金购买资产暨关联交易报告书",
            "发行股份及支付现金购买资产报告书",
            "购买资产报告书",
            "收购报告书",
            "重组报告书",
        )
        excluded_markers = (
            "摘要", "评估报告", "资产评估", "估值报告", "审计报告", "审阅报告",
            "法律意见", "律师工作报告", "核查意见", "独立财务顾问", "问询", "回复",
            "关于披露", "风险提示", "提示性公告", "提示公告", "进展公告", "停牌公告",
        )

        def _primary_rank(row: Dict[str, Any]) -> tuple[int, str]:
            title = re.sub(r"\s+", "", str(row.get("title") or ""))
            # Prefer the issuer's complete transaction-report body over loose
            # titles such as a generic takeover report.  Recency remains the
            # tie-breaker, so a revised draft replaces its earlier draft.
            specificity = 0
            for index, pattern in enumerate(primary_patterns):
                if pattern in title:
                    specificity = len(primary_patterns) - index
                    break
            main_body = int(
                "报告书（草案" in title
                or "报告书(草案" in title
                or "报告书（修订稿" in title
                or "报告书(修订稿" in title
            )
            return (main_body * 100 + specificity, str(row.get("announcement_at") or ""))

        ranked = sorted(rows, key=_primary_rank, reverse=True)
        selected: List[Dict[str, Any]] = []
        for row in ranked:
            title = re.sub(r"\s+", "", str(row.get("title") or ""))
            if not row.get("pdf_url") or any(marker in title for marker in excluded_markers):
                continue
            if not any(pattern in title for pattern in primary_patterns):
                continue
            selected.append({
                **row,
                "filing_type": "material_transaction",
                "filing_type_label": _FILING_TYPE_LABELS["material_transaction"],
                "report_year": None,
                "report_period": None,
                "classification_basis": "material_transaction_title",
                "report_year_basis": None,
            })
            if len(selected) >= max_docs:
                break
        return selected

    @classmethod
    def _classify_periodic_report(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        """Return an auditable filing type/period, or empty for non-filings."""

        title = re.sub(r"\s+", "", str(row.get("title") or ""))
        filing_type = ""
        basis = ""
        title_report_year = 0
        # CNINFO category endpoints can contain governance documents whose
        # titles merely mention "年报" (for example, an annual-report error
        # accountability policy).  A loose substring match promotes those
        # documents into filing evidence and can displace the real annual
        # report under the bounded PDF budget.  Require the filing phrase to
        # be the semantic end of the title, while still accepting explicit
        # full-text and revision suffixes used by real filings.
        revision_suffix = (
            r"(?:[（(](?:修订(?:版|稿)?|更正(?:版|稿|后)?|更新(?:版|稿|后)?|"
            r"第[一二三四五六七八九十\d]+次修订)[）)])?"
        )
        report_suffix = rf"(?:全文|正文)?{revision_suffix}$"
        non_filing_markers = (
            "摘要", "英文", "已取消", "制度", "规则", "指引", "办法", "责任追究",
            "说明会", "问询", "回复", "提示性", "延期", "预约", "审计机构", "审议",
            "社会责任", "可持续发展", "ESG", "环境、社会", "内部控制", "债券", "受托管理",
        )
        if any(marker.casefold() in title.casefold() for marker in non_filing_markers):
            return {}
        title_rules = (
            ("interim", rf"(?P<year>20\d{{2}})年?(?:半年度报告|半年报|中期报告){report_suffix}"),
            ("q1", rf"(?P<year>20\d{{2}})年?(?:第一季度报告|一季度报告|第1季度报告){report_suffix}"),
            ("q3", rf"(?P<year>20\d{{2}})年?(?:第三季度报告|三季度报告|第3季度报告){report_suffix}"),
            ("annual", rf"(?P<year>20\d{{2}})(?:年年度报告|年度报告|年年报|年报){report_suffix}"),
        )
        for candidate, pattern in title_rules:
            match = re.search(pattern, title)
            if match:
                filing_type, basis = candidate, "title"
                title_report_year = int(match.group("year"))
                break

        # A small number of upstream records use the generic title "定期报告
        # 全文（修订版）".  Only that explicit generic-filing form may use a
        # unique category as a fallback; arbitrary titles must never inherit
        # filing identity from the category endpoint that happened to return
        # them.
        generic_periodic_title = bool(
            re.search(rf"定期报告(?:全文|正文){revision_suffix}$", title)
        )
        if not filing_type and generic_periodic_title:
            category_rules = (
                ("interim", r"半年度报告|半年报|中期报告"),
                ("q1", r"第一季度报告|一季度报告|第1季度报告|一季报"),
                ("q3", r"第三季度报告|三季度报告|第3季度报告|三季报"),
                ("annual", r"(?<!半)年度报告|(?<!半)年报"),
                ("quarter", r"季度报告"),
            )
            code_types = {
                _FILING_CATEGORY_TYPES[code]
                for code in (row.get("category_codes") or [])
                if code in _FILING_CATEGORY_TYPES
            }
            name_types: set[str] = set()
            category_texts = [
                str(value)
                for value in (row.get("category_names") or [])
                if str(value).strip()
            ]
            for category_text in category_texts:
                for candidate, pattern in category_rules:
                    if re.search(pattern, category_text):
                        name_types.add(candidate)
            category_types = code_types | name_types
            if len(category_types) == 1:
                filing_type = next(iter(category_types))
                basis = "category_code" if code_types else "category_name"
        if not filing_type:
            return {}

        announcement_raw = str(row.get("announcement_at") or "")
        announcement_digits = re.sub(r"\D", "", announcement_raw)
        report_year = title_report_year
        year_basis = "title" if report_year else "announcement_date"
        if not report_year and len(announcement_digits) >= 4:
            report_year = int(announcement_digits[:4])
            # Annual reports are normally published in the following calendar
            # year.  This is explicit inference metadata, never presented as a
            # title-derived period.
            month = int(announcement_digits[4:6]) if len(announcement_digits) >= 6 else 0
            if filing_type == "annual" and 1 <= month <= 6:
                report_year -= 1
                year_basis = "announcement_date_previous_year"
            elif filing_type == "annual":
                report_year = 0
                year_basis = "announcement_date_unresolved"
        period_suffix = {
            "annual": "12-31", "interim": "06-30", "q1": "03-31", "q3": "09-30",
        }.get(filing_type)
        report_period = f"{report_year:04d}-{period_suffix}" if report_year and period_suffix else None
        return {
            "filing_type": filing_type,
            "filing_type_label": _FILING_TYPE_LABELS[filing_type],
            "report_year": report_year or None,
            "report_period": report_period,
            "classification_basis": basis,
            "report_year_basis": year_basis if report_year else None,
        }

    @staticmethod
    def _announcement_category_names(row: Dict[str, Any]) -> List[str]:
        """Avoid labelling every row as every category after a multi-category query."""

        category_codes = list(row.get("category_codes") or [])
        category_names = [str(value) for value in (row.get("category_names") or []) if str(value).strip()]
        if category_names and len(category_codes) < len(_COMPANY_ANNOUNCEMENT_CATEGORIES):
            return category_names
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        upstream = str(
            raw.get("announcementTypeName") or raw.get("categoryName") or raw.get("announcementType") or ""
        ).strip()
        if upstream:
            values = [value.strip() for value in re.split(r"[,;/|]", upstream) if value.strip()]
            if values:
                return values[:4]
        title = str(row.get("title") or "")
        rules = (
            ("半年度报告", "半年报"), ("一季度报告", "一季报"), ("三季度报告", "三季报"),
            ("年度报告", "年报"), ("业绩预告", "业绩预告"), ("业绩快报", "业绩预告"),
            ("权益分派", "权益分派"), ("董事会", "董事会"), ("监事会", "监事会"),
            ("股东大会", "股东会"), ("股东会", "股东会"), ("股权激励", "股权激励"),
            ("解除限售", "解禁"), ("解禁", "解禁"), ("可转换公司债", "可转债"),
            ("可转债", "可转债"), ("公司债", "公司债"), ("增发", "增发"), ("配股", "配股"),
            ("回购", "股权变动"), ("减持", "股权变动"), ("增持", "股权变动"),
            ("更正", "补充更正"), ("补充公告", "补充更正"), ("澄清", "澄清致歉"),
            ("风险提示", "风险提示"), ("退市", "特别处理和退市"),
            ("中标", "日常经营"), ("合同", "日常经营"), ("订单", "日常经营"),
            ("公司治理", "公司治理"),
        )
        inferred = [name for keyword, name in rules if keyword in title]
        return list(dict.fromkeys(inferred))[:4] or ["公司公告"]

    @staticmethod
    def _filing_excerpts(value: str, max_chars: int = 24_000) -> str:
        text_value = re.sub(r"[ \t]+", " ", str(value or "")).strip()
        if not text_value:
            return ""
        headings = (
            "公司业务概要", "公司简介和主要财务指标", "管理层讨论与分析", "经营情况讨论与分析",
            "核心竞争力分析", "核心竞争力", "未来发展的展望", "公司未来发展的展望",
            "公司面临的风险和应对措施", "可能面对的风险", "主要会计数据和财务指标",
            "重要会计政策及会计估计", "财务报表附注", "与金融工具相关的风险",
            "本次交易方案概况", "本次交易概况", "交易标的基本情况", "标的资产基本情况",
            "交易标的财务状况", "标的公司财务状况", "业绩承诺及补偿安排",
            "交易作价及评估情况", "本次交易对上市公司的影响", "风险因素",
        )
        chunks: List[str] = []
        seen_ranges: List[tuple[int, int]] = []
        for heading in headings:
            positions = [match.start() for match in re.finditer(re.escape(heading), text_value)]
            if not positions:
                continue
            # Exchange PDFs normally repeat each major heading once in the
            # table of contents and once at the real chapter.  The old
            # first-match rule therefore returned dotted leaders and page
            # numbers instead of the filing body.  Prefer the first materially
            # later occurrence; retain the only occurrence for short reports.
            position = positions[0]
            for candidate in positions[1:]:
                if candidate - positions[0] >= 500:
                    position = candidate
                    break
            start = max(0, position - 120)
            end = min(len(text_value), position + 4200)
            if any(abs(start - prior_start) < 600 for prior_start, _prior_end in seen_ranges):
                continue
            seen_ranges.append((start, end))
            chunks.append(f"【{heading}】\n{text_value[start:end]}")
            if sum(len(chunk) for chunk in chunks) >= max_chars:
                break
        if not chunks:
            chunks.append(text_value[:max_chars])
        return "\n\n".join(chunks)[:max_chars]

    def _collect_structured_essays(
        self,
        result: Dict[str, Any],
        *,
        topic: str,
        terms: Sequence[str],
        lookback_days: int,
    ) -> None:
        """Add completed AI analyses as explicitly derived, unverified evidence.

        The raw knowledge-planet note remains the primary source.  These rows
        carry the already persisted extraction (key points, industries,
        earnings/valuation transmission, contradictions and falsification
        conditions) so a long report does not throw away the expensive
        structure and then infer it again from a truncated original.
        """
        subject = result.get("subject") or {}
        candidates = [
            topic,
            subject.get("name"),
            subject.get("company_name"),
            subject.get("symbol"),
            str(subject.get("symbol") or "").split(".", 1)[0],
            *terms,
        ]
        needles: List[str] = []
        for value in candidates:
            normalized = str(value or "").strip()
            if len(normalized) < 2 or normalized.casefold() in {item.casefold() for item in needles}:
                continue
            needles.append(normalized)
            if len(needles) >= 12:
                break
        if not needles:
            result["source_status"].append({
                "key": "structured_essays",
                "name": "机构段子 AI 结构化结论",
                "status": "missing",
                "count": 0,
                "message": "研究对象没有可用于本地语料匹配的名称、代码或关键词。",
            })
            return

        columns = (
            ResearchNote.title,
            ResearchNote.content,
            ResearchNote.symbol_codes,
            EssayAnalysisRecord.summary,
            EssayAnalysisRecord.tags_json,
            EssayAnalysisRecord.industries_json,
            EssayAnalysisRecord.themes_json,
            EssayAnalysisRecord.stock_mentions_json,
            EssayAnalysisRecord.key_points_json,
        )
        clauses = []
        for needle in needles:
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.extend(column.like(pattern, escape="\\") for column in columns)
        cutoff = utc_naive_now() - timedelta(days=max(1, min(int(lookback_days), 3650)))
        try:
            db = self.db or DatabaseManager.get_instance()
            self.db = db
            with db.get_session() as session:
                rows = session.execute(
                    select(EssayAnalysisRecord, ResearchNote)
                    .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                    .where(
                        EssayAnalysisRecord.status == "completed",
                        ResearchNote.created_at >= cutoff,
                        or_(*clauses),
                    )
                    .order_by(
                        EssayAnalysisRecord.importance_score.desc(),
                        ResearchNote.created_at.desc(),
                    )
                    .limit(80)
                ).all()
        except Exception as exc:  # noqa: BLE001 - local corpus is additive to official sources.
            logger.info("Structured essay evidence unavailable: %s", type(exc).__name__)
            result["source_status"].append({
                "key": "structured_essays",
                "name": "机构段子 AI 结构化结论",
                "status": "failed",
                "count": 0,
                "message": f"{type(exc).__name__}，报告仍保留官方、行情与互联网来源。",
            })
            return

        for analysis, note in rows:
            raw = self._json_value(analysis.raw_response, {})
            key_points = self._json_value(analysis.key_points_json, [])
            catalysts = self._json_value(analysis.catalysts_json, [])
            risks = self._json_value(analysis.risks_json, [])
            industries = self._json_value(analysis.industries_json, [])
            themes = self._json_value(analysis.themes_json, [])
            mentions = self._json_value(analysis.stock_mentions_json, [])
            contradictions = raw.get("contradictions") if isinstance(raw, dict) else []
            falsification = raw.get("falsification_conditions") if isinstance(raw, dict) else []
            monitoring = raw.get("monitoring_points") if isinstance(raw, dict) else []
            earnings = str(raw.get("earnings_impact") or "").strip() if isinstance(raw, dict) else ""
            valuation = str(raw.get("valuation_impact") or "").strip() if isinstance(raw, dict) else ""
            sections = [str(analysis.summary or "").strip()]
            for label, value in (
                ("核心要点", key_points),
                ("催化线索", catalysts),
                ("风险线索", risks),
                ("矛盾与待核验", contradictions),
                ("证伪条件", falsification),
                ("后续跟踪", monitoring),
            ):
                rendered = self._analysis_list_text(value, limit=6)
                if rendered:
                    sections.append(f"{label}：{rendered}")
            if earnings and earnings != "信息不足":
                sections.append(f"盈利传导：{earnings}")
            if valuation and valuation != "信息不足":
                sections.append(f"估值传导：{valuation}")
            summary = _text("\n".join(value for value in sections if value), 6000)
            result["evidence"].append({
                "evidence_id": f"note_analysis:{note.topic_id}",
                "kind": "institution_note",
                "source": f"{note.group_name} · AI 结构化分析",
                "title": f"{note.title} · 结构化结论",
                "summary": summary or "该机构段子已完成结构化分析，但没有可展示的有效结论。",
                "date": note.created_at.isoformat() if isinstance(note.created_at, datetime) else str(note.created_at or ""),
                "url": f"/essay-radar/feed?topic={note.topic_id}",
                "symbol": note.symbol_codes or self._mention_symbols(mentions),
                "company": subject.get("name") if subject.get("research_type") == "company" else None,
                "evidence_level": "unverified",
                "original_available": True,
                "derived_from": f"note:{note.topic_id}",
                "is_derived": True,
                "model": analysis.model,
                "importance": int(analysis.importance_score or 0),
                "confidence": _number(analysis.confidence_score),
                "structured_analysis": {
                    "category": analysis.primary_category,
                    "sentiment": analysis.sentiment,
                    "time_horizon": analysis.time_horizon,
                    "industries": industries,
                    "themes": themes,
                    "stock_mentions": mentions,
                    "key_points": key_points,
                    "catalysts": catalysts,
                    "risks": risks,
                    "contradictions": contradictions or [],
                    "falsification_conditions": falsification or [],
                    "monitoring_points": monitoring or [],
                    "earnings_impact": earnings or None,
                    "valuation_impact": valuation or None,
                },
            })
        result["source_status"].append({
            "key": "structured_essays",
            "name": "机构段子 AI 结构化结论",
            "status": "covered" if rows else "missing",
            "count": len(rows),
            "message": (
                "已读取原文对应的持久化 AI 标签、核心要点、盈利/估值传导、矛盾与证伪条件；"
                "该层属于待核验观点，不作为独立公司事实。"
                if rows else "在当前时间窗和关键词下没有命中已完成 AI 分析的机构段子。"
            ),
        })

    @staticmethod
    def _json_value(value: Any, fallback: Any) -> Any:
        if value in (None, ""):
            return fallback
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback

    @classmethod
    def _analysis_list_text(cls, value: Any, *, limit: int) -> str:
        values = value if isinstance(value, list) else [value] if value else []
        rendered: List[str] = []
        for item in values[:limit]:
            if isinstance(item, dict):
                item = next((item.get(key) for key in ("text", "point", "name", "summary", "value") if item.get(key)), item)
            text_value = _text(item, 360)
            if text_value:
                rendered.append(text_value)
        return "；".join(rendered)

    @staticmethod
    def _mention_symbols(value: Any) -> str:
        if not isinstance(value, list):
            return ""
        symbols: List[str] = []
        for item in value:
            if isinstance(item, dict):
                raw = item.get("ts_code") or item.get("stock_code") or item.get("symbol")
            else:
                raw = item
            canonical = _canonical_a_share(raw)
            if canonical and canonical not in symbols:
                symbols.append(canonical)
        return ",".join(symbols)

    def _collect_broker_report_text(
        self,
        result: Dict[str, Any],
        *,
        topic: str,
        terms: Sequence[str],
        lookback_days: int,
    ) -> None:
        """Read a few recent, high-relevance PDFs from the local report index.

        Candidate discovery is SQLite-only.  Network access is delegated to a
        strict HTTPS allowlist service and every selected PDF is isolated so a
        malformed or scanned document cannot abort the research task.
        """

        subject = result.get("subject") or {}
        candidates = [
            topic,
            subject.get("name"),
            subject.get("company_name"),
            subject.get("symbol"),
            str(subject.get("symbol") or "").split(".", 1)[0],
            *terms,
        ]
        needles: List[str] = []
        for value in candidates:
            normalized = str(value or "").strip()
            if len(normalized) < 2 or normalized.casefold() in {item.casefold() for item in needles}:
                continue
            needles.append(normalized)
            if len(needles) >= 16:
                break
        if not needles:
            self._append_broker_report_status(
                result, status="missing", matched=0, requested=0, extracted=0, failures=[],
                message="研究对象没有可用于本地券商研报库匹配的名称、代码或关键词。",
            )
            return

        try:
            max_docs = max(1, min(int(os.getenv("INDUSTRY_RESEARCH_BROKER_PDF_MAX_DOCS", "4")), 10))
        except (TypeError, ValueError):
            max_docs = 4
        cutoff = date.today() - timedelta(days=max(30, min(int(lookback_days), 3650)))
        columns = (
            ResearchReportRecord.title,
            ResearchReportRecord.abstract,
            ResearchReportRecord.industry,
            ResearchReportRecord.company_name,
            ResearchReportRecord.ts_code,
        )
        clauses = []
        for needle in needles:
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.extend(column.like(pattern, escape="\\") for column in columns)
        try:
            db = self.db or DatabaseManager.get_instance()
            self.db = db
            with db.get_session() as session:
                rows = session.execute(
                    select(ResearchReportRecord)
                    .where(and_(
                        ResearchReportRecord.trade_date >= cutoff,
                        ResearchReportRecord.pdf_url.is_not(None),
                        ResearchReportRecord.pdf_url != "",
                        or_(*clauses),
                    ))
                    .order_by(desc(ResearchReportRecord.trade_date), desc(ResearchReportRecord.id))
                    .limit(160)
                ).scalars().all()
        except Exception as exc:  # noqa: BLE001 - report PDFs are additive to other research sources.
            logger.info("Broker report metadata lookup unavailable: %s", type(exc).__name__)
            self._append_broker_report_status(
                result, status="failed", matched=0, requested=0, extracted=0,
                failures=[{"error": f"metadata_lookup:{type(exc).__name__}"}],
                message=f"本地券商研报索引读取失败：{type(exc).__name__}；其他来源继续处理。",
            )
            return

        ranked = sorted(
            rows,
            key=lambda row: (
                self._broker_report_relevance(row, needles, subject),
                row.trade_date or date.min,
                int(row.id or 0),
            ),
            reverse=True,
        )
        selected: List[ResearchReportRecord] = []
        seen_urls: set[str] = set()
        for row in ranked:
            url = str(row.pdf_url or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            selected.append(row)
            if len(selected) >= max_docs:
                break
        if not selected:
            self._append_broker_report_status(
                result, status="missing", matched=len(rows), requested=0, extracted=0, failures=[],
                message="本地研报库在当前时间窗和关键词下没有可读取的 PDF 链接。",
            )
            return

        failures: List[Dict[str, str]] = []
        extracted = 0
        try:
            artifacts = self.broker_report_artifacts or BrokerReportArtifactService()
            self.broker_report_artifacts = artifacts
        except Exception as exc:  # noqa: BLE001 - invalid configuration must be visible but non-fatal.
            self._append_broker_report_status(
                result, status="failed", matched=len(rows), requested=len(selected), extracted=0,
                failures=[{"error": f"artifact_config:{type(exc).__name__}"}],
                message=f"券商研报正文解析器初始化失败：{type(exc).__name__}；没有把 PDF 链接误报为正文。",
            )
            return

        for row in selected:
            try:
                parsed = artifacts.fetch_text(str(row.pdf_url or ""))
                text_value = str(parsed.get("text") or "").strip()
                if not text_value:
                    raise BrokerReportArtifactError("券商研报 PDF 未提取到可用文字")
                extracted += 1
                evidence_id = f"broker_report_text:{row.report_key}"
                document = {
                    "report_key": row.report_key,
                    "title": row.title,
                    "date": row.trade_date.isoformat() if row.trade_date else None,
                    "broker": row.broker,
                    "url": row.pdf_url,
                    "relevance_score": self._broker_report_relevance(row, needles, subject),
                    "cached": bool(parsed.get("cached")),
                    "fetched_at": parsed.get("fetched_at"),
                    "document_bytes": int(parsed.get("document_bytes") or 0),
                    "document_hash": parsed.get("document_hash"),
                    "text_chars": int(parsed.get("text_chars") or len(text_value)),
                    "text_hash": parsed.get("text_hash"),
                    "page_count": int(parsed.get("page_count") or 0),
                    "pages_read": int(parsed.get("pages_read") or 0),
                    "text_status": "extracted",
                }
                result.setdefault("broker_report_documents", []).append(document)
                result["evidence"].append({
                    "evidence_id": evidence_id,
                    "kind": "broker_report_text",
                    "source": f"{row.broker or row.source or '券商研报'} · PDF 正文",
                    "title": f"{row.title} · PDF 正文",
                    "summary": text_value[:24_000],
                    "date": row.trade_date.isoformat() if row.trade_date else "",
                    "url": row.pdf_url,
                    "symbol": row.ts_code,
                    "company": row.company_name,
                    "evidence_level": "reported",
                    "original_available": True,
                    "importance": 78,
                    "document_text": text_value,
                    "document_text_status": "extracted",
                    "document_text_chars": document["text_chars"],
                    "document_text_hash": document["text_hash"],
                    "document_file_hash": document["document_hash"],
                    "document_bytes": document["document_bytes"],
                    "document_cached": document["cached"],
                    "document_fetched_at": document["fetched_at"],
                    "document_pages": document["page_count"],
                    "document_pages_read": document["pages_read"],
                    "extraction_method": parsed.get("extraction_method") or "pypdf_text",
                    "relevance_score": document["relevance_score"],
                })
            except Exception as exc:  # noqa: BLE001 - one report must never fail the full research task.
                logger.info("Broker report text unavailable for %s: %s", row.report_key, type(exc).__name__)
                failures.append({
                    "report_key": str(row.report_key),
                    "title": _text(row.title, 160),
                    "error": f"{type(exc).__name__}: {_text(exc, 180)}",
                })

        status = (
            "covered"
            if extracted == len(selected) and not failures
            else "partial" if extracted else "failed"
        )
        self._append_broker_report_status(
            result,
            status=status,
            matched=len(rows),
            requested=len(selected),
            extracted=extracted,
            failures=failures,
            message=(
                f"从本地匹配到 {len(rows)} 篇带 PDF 的券商研报，按相关度和发布日期选择 {len(selected)} 篇；"
                f"成功读取 {extracted} 篇正文，失败 {len(failures)} 篇。下载仅限显式允许的 HTTPS 公网域名并复用本地缓存。"
            ),
        )

    @staticmethod
    def _broker_report_relevance(
        row: ResearchReportRecord,
        needles: Sequence[str],
        subject: Dict[str, Any],
    ) -> int:
        title = str(row.title or "").casefold()
        abstract = str(row.abstract or "").casefold()
        industry = str(row.industry or "").casefold()
        company = str(row.company_name or "").casefold()
        symbol = str(row.ts_code or "").casefold()
        score = 0
        subject_name = str(subject.get("name") or subject.get("company_name") or "").strip().casefold()
        subject_symbol = str(subject.get("symbol") or "").strip().casefold()
        if subject_name and company == subject_name:
            score += 90
        if subject_name and subject_name in title:
            score += 70
        if subject_symbol and symbol == subject_symbol:
            score += 100
        for raw in needles:
            value = str(raw or "").strip().casefold()
            if len(value) < 2:
                continue
            score += 24 if value in title else 0
            score += 16 if value in company else 0
            score += 12 if value in industry else 0
            score += 4 if value in abstract else 0
            score += 20 if value == symbol else 0
        return score

    @staticmethod
    def _append_broker_report_status(
        result: Dict[str, Any],
        *,
        status: str,
        matched: int,
        requested: int,
        extracted: int,
        failures: Sequence[Dict[str, str]],
        message: str,
    ) -> None:
        result["source_status"].append({
            "key": "research_report_fulltext",
            "name": "券商研报 PDF 正文",
            "status": status,
            "count": extracted,
            "matched": matched,
            "requested": requested,
            "content_count": extracted,
            "content_failures": len(failures),
            "failures": list(failures)[:10],
            "message": message,
        })

    def _collect_web(
        self,
        result: Dict[str, Any],
        topic: str,
        symbol: str,
        terms: Sequence[str],
        research_type: str,
    ) -> None:
        rows: List[Any] = []
        provider_names: Sequence[str] = ()
        try:
            service = get_search_service()
            if not service.is_available:
                raise RuntimeError("未配置可用搜索通道")
            provider_names = service.available_provider_names
            # Public SearXNG nodes are useful as a last resort, but are often
            # rate-limited.  Managed/self-hosted providers retain the richer
            # multi-dimensional search; otherwise the keyless RSS fallback is
            # both faster and more predictable for background research jobs.
            has_managed_provider = any(name.casefold() != "searxng" for name in provider_names)
            if has_managed_provider:
                intel = service.search_comprehensive_intel(
                    symbol,
                    str(result["subject"].get("name") or topic),
                    max_searches=6 if research_type == "company" else 5,
                )
                for dimension, response in intel.items():
                    if not response or not response.success:
                        continue
                    for item in response.results or []:
                        rows.append((dimension, response.provider, item))
            for dimension, provider, item in rows:
                stable = sha256(str(item.url or f"{item.title}:{item.snippet}").encode("utf-8")).hexdigest()[:20]
                result["evidence"].append({
                    "evidence_id": f"web:{stable}",
                    "kind": f"web_{dimension}",
                    "source": item.source or provider,
                    "title": item.title,
                    "summary": _text(item.snippet, 1400),
                    "date": item.published_date,
                    "url": item.url,
                    "symbol": symbol or None,
                    "company": result["subject"].get("name") if research_type == "company" else None,
                    "evidence_level": "reported",
                    "original_available": bool(item.url),
                    "relevance_score": item.relevance_score,
                    "relevance_category": item.relevance_category,
                })
            fallback_rows = []
            if len(rows) < 6:
                fallback_rows = self._google_news_fallback(
                    topic=str(result["subject"].get("name") or topic),
                    symbol=symbol,
                    terms=terms,
                    research_type=research_type,
                    limit=max(0, 18 - len(rows)),
                )
                result["evidence"].extend(fallback_rows)
            total = len(rows) + len(fallback_rows)
            traceable_count = sum(
                1 for _dimension, _provider, item in rows if str(getattr(item, "url", "") or "").strip()
            ) + sum(1 for item in fallback_rows if str(item.get("url") or "").strip())
            result["source_status"].append({
                "key": "web_search",
                "name": "互联网多维检索",
                "status": "covered" if traceable_count else "partial" if total else "missing",
                "count": traceable_count,
                "matched": total,
                "untraceable_count": max(0, total - traceable_count),
                "message": (
                    "优先使用已配置搜索 API，并以 Google News RSS 作为无密钥降级；"
                    f"召回 {total} 条，其中 {traceable_count} 条保留标题、来源、时间与可追溯原链接。"
                ),
            })
        except Exception as exc:  # noqa: BLE001 - web search is additive, never a report blocker.
            logger.info("Industry research web search unavailable: %s", type(exc).__name__)
            fallback_rows = self._google_news_fallback(
                topic=str(result["subject"].get("name") or topic),
                symbol=symbol,
                terms=terms,
                research_type=research_type,
                limit=18,
            )
            result["evidence"].extend(fallback_rows)
            traceable_count = sum(1 for item in fallback_rows if str(item.get("url") or "").strip())
            result["source_status"].append({
                "key": "web_search",
                "name": "互联网多维检索",
                "status": "covered" if traceable_count else "partial" if fallback_rows else "failed",
                "count": traceable_count,
                "matched": len(fallback_rows),
                "untraceable_count": max(0, len(fallback_rows) - traceable_count),
                "message": (
                    f"主搜索通道不可用，已自动切换 Google News RSS；保留 {traceable_count} 条可追溯原链接。"
                    if fallback_rows else f"{type(exc).__name__}，报告仍使用本地与官方数据。"
                ),
            })

        self._collect_web_fulltext(
            result,
            topic=topic,
            terms=terms,
            research_type=research_type,
        )

    def _collect_web_fulltext(
        self,
        result: Dict[str, Any],
        *,
        topic: str,
        terms: Sequence[str],
        research_type: str,
    ) -> None:
        """Read a few allowlisted, high-relevance pages found by web search."""

        subject = result.get("subject") or {}
        web_rows = [
            item for item in result.get("evidence") or []
            if isinstance(item, dict)
            and str(item.get("kind") or "").startswith("web_")
            and str(item.get("kind") or "") != "web_fulltext"
            and str(item.get("url") or "").strip()
        ]
        official_host = ""
        official_failures: List[Dict[str, str]] = []
        raw_official_url = str(subject.get("website") or "").strip()
        if research_type == "company" and raw_official_url:
            try:
                official_url, official_host = WebArticleArtifactService.normalize_exact_https_url(
                    raw_official_url
                )
                web_rows.insert(0, {
                    "evidence_id": (
                        "web:company-official:"
                        f"{sha256(official_url.encode('utf-8')).hexdigest()[:24]}"
                    ),
                    "kind": "web_company_official",
                    "source": "公司官网 · Tushare stock_company",
                    "title": f"{subject.get('name') or topic} 公司官网",
                    "summary": "Tushare stock_company 披露的公司官网；正文需通过安全抓取和主体校验。",
                    # The profile does not disclose when the homepage was
                    # published. Retrieval time is recorded after fetching.
                    "date": None,
                    "url": official_url,
                    "symbol": subject.get("symbol"),
                    "company": subject.get("name") or topic,
                    "evidence_level": "factual",
                    "original_available": True,
                    "relevance_score": 100,
                    "relevance_category": "official_company",
                })
            except WebArticleArtifactError as exc:
                official_failures.append({
                    "url": _text(raw_official_url, 300),
                    "title": _text(f"{subject.get('name') or topic} 公司官网", 160),
                    "error": f"WebArticleArtifactError: {_text(exc, 180)}",
                })
        subject_symbol = _canonical_a_share(subject.get("symbol"))
        if research_type == "company" and subject_symbol.endswith(".SH"):
            stock_code = subject_symbol.split(".", 1)[0]
            exchange_url = (
                "https://www.sse.com.cn/disclosure/listedinfo/announcement/"
                f"?productId={stock_code}"
            )
            web_rows.append({
                "evidence_id": (
                    "web:company-exchange:"
                    f"{sha256(exchange_url.encode('utf-8')).hexdigest()[:24]}"
                ),
                "kind": "web_company_exchange",
                "source": "上海证券交易所 · 上市公司公开信息",
                "title": f"{subject.get('name') or topic}（{subject_symbol}）上交所公开信息",
                "summary": "按证券代码定位的上交所上市公司公告与公开信息页面。",
                "date": None,
                "url": exchange_url,
                "symbol": subject_symbol,
                "company": subject.get("name") or topic,
                "evidence_level": "factual",
                "original_available": True,
                "relevance_score": 98,
                "relevance_category": "official_exchange",
            })
        if not web_rows:
            self._append_web_fulltext_status(
                result, status="missing", matched=1 if raw_official_url else 0,
                eligible=0, requested=0, extracted=0,
                failures=official_failures,
                message=(
                    "公司资料中的官网未通过 HTTPS 公网域名安全校验，且互联网检索没有返回可读取链接。"
                    if official_failures else "互联网检索没有返回可读取的网页链接。"
                ),
            )
            return

        try:
            max_docs = max(1, min(int(os.getenv("INDUSTRY_RESEARCH_WEB_MAX_DOCS", "4")), 8))
        except (TypeError, ValueError):
            max_docs = 4
        try:
            artifacts = self.web_artifacts or WebArticleArtifactService()
            self.web_artifacts = artifacts
            # Add only the resolved company's exact official host to this
            # collection call. The process-global allowlist is not mutated,
            # and child/sibling hosts remain outside this exact scope.
            if official_host and not artifacts.can_fetch(str(web_rows[0].get("url") or "")):
                artifacts = artifacts.with_exact_allowed_hosts([official_host])
        except Exception as exc:  # noqa: BLE001 - configuration errors are visible and isolated.
            self._append_web_fulltext_status(
                result, status="failed", matched=len(web_rows), eligible=0, requested=0, extracted=0,
                failures=[*official_failures, {"error": f"artifact_config:{type(exc).__name__}"}],
                message=f"网页正文解析器初始化失败：{type(exc).__name__}；搜索摘要不会被冒充为已读全文。",
            )
            return

        primary_values = [topic, subject.get("name"), subject.get("symbol")]
        primary_needles = self._normalized_web_needles(primary_values)
        relevance_needles = self._normalized_web_needles([*primary_values, *terms])
        deduplicated: Dict[str, Dict[str, Any]] = {}
        for item in web_rows:
            url = str(item.get("url") or "").strip()
            if url and url not in deduplicated:
                deduplicated[url] = item
        eligible_rows = [item for item in deduplicated.values() if artifacts.can_fetch(str(item.get("url") or ""))]
        ranked = sorted(
            eligible_rows,
            key=lambda item: self._web_result_rank(item, relevance_needles),
            reverse=True,
        )
        selected = ranked[:max_docs]
        if not selected:
            self._append_web_fulltext_status(
                result, status="failed", matched=len(deduplicated), eligible=0, requested=0, extracted=0,
                failures=[*official_failures, {"error": "no_allowlisted_authoritative_url"}],
                message=(
                    "检索结果存在，但没有命中网页正文 HTTPS 来源白名单；"
                    "请只为可信机构或公司官网增加明确域名。"
                ),
            )
            return

        failures: List[Dict[str, str]] = list(official_failures)
        extracted = 0
        substantive_count = 0
        company_profile_count = 0
        short_content_count = 0
        for row in selected:
            requested_url = str(row.get("url") or "").strip()
            try:
                candidate_kind = str(row.get("kind") or "")
                parsed = (
                    artifacts.fetch_text(
                        requested_url,
                        allow_same_origin_module_fallback=True,
                    )
                    if candidate_kind == "web_company_official"
                    else artifacts.fetch_text(requested_url)
                )
                text_value = str(parsed.get("text") or "").strip()
                # For first-party company candidates, relevance must come from
                # the downloaded page itself. The locally generated candidate
                # title is not evidence that the remote body names the company.
                relevance_value = (
                    text_value
                    if parsed.get("extraction_method") == "same_origin_module_static_strings"
                    else
                    f"{parsed.get('title') or ''} {text_value}"
                    if candidate_kind in {"web_company_official", "web_company_exchange"}
                    else f"{row.get('title') or ''} {parsed.get('title') or ''} {text_value}"
                )
                haystack = self._normalize_web_text(relevance_value)
                if primary_needles and not any(needle in haystack for needle in primary_needles):
                    raise WebArticleArtifactError("网页正文未出现研究主体，已拒绝作为相关证据")
                extracted += 1
                final_url = str(parsed.get("final_url") or requested_url)
                host = str(urlparse(final_url).hostname or "").lower().rstrip(".")
                text_chars = int(parsed.get("text_chars") or len(text_value))
                extraction_method = str(parsed.get("extraction_method") or "lxml_readable_text")
                is_company_profile = (
                    candidate_kind == "web_company_official"
                    and (
                        extraction_method == "same_origin_module_static_strings"
                        or text_chars < _WEB_SUBSTANTIVE_MIN_CHARS
                    )
                )
                is_substantive = (
                    not is_company_profile
                    and text_chars >= _WEB_SUBSTANTIVE_MIN_CHARS
                )
                if is_company_profile:
                    content_role = "company_profile"
                    company_profile_count += 1
                elif is_substantive:
                    content_role = "substantive_article"
                    substantive_count += 1
                else:
                    content_role = "short_reference"
                    short_content_count += 1
                evidence_id = str(parsed.get("evidence_id") or "").strip()
                if not evidence_id:
                    document_hash = str(parsed.get("document_hash") or "")
                    evidence_id = f"web_fulltext:{sha256(f'{final_url}:{document_hash}'.encode('utf-8')).hexdigest()[:24]}"
                document = {
                    "evidence_id": evidence_id,
                    "title": parsed.get("title") or row.get("title"),
                    "date": row.get("date"),
                    "source": row.get("source"),
                    "requested_url": requested_url,
                    "url": final_url,
                    "host": host,
                    "authority_score": self._web_authority_score(host),
                    "relevance_score": self._web_result_rank(row, relevance_needles),
                    "cached": bool(parsed.get("cached")),
                    "document_bytes": int(parsed.get("document_bytes") or 0),
                    "document_hash": parsed.get("document_hash"),
                    "html_document_hash": parsed.get("html_document_hash"),
                    "text_chars": text_chars,
                    "text_hash": parsed.get("text_hash"),
                    "content_type": parsed.get("content_type"),
                    "text_status": "extracted",
                    "extraction_method": extraction_method,
                    "content_role": content_role,
                    "satisfies_substantive_fulltext": is_substantive,
                    "retrieved_at": parsed.get("fetched_at"),
                    "asset_url": parsed.get("asset_url"),
                    "asset_requested_url": parsed.get("asset_requested_url"),
                    "asset_document_hash": parsed.get("asset_document_hash"),
                    "asset_bytes": int(parsed.get("asset_bytes") or 0),
                    "asset_content_type": parsed.get("asset_content_type"),
                    "asset_cached": parsed.get("asset_cached"),
                    "module_documents": parsed.get("module_documents") or [],
                }
                result.setdefault("web_documents", []).append(document)
                result["evidence"].append({
                    "evidence_id": evidence_id,
                    "kind": "web_fulltext",
                    "source": f"{row.get('source') or host or '互联网来源'} · 网页正文",
                    "title": parsed.get("title") or row.get("title") or final_url,
                    "summary": text_value[:24_000],
                    "date": row.get("date") or "",
                    "url": final_url,
                    "requested_url": requested_url,
                    "symbol": row.get("symbol"),
                    "company": row.get("company") if research_type == "company" else None,
                    "evidence_level": "reported",
                    "original_available": True,
                    "importance": min(92, 74 + self._web_authority_score(host) // 8),
                    "document_text": text_value,
                    "document_text_status": "extracted",
                    "document_text_chars": document["text_chars"],
                    "document_text_hash": document["text_hash"],
                    "document_file_hash": document["document_hash"],
                    "html_document_hash": document["html_document_hash"],
                    "document_bytes": document["document_bytes"],
                    "document_cached": document["cached"],
                    "content_type": document["content_type"],
                    "extraction_method": document["extraction_method"],
                    "content_role": document["content_role"],
                    "satisfies_substantive_fulltext": document["satisfies_substantive_fulltext"],
                    "authority_host": host,
                    "authority_score": document["authority_score"],
                    "relevance_score": document["relevance_score"],
                    "retrieved_at": parsed.get("fetched_at"),
                    "asset_url": document["asset_url"],
                    "asset_document_hash": document["asset_document_hash"],
                    "asset_bytes": document["asset_bytes"],
                    "asset_content_type": document["asset_content_type"],
                    "module_documents": document["module_documents"],
                })
            except Exception as exc:  # noqa: BLE001 - one page must not abort the full research task.
                logger.info("Web fulltext unavailable for %s: %s", requested_url, type(exc).__name__)
                failures.append({
                    "url": requested_url,
                    "title": _text(row.get("title"), 160),
                    "error": f"{type(exc).__name__}: {_text(exc, 180)}",
                })

        # A thin company SPA bundle is useful company-profile context, but is
        # not a read article.  One readable article is still a narrow sample;
        # only multiple substantive bodies qualify as full coverage.
        status = "covered" if substantive_count >= 2 else "partial" if extracted else "failed"
        self._append_web_fulltext_status(
            result,
            status=status,
            matched=len(deduplicated),
            eligible=len(eligible_rows),
            requested=len(selected),
            extracted=extracted,
            failures=failures,
            substantive_count=substantive_count,
            company_profile_count=company_profile_count,
            short_content_count=short_content_count,
            message=(
                f"从 {len(deduplicated)} 条网页候选中选择 {len(selected)} 个安全允许页面，"
                f"固化 {extracted} 份可读内容，其中充分正文 {substantive_count} 份、"
                f"公司简介 {company_profile_count} 份、短参考 {short_content_count} 份；"
                "每份均保存原文哈希、正文哈希和证据 ID。"
            ),
        )

    @staticmethod
    def _append_web_fulltext_status(
        result: Dict[str, Any],
        *,
        status: str,
        matched: int,
        eligible: int,
        requested: int,
        extracted: int,
        failures: Sequence[Dict[str, str]],
        message: str,
        substantive_count: int = 0,
        company_profile_count: int = 0,
        short_content_count: int = 0,
    ) -> None:
        result["source_status"].append({
            "key": "web_fulltext",
            "name": "互联网权威网页正文",
            "status": status,
            "count": extracted,
            "matched": matched,
            "eligible": eligible,
            "requested": requested,
            "content_count": extracted,
            "substantive_content_count": int(substantive_count),
            "company_profile_count": int(company_profile_count),
            "short_content_count": int(short_content_count),
            "content_failures": len(failures),
            "failures": list(failures)[:10],
            "message": message,
        })

    @classmethod
    def _normalized_web_needles(cls, values: Sequence[Any]) -> List[str]:
        output: List[str] = []
        for value in values:
            normalized = cls._normalize_web_text(value)
            if len(normalized) >= 2 and normalized not in output:
                output.append(normalized)
        return output[:20]

    @staticmethod
    def _normalize_web_text(value: Any) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())

    @classmethod
    def _web_result_rank(cls, item: Dict[str, Any], needles: Sequence[str]) -> int:
        title = cls._normalize_web_text(item.get("title"))
        summary = cls._normalize_web_text(item.get("summary"))
        host = str(urlparse(str(item.get("url") or "")).hostname or "").lower().rstrip(".")
        score = cls._web_authority_score(host)
        if str(item.get("kind") or "") == "web_company_official":
            # The exact first-party homepage must not be displaced by several
            # discovery snippets when the body budget is intentionally small.
            score += 500
        for needle in needles:
            if needle in title:
                score += 24
            elif needle in summary:
                score += 8
        try:
            score += max(0, min(int(item.get("relevance_score") or 0), 100)) // 5
        except (TypeError, ValueError):
            pass
        if item.get("date"):
            score += 4
        return score

    @staticmethod
    def _web_authority_score(host: str) -> int:
        hostname = str(host or "").lower().rstrip(".")
        official = (
            "gov.cn", "csrc.gov.cn", "sse.com.cn", "szse.cn", "cninfo.com.cn",
            "pbc.gov.cn", "miit.gov.cn", "stats.gov.cn", "ndrc.gov.cn", "caict.ac.cn",
            "ieee.org", "itu.int", "iso.org", "nist.gov", "sec.gov", "ifrs.org",
            "worldbank.org", "oecd.org", "imf.org",
        )
        institutional = (
            "people.com.cn", "xinhuanet.com", "cs.com.cn", "cnstock.com", "stcn.com",
            "yicai.com", "cls.cn", "eastmoney.com", "cfainstitute.org",
        )
        if any(hostname == value or hostname.endswith(f".{value}") for value in official):
            return 60
        if any(hostname == value or hostname.endswith(f".{value}") for value in institutional):
            return 36
        return 18

    @staticmethod
    def _google_news_fallback(
        *,
        topic: str,
        symbol: str,
        terms: Sequence[str],
        research_type: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Fetch tightly matched public-news results when API search is absent.

        The fallback intentionally admits only rows that repeat the research
        subject in title/description.  This avoids the broad, unrelated recall
        that previously made keyless search look populated but unusable.
        """
        if limit <= 0 or not topic.strip():
            return []
        subject = topic.strip()
        queries = [f'"{subject}"']
        if research_type == "company":
            queries.append(f'"{subject}" (业绩 OR 订单 OR 公告 OR 研报)')
        else:
            queries.append(f'"{subject}" (产业 OR 技术 OR 趋势 OR 研究)')
        needles = {
            re.sub(r"\W+", "", value).casefold()
            for value in (subject, symbol, *terms)
            if len(re.sub(r"\W+", "", str(value))) >= 2
        }
        # The subject itself is mandatory; the wider term set only helps with
        # aliases and code/name variants.
        subject_key = re.sub(r"\W+", "", subject).casefold()
        output: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for query in queries:
            if len(output) >= limit:
                break
            url = (
                "https://news.google.com/rss/search?q="
                f"{quote_plus(query)}&hl=zh-CN&gl=CN&ceid=CN%3Azh-Hans"
            )
            try:
                response = requests.get(
                    url,
                    timeout=(3.05, 12),
                    headers={"User-Agent": "Mozilla/5.0 (compatible; LeziwuResearch/1.0)"},
                )
                response.raise_for_status()
                root = ElementTree.fromstring(response.content)
            except Exception as exc:  # noqa: BLE001 - fallback remains optional.
                logger.info("Google News RSS fallback unavailable: %s", type(exc).__name__)
                continue
            for item in root.findall("./channel/item"):
                title = _text(item.findtext("title"), 500)
                description = unescape(str(item.findtext("description") or ""))
                description = _text(re.sub(r"<[^>]+>", " ", description), 1400)
                haystack = re.sub(r"\W+", "", f"{title} {description}").casefold()
                if subject_key and subject_key not in haystack:
                    continue
                if needles and not any(needle in haystack for needle in needles):
                    continue
                link = _text(item.findtext("link"), 2000)
                stable_key = link or title
                if not stable_key or stable_key in seen:
                    continue
                seen.add(stable_key)
                source_node = item.find("source")
                source_name = _text(source_node.text if source_node is not None else "Google News", 120)
                raw_date = _text(item.findtext("pubDate"), 120)
                published = raw_date
                try:
                    published = parsedate_to_datetime(raw_date).date().isoformat()
                except (TypeError, ValueError, OverflowError):
                    pass
                stable = sha256(stable_key.encode("utf-8")).hexdigest()[:20]
                output.append({
                    "evidence_id": f"web:rss:{stable}",
                    "kind": "web_news",
                    "source": source_name or "Google News",
                    "title": title,
                    "summary": description or title,
                    "date": published,
                    "url": link,
                    "symbol": symbol or None,
                    "company": subject if research_type == "company" else None,
                    "evidence_level": "reported",
                    "original_available": bool(link),
                })
                if len(output) >= limit:
                    break
        return output

    @staticmethod
    def _deduplicate_evidence(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_key: Dict[str, Dict[str, Any]] = {}
        for item in items:
            key = str(item.get("url") or item.get("evidence_id") or "").strip()
            if not key:
                continue
            existing = by_key.get(key)
            if existing is None or len(str(item.get("summary") or "")) > len(str(existing.get("summary") or "")):
                by_key[key] = item
        return list(by_key.values())
