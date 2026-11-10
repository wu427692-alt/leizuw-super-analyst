"""Offline command-center data contracts against the actual storage schema."""

from datetime import date, datetime, timedelta
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from api.v1.endpoints import command
from src import config
from src.storage import ConceptThemeRecord, IntelligenceItem, ResearchReportRecord, StockDaily


@pytest.fixture(autouse=True)
def no_external_side_effects(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("No live API or model calls are allowed in these tests")

    monkeypatch.setattr(command, "_internal_call", unexpected)
    monkeypatch.setattr(command, "_deepseek_search", unexpected)
    monkeypatch.setattr(command, "_record_gap", lambda *args: None)


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "configured database.db"
    engine = create_engine(f"sqlite:///{path}")
    tables = [IntelligenceItem.__table__, ConceptThemeRecord.__table__,
              ResearchReportRecord.__table__, StockDaily.__table__]
    for table in tables:
        table.create(engine)
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(path)))
    yield engine, path
    engine.dispose()


def test_local_rows_uses_configured_existing_database_and_cannot_write(database):
    engine, path = database
    with engine.begin() as con:
        con.execute(StockDaily.__table__.insert(), [{"code": "603306", "date": date.today(), "close": 80}])
    assert command._local_rows("SELECT code FROM stock_daily", ()) == [("603306",)]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        command._local_rows("UPDATE stock_daily SET close = ?", (1,))
    assert command._local_rows("SELECT close FROM stock_daily", ()) == [(80,)]
    assert path.is_file()


def test_local_rows_does_not_create_missing_database(tmp_path, monkeypatch):
    path = tmp_path / "missing" / "stock.db"
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(path)))
    with pytest.raises(sqlite3.OperationalError):
        command._local_rows("SELECT 1", ())
    assert not path.exists()
    assert not path.parent.exists()


def test_news_uses_real_summary_fields_and_does_not_match_empty_scope(database):
    engine, _ = database
    with engine.begin() as con:
        con.execute(IntelligenceItem.__table__.insert(), [
            {"title": "公司更新", "summary": "华懋科技最新订单", "source_name": "官方新闻源",
             "url": "https://example.test/target", "published_at": datetime.now(), "scope_value": ""},
            {"title": "完全无关", "summary": "机器人", "source_name": "其他来源",
             "url": "https://example.test/other", "published_at": datetime.now(), "scope_value": ""},
            {"title": "华懋科技旧闻", "summary": "旧订单", "source_name": "旧来源",
             "url": "https://example.test/old", "published_at": datetime.now() - timedelta(days=90), "scope_value": ""},
        ])
    result = command._resolve_data("news", "华懋科技", "", 30, 10, "cookie")
    assert result["ok"]
    assert result["source"] == "local_db"
    assert result["data"]["items"] == [{"title": "公司更新", "source": "官方新闻源",
                                        "published_at": result["data"]["items"][0]["published_at"],
                                        "summary": "华懋科技最新订单", "url": "https://example.test/target"}]


def test_news_symbol_matches_canonical_scope(database):
    engine, _ = database
    with engine.begin() as con:
        con.execute(IntelligenceItem.__table__.insert(), [{
            "title": "华懋业绩", "summary": "经营信息", "scope_value": "603306.SH",
            "url": "https://example.test/scoped", "published_at": datetime.now(),
        }])
    result = command._resolve_data("news", "", "603306.SH", 30, 10, "")
    assert result["ok"]
    assert result["data"]["items"][0]["title"] == "华懋业绩"


def _insert_reports(engine):
    with engine.begin() as con:
        con.execute(ResearchReportRecord.__table__.insert(), [
            {"report_key": "target", "title": "华懋科技深度研究", "company_name": "华懋科技",
             "abstract": "汽车安全", "broker": "券商甲", "ts_code": "603306.SH",
             "trade_date": date.today(), "pdf_url": "https://example.test/target.pdf"},
            {"report_key": "other", "title": "其他公司", "company_name": "其他公司",
             "abstract": "完全无关", "broker": "券商乙", "ts_code": "300476.SZ",
             "trade_date": date.today(), "pdf_url": "https://example.test/other.pdf"},
            {"report_key": "old", "title": "华懋科技旧报告", "company_name": "华懋科技",
             "abstract": "历史资料", "broker": "券商甲", "ts_code": "603306.SH",
             "trade_date": date.today() - timedelta(days=300), "pdf_url": "https://example.test/old.pdf"},
        ])


@pytest.mark.parametrize("query,symbol", [("华懋科技", ""), ("", "603306"), ("", "603306.SH")])
def test_report_does_not_broaden_empty_target_clause(database, query, symbol):
    engine, _ = database
    _insert_reports(engine)
    result = command._resolve_data("report", query, symbol, 30, 20, "")
    assert result["ok"]
    assert [item["title"] for item in result["data"]["items"]] == ["华懋科技深度研究"]


def test_report_query_wildcards_are_literal(database):
    engine, _ = database
    _insert_reports(engine)
    result = command._resolve_data("report", "%", "", 30, 20, "")
    assert result["ok"] is False
    assert result["attempts"] == [{"source": "local_db", "count": 0}]


