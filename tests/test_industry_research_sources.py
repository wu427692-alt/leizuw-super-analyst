from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.broker_report_artifact_service import (
    BrokerReportArtifactError,
    BrokerReportArtifactService,
)
from src.services.announcement_artifact_service import _PdfExtractionFailure
from src.services.cninfo_announcement_service import ANNOUNCEMENT_CATEGORIES
from src.services.industry_research_sources import IndustryResearchSourceCollector
from src.storage import (
    DatabaseManager,
    EssayAnalysisRecord,
    ResearchNote,
    ResearchReportRecord,
    utc_naive_now,
)


class _FakeTushare:
    available = True

    def __init__(self):
        self.calls = []

    def query(self, api_name, *, params=None, fields=None):
        self.calls.append((api_name, params or {}, fields))
        rows = {
            "stock_basic": [{
                "ts_code": "300308.SZ", "symbol": "300308", "name": "测试股份",
                "industry": "通信设备", "market": "创业板", "list_date": "20150910",
            }],
            "stock_company": [{
                "com_name": "测试股份有限公司", "industry": "通信设备", "main_business": "光模块研发与销售",
                "employees": 1200, "province": "广东", "website": "https://example.com",
            }],
            "fina_indicator": [{
                "end_date": "20260630", "ann_date": "20260820", "roe": 8.5,
                "grossprofit_margin": 31.2, "or_yoy": 44.0, "netprofit_yoy": 51.0,
                "q_sales_yoy": 45.0, "q_netprofit_yoy": 52.0,
            }],
            "income": [{"end_date": "20260630", "revenue": 500.0, "n_income_attr_p": 80.0}],
            "balancesheet": [{"end_date": "20260630", "total_assets": 1000.0, "total_liab": 380.0}],
            "cashflow": [{"end_date": "20260630", "n_cashflow_act": 70.0}],
            "forecast": [{"end_date": "20261231", "type": "预增", "p_change_min": 30, "p_change_max": 50}],
            "report_rc": [{
                "report_date": "20260825", "quarter": "2026Q3", "rating": "增持",
                "org_name": "测试证券", "author": "研究员甲", "np": 80, "eps": 2.1,
                "pe": 25, "min_price": 100, "max_price": 120,
            }],
            "daily_basic": [{
                "trade_date": "20260828", "close": 110, "turnover_rate": 3.2,
                "pe_ttm": 28.1, "pb": 6.2, "total_mv": 1200000, "circ_mv": 900000,
            }],
            "cyq_perf": [{"trade_date": "20260828", "weight_avg": 104.5, "winner_rate": 72.4}],
            "stk_factor": [{"trade_date": "20260828", "rsi_6": 63.2}],
            "stk_managers": [{"ann_date": "20260801", "name": "高管甲", "title": "董事长"}],
            "stk_holdernumber": [{"ann_date": "20260810", "end_date": "20260630", "holder_num": 42000}],
            "pledge_stat": [{"end_date": "20260630", "pledge_count": 2, "pledge_ratio": 1.2}],
            "share_float": [{"ann_date": "20260701", "float_date": "20260901", "float_share": 1000}],
            "dividend": [{"ann_date": "20260420", "end_date": "20251231", "cash_div_tax": 0.2}],
            "express": [{"ann_date": "20260715", "end_date": "20260630", "revenue": 500, "n_income": 80}],
            "stk_surv": [{"surv_date": "20260822", "fund_visitors": "测试基金", "rece_mode": "电话会议"}],
            "margin_detail": [{"trade_date": "20260828", "rzye": 1000, "rqye": 10}],
            "hk_hold": [{"trade_date": "20260828", "vol": 200, "ratio": 1.5}],
            "top_list": [{"trade_date": "20260828", "reason": "日涨幅偏离", "net_amount": 100}],
        }.get(api_name, [])
        return {"rows": rows}


class _FakeMarket:
    def get_series(self, *_args, **_kwargs):
        return {
            "source": "test.market",
            "data": [
                {"date": "2026-08-20", "close": 100.0, "volume": 10.0},
                {"date": "2026-08-21", "close": 110.0, "volume": 12.0},
            ],
        }


class _FakePeerTushare:
    available = True

    def __init__(self, *, fail_symbol=None):
        self.calls = []
        self.fail_symbol = fail_symbol

    def query(self, api_name, *, params=None, fields=None):
        params = params or {}
        symbol = params.get("ts_code")
        self.calls.append((api_name, symbol, fields))
        if symbol == self.fail_symbol:
            raise RuntimeError("isolated peer failure")
        suffix = int(str(symbol or "000000")[:6]) % 100
        rows = {
            "stock_basic": [{
                "ts_code": symbol, "symbol": str(symbol or "")[:6], "name": f"同业{suffix}",
                "industry": "通信设备", "market": "主板", "list_date": "20100101",
            }],
            "fina_indicator": [{
                "ts_code": symbol, "end_date": "20260630", "ann_date": "20260820",
                "roe": 8 + suffix / 10, "grossprofit_margin": 25 + suffix / 10,
                "or_yoy": 9 + suffix, "netprofit_yoy": 11 + suffix,
                "q_sales_yoy": 10 + suffix, "q_netprofit_yoy": 12 + suffix,
            }],
            "income": [{
                "ts_code": symbol, "end_date": "20260630", "ann_date": "20260820",
                "revenue": 1_000_000 + suffix * 10_000, "n_income_attr_p": 100_000 + suffix * 1_000,
            }],
            "cashflow": [{
                "ts_code": symbol, "end_date": "20260630", "ann_date": "20260820",
                "n_cashflow_act": 90_000 + suffix * 1_000,
            }],
        }.get(api_name, [])
        return {"rows": rows}


class _MixedPeriodPeerTushare(_FakePeerTushare):
    def query(self, api_name, *, params=None, fields=None):
        response = super().query(api_name, params=params, fields=fields)
        symbol = str((params or {}).get("ts_code") or "")
        if api_name != "stock_basic" and symbol in {"600003.SH", "600004.SH"}:
            for row in response["rows"]:
                row["end_date"] = "20260331"
        return response


class _IndicatorOnlyPeerTushare(_FakePeerTushare):
    def query(self, api_name, *, params=None, fields=None):
        response = super().query(api_name, params=params, fields=fields)
        if api_name in {"income", "cashflow"}:
            response["rows"] = []
        return response


class _FakeCninfo:
    def __init__(self):
        self.calls = []

    def fetch(self, **kwargs):
        self.calls.append(kwargs)
        return [{
            "announcement_id": "A1", "title": "2026年半年度报告", "announcement_at": "2026-08-20",
            "pdf_url": "https://example.com/half-year.pdf", "category_names": ["半年度报告"],
        }]


class _FakeAnnouncementArtifacts:
    def __init__(self):
        self.events = []

    def download_pdf(self, event):
        self.events.append(event)
        return Path("/tmp/fake-cninfo.pdf"), True

    def fetch_text(self, event):
        self.events.append(event)
        text = (
            "管理层讨论与分析 公司报告期内光模块业务订单增加，研发投入继续增长。"
            "核心竞争力分析 公司拥有高速光通信器件研发平台。"
            "可能面对的风险 需求波动和客户集中度仍需持续核验。"
        )
        return {
            "text": text,
            "text_hash": "filing-text-hash",
            "text_chars": len(text),
            "document_hash": "filing-document-hash",
            "document_bytes": 4096,
            "page_count": 88,
            "pages_extracted": 88,
            "extraction_complete": True,
            "extraction_status": "complete",
            "cached": True,
            "pdf_cached": True,
            "extraction_method": "pdfium_text",
            "extraction_engine_version": "pypdfium2=test;pdfium=test",
            "extraction_duration_ms": 321,
            "fallback_reason": "",
        }


class _UnavailableSearch:
    is_available = False


class _TimedOutAnnouncementArtifacts(_FakeAnnouncementArtifacts):
    def fetch_text(self, event):
        self.events.append(event)
        raise _PdfExtractionFailure("timeout", "simulated bounded timeout")