@pytest.mark.parametrize("kind,query,symbol", [
    ("report", "", ""), ("history", "走势", ""), ("financial", "", ""),
    ("annual", "年报", ""), ("annual", "", "AAPL"), ("history", "", "../../data"),
])
def test_missing_or_invalid_targets_do_not_access_sources(kind, query, symbol, monkeypatch):
    monkeypatch.setattr(command, "_local_rows", lambda *args: pytest.fail("must not read all symbols"))
    result = command._resolve_data(kind, query, symbol, 365, 20, "")
    assert result["ok"] is False
    assert result["attempts"] == []


def test_concept_uses_actual_storage_names(database):
    engine, _ = database
    with engine.begin() as con:
        con.execute(ConceptThemeRecord.__table__.insert(), [{
            "source": "ths", "source_code": "X1", "name": "CPO光模块",
            "canonical_name": "光通信", "constituent_count": 30,
        }])
    result = command._resolve_data("concept", "光通信", "", 365, 20, "")
    assert result["data"]["items"] == [{"theme": "光通信", "members": 30,
                                        "source": "ths", "source_code": "X1"}]


def test_schema_error_is_not_reported_as_a_matching_empty_result(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    sqlite3.connect(path).close()
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(path)))
    result = command._resolve_data("report", "华懋科技", "", 365, 20, "")
    assert result["ok"] is False
    assert "读取失败" in result["error"]
    assert "no such table" in result["attempts"][0]["error"]


def test_financial_calls_actual_tushare_route_and_reads_rows(monkeypatch):
    calls = []

    def internal(method, path, cookie, params, **kwargs):
        calls.append((method, path, cookie, params))
        return {"ok": True, "data": {"resource": "fina_indicator", "rows": [{"roe": 12}]}}

    monkeypatch.setattr(command, "_internal_call", internal)
    result = command._resolve_data("financial", "", "603306.SH", 365, 20, "owned-cookie")
    assert calls[0][:3] == ("POST", "/api/v1/financial-data/tushare/fina_indicator", "owned-cookie")
    assert calls[0][3]["body"]["params"]["ts_code"] == "603306.SH"
    assert "api_name" not in calls[0][3]["body"]
    assert result["data"]["items"] == [{"roe": 12}]


def test_annual_is_cninfo_annual_metadata_not_broker_research_or_download(monkeypatch):
    calls = []
    annual = {"title": "2025年年度报告", "report_type": "annual", "pdf_url": "https://example.test/annual.pdf"}

    def internal(method, path, cookie, params, **kwargs):
        calls.append((method, path, params))
        return {"ok": True, "data": annual}

    monkeypatch.setattr(command, "_internal_call", internal)
    result = command._resolve_data("annual", "", "603306.SH", 365, 20, "")
    assert calls == [("GET", "/api/v1/financial-data/annual-report",
                      {"query": {"symbol": "603306", "report_type": "annual", "download": False}})]
    assert result["source"] == "cninfo_annual_report"
    assert result["data"]["items"] == [annual]


def test_api_failure_remains_failure_even_with_nonempty_body(monkeypatch):
    monkeypatch.setattr(command, "_internal_call", lambda *a, **kw: {
        "ok": False, "status": 403, "data": {"detail": "not authorized", "rows": [{"roe": 12}]},
    })
    result = command._resolve_data("financial", "", "603306", 365, 20, "")
    assert result["ok"] is False
    assert result["attempts"][0]["status"] == 403
    assert result["attempts"][0]["error"] == "not authorized"


def test_history_fallback_is_exact_symbol_and_date_bounded(database, monkeypatch):
    engine, _ = database
    with engine.begin() as con:
        con.execute(StockDaily.__table__.insert(), [
            {"code": "603306", "date": date.today(), "close": 80},
            {"code": "603306", "date": date.today() - timedelta(days=100), "close": 60},
            {"code": "6033069", "date": date.today(), "close": 1},
        ])
    monkeypatch.setattr(command, "_internal_call", lambda *a, **kw: {"ok": True, "data": {"data": []}})
    result = command._resolve_data("history", "", "603306.SH", 30, 20, "")
    assert result["source"] == "local_db"
    assert result["data"]["rows"] == [{"date": date.today().isoformat(), "close": 80}]


@pytest.mark.parametrize("label,expected", [("日线", "daily"), ("周线", "weekly"), ("月K", "monthly"), ("年线", "yearly")])
def test_history_payload_uses_requested_period_and_full_response(monkeypatch, label, expected):
    calls = []

    def internal(method, path, cookie, params, **kwargs):
        calls.append((path, params, kwargs))
        return {"ok": True, "data": {"stock_name": "华懋科技", "data": [
            {"date": "2026-10-19", "close": "80.5"},
            {"date": "2026-10-20", "close": "nan"},
        ]}}

    monkeypatch.setattr(command, "_internal_call", internal)
    result = command._stock_history_payload(f"603306 最近 120 天{label}", "")
    assert calls[0][0] == "/api/v1/stocks/603306/history"
    assert calls[0][1] == {"query": {"period": expected, "days": 120, "refresh": False}}
    assert calls[0][2]["compact_limit"] >= 1000000
    assert result["series"] == [{"date": "2026-10-19", "close": 80.5}]
    assert result["period"] == expected


def test_history_payload_does_not_use_date_as_stock_code():
    assert command._stock_history_payload("查看 20261021 的日线", "")["ok"] is False