@pytest.fixture
def essay_db(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(f"sqlite:///{tmp_path / 'industry-research.db'}")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_company_source_collector_builds_financial_market_and_cninfo_evidence(essay_db):
    tushare = _FakeTushare()
    cninfo = _FakeCninfo()
    announcement_artifacts = _FakeAnnouncementArtifacts()
    collector = IndustryResearchSourceCollector(
        tushare=tushare,
        market=_FakeMarket(),
        cninfo=cninfo,
        announcement_artifacts=announcement_artifacts,
        db=essay_db,
    )
    progress_messages = []
    with (
        patch("src.services.industry_research_sources.get_search_service", return_value=_UnavailableSearch()),
        patch.object(collector, "_google_news_fallback", return_value=[]),
    ):
        result = collector.collect(
            topic="300308.SZ", research_type="company", terms=["中际旭创", "300308.SZ"], lookback_days=730,
            progress=progress_messages.append,
        )

    ids = {item["evidence_id"] for item in result["evidence"]}
    assert result["subject"]["symbol"] == "300308.SZ"
    assert result["subject"]["main_business"] == "光模块研发与销售"
    assert "company:300308.SZ" in ids
    assert "financial:300308.SZ:20260630" in ids
    assert "valuation:300308.SZ:20260828" in ids
    assert "market:300308.SZ:daily" in ids
    assert "filing:A1" in ids
    assert result["financial_series"][0]["net_profit"] == 80.0
    assert result["market_series"][-1]["close"] == 110.0
    assert result["valuation_series"][0]["pe_ttm"] == 28.1
    valuation_evidence = next(item for item in result["evidence"] if item["evidence_id"] == "valuation:300308.SZ:20260828")
    assert valuation_evidence["kind"] == "valuation_fact"
    assert valuation_evidence["metric_units"]["total_market_value"] == "万元"
    assert "筹码加权成本 104.5 元" in valuation_evidence["summary"]
    assert {name for name, _params, _fields in tushare.calls} >= {
        "daily_basic", "stk_managers", "stk_holdernumber", "pledge_stat",
        "share_float", "dividend", "express", "stk_surv", "margin_detail", "hk_hold", "top_list",
    }
    filing = next(item for item in result["evidence"] if item["evidence_id"] == "filing:A1")
    assert filing["document_text_status"] == "extracted"
    assert "光模块业务订单增加" in filing["document_text"]
    assert filing["document_text_hash"] == "filing-text-hash"
    assert filing["document_file_hash"] == "filing-document-hash"
    assert filing["document_cached"] is True
    assert filing["document_pages"] == 88
    assert filing["document_pages_extracted"] == 88
    assert filing["document_extraction_complete"] is True
    assert filing["document_extraction_status"] == "complete"
    assert filing["extraction_method"] == "pdfium_text"
    assert filing["extraction_engine_version"] == "pypdfium2=test;pdfium=test"
    assert filing["extraction_duration_ms"] == 321
    assert result["filing_documents"][0]["text_cached"] is True
    assert any("定期报告正文 1/1" in message for message in progress_messages)
    assert any("复用正文缓存" in message for message in progress_messages)
    assert next(item for item in result["source_status"] if item["key"] == "cninfo_reports")["count"] == 1
    assert next(item for item in result["source_status"] if item["key"] == "cninfo_report_text")["status"] == "covered"
    assert next(item for item in result["source_status"] if item["key"] == "research_report_fulltext")["status"] == "missing"
    assert all(len(call["categories"]) == 1 for call in cninfo.calls)
    queried_categories = {call["categories"][0] for call in cninfo.calls}
    assert queried_categories == set(ANNOUNCEMENT_CATEGORIES)


def test_company_source_collector_queries_cninfo_categories_individually_to_keep_quarterly_reports(essay_db):
    class _UnionLosingCninfo:
        def __init__(self):
            self.calls = []

        def fetch(self, **kwargs):
            self.calls.append(kwargs)
            categories = list(kwargs.get("categories") or [])
            if len(categories) != 1:
                return [{
                    "announcement_id": "A1",
                    "title": "华懋科技2025年年度报告",
                    "announcement_at": "2026-04-22",
                    "pdf_url": "https://example.com/annual.pdf",
                    "category_codes": categories,
                }]
            rows = {
                "category_ndbg_szsh": [{
                    "announcement_id": "A1", "title": "华懋科技2025年年度报告",
                    "announcement_at": "2026-04-22", "pdf_url": "https://example.com/annual.pdf",
                    "category_codes": categories, "category_names": ["年报"],
                }],
                "category_bndbg_szsh": [{
                    "announcement_id": "H1", "title": "华懋科技2026年半年度报告",
                    "announcement_at": "2026-08-25", "pdf_url": "https://example.com/interim.pdf",
                    "category_codes": categories, "category_names": ["半年报"],
                }],
                "category_yjdbg_szsh": [{
                    "announcement_id": "Q1", "title": "华懋科技2026年第一季度报告",
                    "announcement_at": "2026-04-28", "pdf_url": "https://example.com/q1.pdf",
                    "category_codes": categories, "category_names": ["一季报"],
                }],
            }.get(categories[0], [])
            return rows

    cninfo = _UnionLosingCninfo()
    collector = IndustryResearchSourceCollector(
        tushare=_FakeTushare(),
        market=_FakeMarket(),
        cninfo=cninfo,
        announcement_artifacts=_FakeAnnouncementArtifacts(),
        db=essay_db,
    )
    with (
        patch("src.services.industry_research_sources.get_search_service", return_value=_UnavailableSearch()),
        patch.object(collector, "_google_news_fallback", return_value=[]),
    ):
        result = collector.collect(
            topic="603306.SH",
            research_type="company",
            terms=["华懋科技", "603306.SH"],
            lookback_days=730,
        )

    filings = {item["filing_type"]: item for item in result["filing_documents"]}
    assert {"annual", "interim", "q1"} <= set(filings)
    assert filings["annual"]["report_period"] == "2025-12-31"
    assert filings["interim"]["report_period"] == "2026-06-30"
    assert filings["q1"]["report_period"] == "2026-03-31"
    assert all(len(call["categories"]) == 1 for call in cninfo.calls)
    status = next(item for item in result["source_status"] if item["key"] == "cninfo_reports")
    assert status["category_requested"] == len(ANNOUNCEMENT_CATEGORIES)
    assert status["category_succeeded"] == len(ANNOUNCEMENT_CATEGORIES)
    assert status["category_failures"] == {}


def test_cninfo_partial_category_failure_never_claims_complete_filing_coverage(essay_db):
    class _QuarterFailureCninfo:
        def fetch(self, **kwargs):
            category = list(kwargs.get("categories") or [""])[0]
            if category == "category_yjdbg_szsh":
                raise RuntimeError("simulated q1 upstream failure")
            if category == "category_ndbg_szsh":
                return [{
                    "announcement_id": "A1", "title": "华懋科技2025年年度报告",
                    "announcement_at": "2026-04-22", "pdf_url": "https://example.com/annual.pdf",
                    "category_codes": [category], "category_names": ["年报"],
                }]
            return []

    result = {
        "subject": {"name": "华懋科技"},
        "evidence": [],
        "filing_documents": [],
        "source_status": [],
    }
    collector = IndustryResearchSourceCollector(
        tushare=_FakeTushare(),
        market=_FakeMarket(),
        cninfo=_QuarterFailureCninfo(),
        announcement_artifacts=_FakeAnnouncementArtifacts(),
        db=essay_db,
    )

    collector._collect_cninfo(result, "603306.SH", 730)

    metadata_status = next(item for item in result["source_status"] if item["key"] == "cninfo_reports")
    filing_status = next(item for item in result["source_status"] if item["key"] == "cninfo_report_text")
    assert metadata_status["status"] == "partial"
    assert metadata_status["category_states"]["category_yjdbg_szsh"]["status"] == "RuntimeError"
    assert filing_status["status"] == "partial"
    assert filing_status["periodic_category_incomplete"] == {
        "category_yjdbg_szsh": "RuntimeError",
    }


def test_cninfo_only_empty_category_success_with_other_failures_is_failed_not_missing(essay_db):
    class _MostlyFailedCninfo:
        def fetch(self, **kwargs):
            category = list(kwargs.get("categories") or [""])[0]
            if category == "category_ndbg_szsh":
                return []
            raise RuntimeError("simulated category outage")

    result = {
        "subject": {"name": "华懋科技"},
        "evidence": [],
        "filing_documents": [],
        "source_status": [],
    }
    collector = IndustryResearchSourceCollector(
        tushare=_FakeTushare(),
        market=_FakeMarket(),
        cninfo=_MostlyFailedCninfo(),
        announcement_artifacts=_FakeAnnouncementArtifacts(),
        db=essay_db,
    )

    collector._collect_cninfo(result, "603306.SH", 730)

    metadata_status = next(item for item in result["source_status"] if item["key"] == "cninfo_reports")
    filing_status = next(item for item in result["source_status"] if item["key"] == "cninfo_report_text")
    assert metadata_status["status"] == "failed"
    assert metadata_status["count"] == 0
    assert metadata_status["category_succeeded"] == 1
    assert filing_status["status"] == "partial"


def test_cninfo_stops_remaining_categories_after_shared_request_budget_is_exhausted(
    essay_db,
    monkeypatch,
):
    class _BudgetExhaustingCninfo:
        def __init__(self):
            self.calls = []

        def fetch(self, **kwargs):
            self.calls.append(kwargs)
            budget = kwargs["request_budget"]
            consumed = int(budget["remaining"])
            budget["remaining"] = 0
            budget["used"] = int(budget.get("used") or 0) + consumed
            return []

    monkeypatch.setenv("INDUSTRY_RESEARCH_CNINFO_REQUEST_LIMIT", "30")
    monkeypatch.setenv("INDUSTRY_RESEARCH_CNINFO_DEADLINE_SECONDS", "180")
    monkeypatch.setenv("INDUSTRY_RESEARCH_CNINFO_MAX_PAGES_PER_CATEGORY", "10")
    cninfo = _BudgetExhaustingCninfo()
    result = {
        "subject": {"name": "华懋科技"},
        "evidence": [],
        "filing_documents": [],
        "source_status": [],
    }
    collector = IndustryResearchSourceCollector(
        tushare=_FakeTushare(),
        market=_FakeMarket(),
        cninfo=cninfo,
        announcement_artifacts=_FakeAnnouncementArtifacts(),
        db=essay_db,
    )

    collector._collect_cninfo(result, "603306.SH", 730)

    metadata_status = next(item for item in result["source_status"] if item["key"] == "cninfo_reports")
    filing_status = next(item for item in result["source_status"] if item["key"] == "cninfo_report_text")
    assert [call["categories"] for call in cninfo.calls] == [["category_ndbg_szsh"]]
    assert metadata_status["category_succeeded"] == 1
    assert metadata_status["request_budget"] == {
        "limit": 30,
        "used": 30,
        "deadline_seconds": 180.0,
        "max_pages_per_category": 10,
    }
    assert metadata_status["category_states"]["category_ndbg_szsh"]["status"] == "empty"
    deferred = set(ANNOUNCEMENT_CATEGORIES) - {"category_ndbg_szsh"}
    assert set(metadata_status["category_failures"]) == deferred
    assert all(
        metadata_status["category_states"][code]["status"] == "budget_exhausted"
        for code in deferred
    )
    assert filing_status["status"] == "partial"
    assert filing_status["periodic_category_incomplete"] == {
        "category_bndbg_szsh": "budget_exhausted",
        "category_yjdbg_szsh": "budget_exhausted",
        "category_sjdbg_szsh": "budget_exhausted",
    }


def test_cninfo_pagination_truncation_propagates_to_final_collector_status(essay_db):
    class _TruncatedCninfo:
        def fetch(self, **kwargs):
            category = list(kwargs.get("categories") or [""])[0]
            if category != "category_ndbg_szsh":
                return []
            kwargs["diagnostics"].update({
                "truncated": True,
                "pages_fetched": 2,
                "total_pages": 5,
            })
            return [{
                "announcement_id": "A1",
                "title": "华懋科技2025年年度报告",
                "announcement_at": "2026-04-22",
                "pdf_url": "https://example.com/annual.pdf",
                "category_codes": [category],
                "category_names": ["年报"],
            }]

    result = {
        "subject": {"name": "华懋科技"},
        "evidence": [],
        "filing_documents": [],
        "source_status": [],
    }
    collector = IndustryResearchSourceCollector(
        tushare=_FakeTushare(),
        market=_FakeMarket(),
        cninfo=_TruncatedCninfo(),
        announcement_artifacts=_FakeAnnouncementArtifacts(),
        db=essay_db,
    )

    collector._collect_cninfo(result, "603306.SH", 730)

    metadata_status = next(item for item in result["source_status"] if item["key"] == "cninfo_reports")
    filing_status = next(item for item in result["source_status"] if item["key"] == "cninfo_report_text")
    expected_truncation = {"pages_fetched": 2, "total_pages": 5}
    assert metadata_status["status"] == "partial"
    assert metadata_status["category_truncated"] == {
        "category_ndbg_szsh": expected_truncation,
    }
    assert metadata_status["category_states"]["category_ndbg_szsh"] == {
        "status": "truncated",
        "count": 1,
        **expected_truncation,
    }
    assert filing_status["status"] == "partial"
    assert filing_status["periodic_category_status"]["category_ndbg_szsh"] == {
        "status": "truncated",
        "count": 1,
        **expected_truncation,
    }
    assert filing_status["periodic_category_incomplete"] == {
        "category_ndbg_szsh": "truncated",
    }


def test_company_source_collector_records_filing_timeout_without_claiming_coverage(essay_db):
    collector = IndustryResearchSourceCollector(
        tushare=_FakeTushare(),
        market=_FakeMarket(),
        cninfo=_FakeCninfo(),
        announcement_artifacts=_TimedOutAnnouncementArtifacts(),
        db=essay_db,
    )
    progress_messages = []
    with (
        patch("src.services.industry_research_sources.get_search_service", return_value=_UnavailableSearch()),
        patch.object(collector, "_google_news_fallback", return_value=[]),
    ):
        result = collector.collect(
            topic="300308.SZ",
            research_type="company",
            terms=["中际旭创", "300308.SZ"],
            lookback_days=730,
            progress=progress_messages.append,
        )

    filing_status = next(item for item in result["source_status"] if item["key"] == "cninfo_report_text")
    assert filing_status["status"] == "partial"
    assert filing_status["count"] == 0
    assert filing_status["requested"] == 1
    assert filing_status["failure_reasons"] == {"timeout": 1}
    assert not result["filing_documents"]
    assert not any(item["kind"] == "filing_text" for item in result["evidence"])
    assert any("读取失败（timeout）" in message for message in progress_messages)


def test_periodic_report_selection_excludes_ordinary_announcements_and_labels_periods(monkeypatch):
    monkeypatch.setenv("INDUSTRY_RESEARCH_FILING_MAX_DOCS", "3")
    rows = [
        {
            "announcement_id": "N1", "title": "关于召开股东大会的通知",
            "announcement_at": "2026-08-30", "pdf_url": "https://example.com/notice.pdf",
            # Some upstream rows echo every queried category code.  That is not
            # reliable evidence that this particular row is a periodic report.
            "category_codes": [
                "category_ndbg_szsh", "category_bndbg_szsh",
                "category_yjdbg_szsh", "category_sjdbg_szsh",
            ],
        },
        {
            "announcement_id": "H1", "title": "华懋科技2026年半年度报告",
            "announcement_at": "2026-08-28", "pdf_url": "https://example.com/half.pdf",
        },
        {
            "announcement_id": "Q1", "title": "华懋科技2026年第一季度报告",
            "announcement_at": "2026-04-29", "pdf_url": "https://example.com/q1.pdf",
        },
        {
            "announcement_id": "A1", "title": "华懋科技2025年年度报告",
            "announcement_at": "2026-04-15", "pdf_url": "https://example.com/annual.pdf",
        },
    ]

    selected = IndustryResearchSourceCollector._select_periodic_reports(rows)

    assert {item["announcement_id"] for item in selected} == {"A1", "H1", "Q1"}
    by_id = {item["announcement_id"]: item for item in selected}
    assert by_id["A1"]["filing_type"] == "annual"
    assert by_id["A1"]["report_period"] == "2025-12-31"
    assert by_id["H1"]["filing_type"] == "interim"
    assert by_id["H1"]["report_period"] == "2026-06-30"
    assert by_id["Q1"]["filing_type"] == "q1"
    assert by_id["Q1"]["report_period"] == "2026-03-31"
    assert all(item["classification_basis"] == "title" for item in selected)


def test_material_transaction_selection_prefers_latest_primary_report(monkeypatch):
    monkeypatch.setenv("INDUSTRY_RESEARCH_MATERIAL_FILING_MAX_DOCS", "1")
    rows = [
        {
            "announcement_id": "SUMMARY", "title": "重大资产重组报告书（草案）摘要",
            "announcement_at": "2026-08-30", "pdf_url": "https://example.com/summary.pdf",
        },
        {
            "announcement_id": "ADVISER", "title": "独立财务顾问关于重大资产重组的核查意见",
            "announcement_at": "2026-08-30", "pdf_url": "https://example.com/adviser.pdf",
        },
        {
            "announcement_id": "LATEST",
            "title": "华懋科技发行股份及支付现金购买资产并募集配套资金报告书（草案）（四次修订稿）",
            "announcement_at": "2026-08-28", "pdf_url": "https://example.com/latest.pdf",
        },
        {
            "announcement_id": "OLDER",
            "title": "华懋科技发行股份及支付现金购买资产并募集配套资金报告书（草案）",
            "announcement_at": "2026-06-26", "pdf_url": "https://example.com/older.pdf",
        },
    ]

    selected = IndustryResearchSourceCollector._select_material_transaction_reports(rows)

    assert [item["announcement_id"] for item in selected] == ["LATEST"]
    assert selected[0]["filing_type"] == "material_transaction"
    assert selected[0]["classification_basis"] == "material_transaction_title"


def test_material_transaction_selection_excludes_disclosure_risk_notice(monkeypatch):
    monkeypatch.setenv("INDUSTRY_RESEARCH_MATERIAL_FILING_MAX_DOCS", "1")
    rows = [
        {
            "announcement_id": "1224692841",
            "title": "关于披露重组报告书暨一般风险提示公告",
            "announcement_at": "2026-08-30",
            "pdf_url": "https://example.com/two-page-notice.pdf",
        },
        {
            "announcement_id": "1224692878",
            "title": "发行股份及支付现金购买资产并募集配套资金暨关联交易报告书（草案）",
            "announcement_at": "2026-08-30",
            "pdf_url": "https://example.com/full-report.pdf",
        },
        {
            "announcement_id": "LEGAL",
            "title": "关于本次发行股份购买资产暨关联交易的法律意见书",
            "announcement_at": "2026-08-30",
            "pdf_url": "https://example.com/legal.pdf",
        },
    ]

    selected = IndustryResearchSourceCollector._select_material_transaction_reports(rows)

    assert [item["announcement_id"] for item in selected] == ["1224692878"]


def test_filing_excerpts_include_transaction_financial_and_commitment_sections():
    text = (
        "前言" * 200
        + "交易标的财务状况 富创优越最近两年一期主要财务数据。" + "财务数据" * 500
        + "业绩承诺及补偿安排 承诺期与承诺净利润口径如下。" + "承诺条款" * 500
    )

    excerpts = IndustryResearchSourceCollector._filing_excerpts(text)

    assert "【交易标的财务状况】" in excerpts
    assert "【业绩承诺及补偿安排】" in excerpts


def test_filing_excerpts_skip_table_of_contents_heading_for_real_body():
    text = (
        "目录 管理层讨论与分析........................9 "
        + "目录项" * 900
        + "管理层讨论与分析 本期营业收入与现金流的真实经营分析。"
        + "经营正文" * 600
    )

    excerpts = IndustryResearchSourceCollector._filing_excerpts(text)

    assert "本期营业收入与现金流的真实经营分析" in excerpts
    first_section = excerpts.split("【管理层讨论与分析】", 1)[1][:300]
    assert "........................9" not in first_section


def test_periodic_report_classification_can_use_unique_category_and_discloses_date_inference():
    classification = IndustryResearchSourceCollector._classify_periodic_report({
        "title": "华懋科技定期报告全文（修订版）",
        "announcement_at": "2026-04-20",
        "category_codes": ["category_ndbg_szsh"],
    })

    assert classification == {
        "filing_type": "annual",
        "filing_type_label": "年度报告",
        "report_year": 2025,
        "report_period": "2025-12-31",
        "classification_basis": "category_code",
        "report_year_basis": "announcement_date_previous_year",
    }


def test_periodic_report_classification_rejects_governance_title_that_only_mentions_annual_report():
    row = {
        "announcement_id": "POLICY1",
        "title": "华懋科技年报信息披露重大差错责任追究制度（2026年8月修订）",
        "announcement_at": "2026-08-28",
        "pdf_url": "https://example.com/annual-report-policy.pdf",
        "category_codes": ["category_ndbg_szsh"],
        "category_names": ["年报"],
    }

    assert IndustryResearchSourceCollector._classify_periodic_report(row) == {}
    assert IndustryResearchSourceCollector._select_periodic_reports([row]) == []


@pytest.mark.parametrize("title", [
    "华懋科技2025年ESG年度报告",
    "华懋科技2025年社会责任年度报告",
    "华懋科技2025年公司债券年度报告",
    "关于审议华懋科技2025年年度报告",
    "华懋科技定期报告",
])
def test_periodic_report_classification_rejects_non_financial_annual_report_contexts(title):
    assert IndustryResearchSourceCollector._classify_periodic_report({
        "title": title,
        "announcement_at": "2026-04-20",
        "category_codes": ["category_ndbg_szsh"],
        "category_names": ["年报"],
    }) == {}


def test_generic_periodic_report_fallback_rejects_conflicting_categories():
    assert IndustryResearchSourceCollector._classify_periodic_report({
        "title": "华懋科技定期报告全文（修订版）",
        "announcement_at": "2026-04-20",
        "category_codes": ["category_ndbg_szsh"],
        "category_names": ["年报", "半年报"],
    }) == {}


@pytest.mark.parametrize(("title", "filing_type", "report_period"), [
    ("华懋科技2025年年度报告全文（修订版）", "annual", "2025-12-31"),
    ("华懋科技2026年半年度报告", "interim", "2026-06-30"),
    ("华懋科技2026年第一季度报告", "q1", "2026-03-31"),
    ("华懋科技2025年第三季度报告（更正版）", "q3", "2025-09-30"),
])
def test_periodic_report_classification_accepts_explicit_financial_report_titles(
    title, filing_type, report_period,
):
    classification = IndustryResearchSourceCollector._classify_periodic_report({
        "title": title,
        "announcement_at": "2026-08-28",
    })

    assert classification["filing_type"] == filing_type
    assert classification["report_period"] == report_period
    assert classification["classification_basis"] == "title"


def test_industry_peer_collector_uses_only_top_five_and_never_ai_numbers(essay_db, monkeypatch):
    fake = _FakePeerTushare()
    collector = IndustryResearchSourceCollector(tushare=fake, market=_FakeMarket(), db=essay_db)
    collector._industry_peer_cache.clear()
    monkeypatch.setenv("INDUSTRY_RESEARCH_PEER_MAX_COMPANIES", "5")
    monkeypatch.setenv("INDUSTRY_RESEARCH_PEER_PERIODS", "4")
    monkeypatch.setenv("INDUSTRY_RESEARCH_PEER_API_CALL_LIMIT", "20")
    concept_context = {"constituents": [
        {
            "ts_code": f"60000{index}.SH", "name": f"候选{index}",
            "weight_score": 100 - index, "source_count": 3 if index < 4 else 1,
            "confidence": "high", "reasons": ["多源成分共识"],
        }
        for index in range(6)
    ]}

    result = collector.collect_industry_peers(concept_context=concept_context, lookback_days=730)
    matrix = result["industry_peer_matrix"]

    assert matrix["status"] == "covered"
    assert matrix["company_count"] == 5
    assert matrix["comparable_company_count"] == 5
    assert matrix["common_period"] == "20260630"
    assert matrix["api_calls"] == 20
    assert {item["symbol"] for item in matrix["companies"]} == {
        "600000.SH", "600001.SH", "600002.SH", "600003.SH", "600004.SH",
    }
    assert all(item["kind"] == "industry_peer_fact" for item in result["evidence"])
    assert all("关键数值未由 AI 推算" in item["summary"] for item in result["evidence"])
    assert all(call[2] for call in fake.calls)


def test_industry_peer_collector_isolates_one_company_failure(essay_db, monkeypatch):
    fake = _FakePeerTushare(fail_symbol="600003.SH")
    collector = IndustryResearchSourceCollector(tushare=fake, market=_FakeMarket(), db=essay_db)
    collector._industry_peer_cache.clear()
    monkeypatch.setenv("INDUSTRY_RESEARCH_PEER_MAX_COMPANIES", "4")
    concept_context = {"constituents": [
        {"ts_code": f"60000{index}.SH", "name": f"候选{index}", "weight_score": 90 - index, "source_count": 2}
        for index in range(4)
    ]}

    result = collector.collect_industry_peers(concept_context=concept_context, lookback_days=730)
    status = result["source_status"][0]

    assert result["industry_peer_matrix"]["company_count"] == 3
    assert result["industry_peer_matrix"]["comparable_company_count"] == 3
    assert status["status"] == "partial"
    assert status["count"] == 3
    assert any(item["symbol"] == "600003.SH" for item in status["failures"])


def test_industry_peer_collector_uses_latest_period_shared_by_at_least_three(essay_db, monkeypatch):
    fake = _MixedPeriodPeerTushare()
    collector = IndustryResearchSourceCollector(tushare=fake, market=_FakeMarket(), db=essay_db)
    collector._industry_peer_cache.clear()
    monkeypatch.setenv("INDUSTRY_RESEARCH_PEER_MAX_COMPANIES", "5")
    concept_context = {"constituents": [
        {"ts_code": f"60000{index}.SH", "name": f"候选{index}", "weight_score": 90 - index, "source_count": 2}
        for index in range(5)
    ]}

    result = collector.collect_industry_peers(concept_context=concept_context, lookback_days=730)
    matrix = result["industry_peer_matrix"]

    assert matrix["status"] == "partial"
    assert matrix["company_count"] == 5
    assert matrix["common_period"] == "20260630"
    assert matrix["comparable_company_count"] == 3
    assert sum(bool(item["common_period_fact"]) for item in matrix["companies"]) == 3


def test_industry_peer_quality_gate_requires_core_income_and_profit(essay_db, monkeypatch):
    fake = _IndicatorOnlyPeerTushare()
    collector = IndustryResearchSourceCollector(tushare=fake, market=_FakeMarket(), db=essay_db)
    collector._industry_peer_cache.clear()
    monkeypatch.setenv("INDUSTRY_RESEARCH_PEER_MAX_COMPANIES", "3")
    concept_context = {"constituents": [
        {"ts_code": f"60000{index}.SH", "name": f"候选{index}", "weight_score": 90 - index, "source_count": 2}
        for index in range(3)
    ]}

    result = collector.collect_industry_peers(concept_context=concept_context, lookback_days=730)
    matrix = result["industry_peer_matrix"]

    assert matrix["company_count"] == 3
    assert matrix["comparable_company_count"] == 0
    assert matrix["common_period"] is None
    assert matrix["status"] == "missing"
    assert result["source_status"][0]["status"] == "missing"
    assert result["source_status"][0]["count"] == 0


def test_financial_series_preserves_zero_and_separates_cumulative_from_quarter_growth():
    rows = IndustryResearchSourceCollector._financial_series({
        "fina_indicator": [{
            "end_date": "20260630", "ann_date": "20260820",
            "roe": 0.0, "roe_waa": 12.0,
            "or_yoy": 0.0, "q_sales_yoy": 45.0,
            "netprofit_yoy": 0.0, "q_netprofit_yoy": 52.0,
        }],
        "income": [], "cashflow": [], "balancesheet": [],
    })

    assert rows[0]["roe"] == 0.0
    assert rows[0]["revenue_yoy"] == 0.0
    assert rows[0]["net_profit_yoy"] == 0.0
    assert rows[0]["quarter_revenue_yoy"] == 45.0
    assert rows[0]["quarter_net_profit_yoy"] == 52.0


def test_structured_essay_analysis_is_preserved_as_derived_evidence(essay_db):
    now = utc_naive_now()
    with essay_db.get_session() as session:
        session.add(ResearchNote(
            topic_id="NOTE-OPTICAL-1",
            group_id="GROUP-1",
            group_name="调研纪要",
            title="光模块产业链专家交流",
            content="专家讨论 800G 放量节奏与产能瓶颈。",
            author_name="研究员",
            symbol_codes="300308.SZ",
            content_hash="hash-optical-1",
            created_at=now - timedelta(days=1),
        ))
        # The models intentionally expose no ORM relationship, so make the FK
        # insertion order explicit in this isolated SQLite fixture.
        session.flush()
        session.add(EssayAnalysisRecord(
            topic_id="NOTE-OPTICAL-1",
            status="completed",
            model="deepseek-v4-flash",
            prompt_version="test-v1",
            input_hash="input-hash-optical-1",
            summary="800G 需求增长，但交付节奏取决于关键器件产能。",
            primary_category="industry_research",
            sentiment="bullish",
            time_horizon="medium",
            importance_score=88,
            confidence_score=0.78,
            tags_json=json.dumps(["800G", "产能"], ensure_ascii=False),
            industries_json=json.dumps(["光通信"], ensure_ascii=False),
            themes_json=json.dumps(["光模块"], ensure_ascii=False),
            stock_mentions_json=json.dumps([{"name": "中际旭创", "ts_code": "300308.SZ"}], ensure_ascii=False),
            key_points_json=json.dumps(["800G 需求进入放量窗口", "器件产能决定交付斜率"], ensure_ascii=False),
            catalysts_json=json.dumps(["海外云厂商资本开支上修"], ensure_ascii=False),
            risks_json=json.dumps(["扩产不及预期"], ensure_ascii=False),
            raw_response=json.dumps({
                "contradictions": ["需求高增与短期库存波动并存"],
                "falsification_conditions": ["连续两个季度订单同比下滑"],
                "monitoring_points": ["月度交付量"],
                "earnings_impact": "交付增加可能推升收入，但毛利率取决于产品结构。",
                "valuation_impact": "高增长预期若落空会压缩估值溢价。",
            }, ensure_ascii=False),
            completed_at=now,
        ))
        session.commit()

    collector = IndustryResearchSourceCollector(
        tushare=_FakeTushare(), market=_FakeMarket(), cninfo=_FakeCninfo(), db=essay_db,
    )
    with (
        patch("src.services.industry_research_sources.get_search_service", return_value=_UnavailableSearch()),
        patch.object(collector, "_google_news_fallback", return_value=[]),
    ):
        result = collector.collect(
            topic="光模块", research_type="industry", terms=["光通信", "800G"], lookback_days=90,
        )

    evidence = next(item for item in result["evidence"] if item["evidence_id"] == "note_analysis:NOTE-OPTICAL-1")
    assert evidence["kind"] == "institution_note"
    assert evidence["is_derived"] is True
    assert evidence["derived_from"] == "note:NOTE-OPTICAL-1"
    assert evidence["evidence_level"] == "unverified"
    assert "800G 需求进入放量窗口" in evidence["summary"]
    assert "连续两个季度订单同比下滑" in evidence["summary"]
    assert evidence["structured_analysis"]["earnings_impact"].startswith("交付增加")
    status = next(item for item in result["source_status"] if item["key"] == "structured_essays")
    assert status["status"] == "covered"
    assert status["count"] == 1


def test_google_news_fallback_only_admits_subject_matched_rows():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel>
      <item><title>中际旭创发布半年报</title><link>https://news.example/a</link>
        <pubDate>Sun, 30 Aug 2026 08:00:00 GMT</pubDate>
        <description>&lt;b&gt;光模块订单增长&lt;/b&gt;</description><source>测试财经</source></item>
      <item><title>无关消费新闻</title><link>https://news.example/b</link>
        <pubDate>Sun, 30 Aug 2026 09:00:00 GMT</pubDate>
        <description>与研究对象无关</description><source>测试财经</source></item>
    </channel></rss>""".encode("utf-8")

    class _Response:
        content = xml

        @staticmethod
        def raise_for_status():
            return None

    with patch("src.services.industry_research_sources.requests.get", return_value=_Response()):
        rows = IndustryResearchSourceCollector._google_news_fallback(
            topic="中际旭创", symbol="300308.SZ", terms=["光模块"], research_type="company", limit=10,
        )

    assert len(rows) == 1
    assert rows[0]["title"] == "中际旭创发布半年报"
    assert rows[0]["source"] == "测试财经"
    assert rows[0]["date"] == "2026-08-30"


def test_multi_category_cninfo_rows_use_title_classification_not_every_requested_label():
    from src.services.cninfo_announcement_service import ANNOUNCEMENT_CATEGORIES

    categories = IndustryResearchSourceCollector._announcement_category_names({
        "title": "关于签订重大经营合同暨风险提示的公告",
        "category_codes": list(ANNOUNCEMENT_CATEGORIES),
        "category_names": list(ANNOUNCEMENT_CATEGORIES.values()),
    })

    assert categories == ["风险提示", "日常经营"]
    assert len(categories) < len(ANNOUNCEMENT_CATEGORIES)


def test_financial_series_selects_latest_revision_instead_of_api_row_order():
    datasets = {
        "fina_indicator": [
            {"end_date": "20260630", "ann_date": "20260810", "roe": 7.0},
            {"end_date": "20260630", "ann_date": "20260825", "update_flag": "1", "roe": 8.5},
        ],
        "income": [
            {"end_date": "20260630", "ann_date": "20260825", "update_flag": "1", "report_type": "4", "revenue": 520, "n_income_attr_p": 82},
            {"end_date": "20260630", "ann_date": "20260810", "update_flag": "0", "report_type": "1", "revenue": 500, "n_income_attr_p": 80},
        ],
        "balancesheet": [{"end_date": "20260630", "ann_date": "20260825", "total_assets": 1000}],
        "cashflow": [{"end_date": "20260630", "ann_date": "20260825", "n_cashflow_act": 70}],
    }

    row = IndustryResearchSourceCollector._financial_series(datasets)[0]

    assert row["announcement_date"] == "20260825"
    assert row["roe"] == 8.5
    assert row["revenue"] == 520.0
    assert row["net_profit"] == 82.0
    assert row["revision_count"] == 2
    assert row["source_versions"]["income"]["selected_report_type"] == "4"


def _text_pdf_bytes(value: str) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=500)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
    })
    stream = DecodedStreamObject()
    safe_value = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 400 Td ({safe_value}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class _FakePdfResponse:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content
        self.headers = {"Content-Length": str(len(content)), "Content-Type": "application/pdf"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    @staticmethod
    def raise_for_status():
        return None

    def iter_content(self, _chunk_size):
        midpoint = max(1, len(self.content) // 2)
        yield self.content[:midpoint]
        yield self.content[midpoint:]


class _FakePdfSession:
    def __init__(self, content: bytes):
        self.content = content
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakePdfResponse(self.content)


def test_broker_pdf_artifact_is_allowlisted_bounded_and_cached(tmp_path):
    session = _FakePdfSession(_text_pdf_bytes("Optical module demand and revenue growth"))
    service = BrokerReportArtifactService(
        session=session,
        cache_dir=tmp_path / "broker-pdf-cache",
        allowed_hosts=["pdf.dfcfw.com"],
    )
    url = "https://pdf.dfcfw.com/pdf/H3_AP202608241828345704_1.pdf?source=test.pdf"

    first = service.fetch_text(url)
    second = service.fetch_text(url)

    assert first["cached"] is False
    assert second["cached"] is True
    assert len(session.calls) == 1
    assert session.calls[0][1]["stream"] is True
    assert session.calls[0][1]["allow_redirects"] is False
    assert "Optical module demand" in first["text"]
    assert first["document_hash"] == second["document_hash"]
    assert first["text_hash"] == second["text_hash"]
    assert first["text_chars"] > 20
    with pytest.raises(BrokerReportArtifactError, match="允许"):
        service.fetch_text("https://127.0.0.1/private.pdf")
    with pytest.raises(BrokerReportArtifactError, match="允许"):
        service.fetch_text("http://pdf.dfcfw.com/pdf/report.pdf")


class _FakeBrokerArtifacts:
    def __init__(self):
        self.calls = []

    def fetch_text(self, url):
        self.calls.append(url)
        if "broken" in url:
            raise BrokerReportArtifactError("测试 PDF 损坏")
        text = "光模块行业深度研究：800G 需求增长，供应链与盈利能力仍需结合公司公告核验。" * 20
        return {
            "text": text,
            "text_hash": "text-hash",
            "text_chars": len(text),
            "document_hash": "document-hash",
            "document_bytes": 2048,
            "cached": False,
            "page_count": 30,
            "pages_read": 30,
            "extraction_method": "pypdf_text",
        }


def test_broker_report_pdf_selection_keeps_success_when_one_document_fails(essay_db):
    today = utc_naive_now().date()
    with essay_db.get_session() as session:
        session.add_all([
            ResearchReportRecord(
                report_key="report-success", trade_date=today,
                title="光模块行业深度研究：800G进入放量期", abstract="光模块产业链与需求分析",
                report_type="行业深度", author="研究员甲", broker="测试证券",
                industry="光通信", pdf_url="https://pdf.dfcfw.com/pdf/success.pdf",
                tags_json="[]",
            ),
            ResearchReportRecord(
                report_key="report-broken", trade_date=today - timedelta(days=1),
                title="光模块产业链深度报告", abstract="光模块供需格局",
                report_type="行业深度", author="研究员乙", broker="另一证券",
                industry="光通信", pdf_url="https://pdf.dfcfw.com/pdf/broken.pdf",
                tags_json="[]",
            ),
            ResearchReportRecord(
                report_key="report-unrelated", trade_date=today,
                title="白酒行业月报", abstract="消费行业跟踪",
                report_type="行业月报", author="研究员丙", broker="测试证券",
                industry="食品饮料", pdf_url="https://pdf.dfcfw.com/pdf/unrelated.pdf",
                tags_json="[]",
            ),
        ])
        session.commit()

    artifacts = _FakeBrokerArtifacts()
    collector = IndustryResearchSourceCollector(
        tushare=_FakeTushare(), market=_FakeMarket(), cninfo=_FakeCninfo(),
        broker_report_artifacts=artifacts, db=essay_db,
    )
    with (
        patch.dict("os.environ", {"INDUSTRY_RESEARCH_BROKER_PDF_MAX_DOCS": "2"}),
        patch("src.services.industry_research_sources.get_search_service", return_value=_UnavailableSearch()),
        patch.object(collector, "_google_news_fallback", return_value=[]),
    ):
        result = collector.collect(
            topic="光模块", research_type="industry", terms=["光通信", "800G"], lookback_days=730,
        )

    fulltext = [item for item in result["evidence"] if item.get("kind") == "broker_report_text"]
    assert len(fulltext) == 1
    assert fulltext[0]["evidence_id"] == "broker_report_text:report-success"
    assert fulltext[0]["document_text_hash"] == "text-hash"
    assert fulltext[0]["document_file_hash"] == "document-hash"
    assert "800G 需求增长" in fulltext[0]["document_text"]
    assert artifacts.calls == [
        "https://pdf.dfcfw.com/pdf/success.pdf",
        "https://pdf.dfcfw.com/pdf/broken.pdf",
    ]
    status = next(item for item in result["source_status"] if item["key"] == "research_report_fulltext")
    assert status["status"] == "partial"
    assert status["matched"] == 2
    assert status["requested"] == 2
    assert status["content_count"] == 1
    assert status["content_failures"] == 1
    assert status["failures"][0]["report_key"] == "report-broken"
