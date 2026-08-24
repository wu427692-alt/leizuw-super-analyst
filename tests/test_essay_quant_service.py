from datetime import date, datetime, timedelta
import json
import os

import pytest
from sqlalchemy import event

from src.config import Config
from src.services.essay_quant_service import EssayQuantService
from src.storage import DatabaseManager, EssayAnalysisRecord, ResearchNote, StockDaily


@pytest.fixture()
def quant(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "essay-quant.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    event_at = datetime(2026, 8, 1, 2, 0)
    with db.get_session() as session:
        session.add(ResearchNote(
            topic_id="topic-1", group_id="group-1", group_name="卖方研究纪要",
            title="【中信电子组】首次推荐测试公司", content="重点推荐，目标价空间明确",
            author_name="中信电子组", topic_type="talk", symbol_codes="000001.SZ",
            content_hash="hash", created_at=event_at,
        ))
        session.flush()
        session.add(EssayAnalysisRecord(
            topic_id="topic-1", status="completed", model="deepseek", prompt_version="v1", input_hash="hash",
            summary="首次覆盖并看多", sentiment="bullish", importance_score=80, confidence_score=0.9,
            stock_mentions_json=json.dumps([{"ts_code": "000001.SZ", "name": "测试公司", "stance": "bullish", "confidence": 0.9}]),
            raw_response=json.dumps({"novelty_score": 80}),
        ))
        for offset, close in enumerate((10.0, 10.5, 11.0, 11.5, 12.0, 12.5)):
            session.add(StockDaily(code="000001", date=date(2026, 8, 2) + timedelta(days=offset), open=10.0 if offset == 0 else close - .2, close=close, data_source="test"))
            session.add(StockDaily(code="000300", date=date(2026, 8, 2) + timedelta(days=offset), open=100.0, close=100.0 + offset, data_source="test"))
        session.commit()
    try:
        yield EssayQuantService(db=db)
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None: os.environ.pop("DATABASE_PATH", None)
        else: os.environ["DATABASE_PATH"] = previous


def test_event_study_uses_next_open_and_nth_session_close(quant, monkeypatch):
    monkeypatch.setattr("src.services.essay_quant_service.utc_naive_now", lambda: datetime(2026, 8, 10))
    result = quant.run({"lookback_days": 30, "holding_periods": [5], "min_importance": 60, "transaction_cost_bps": 0}, refresh_prices=False, persist=False)
    event = result["events"][0]
    assert event["first_mention"] is True
    assert event["entry_date"] == "2026-08-02"
    assert event["returns"]["5"] == 20.0
    assert result["summary"]["metrics"][0]["win_rate"] == 100.0
    assert result["research_group_rankings"][0]["research_group"] == "中信电子组"


def test_custom_rule_is_persisted(quant):
    saved = quant.save_rule({"name": "中信电子跟踪", "source_query": "中信电子", "holding_periods": [5, 20]})
    assert saved["source_query"] == "中信电子"
    assert saved["holding_periods"] == [5, 20]
    assert quant.list_rules()["total"] == 1


def test_institution_dashboard_is_not_overwritten_by_custom_run(quant, monkeypatch):
    monkeypatch.setattr("src.services.essay_quant_service.utc_naive_now", lambda: datetime(2026, 8, 10))
    baseline = quant.run(
        {"name": "后台全量机构预计算", "source_query": "", "lookback_days": 30, "holding_periods": [5]},
        refresh_prices=False, persist=True,
    )
    custom = quant.run(
        {"name": "中信电子跟踪策略", "source_query": "中信", "lookback_days": 30, "holding_periods": [5]},
        refresh_prices=False, persist=True,
    )
    assert custom["run_id"] > baseline["run_id"]
    assert quant.latest_dashboard()["run_id"] == custom["run_id"]
    assert quant.latest_institution_dashboard()["run_id"] == baseline["run_id"]
    visible_runs = quant.list_runs()
    assert visible_runs["total"] == 1
    assert [item["id"] for item in visible_runs["items"]] == [custom["run_id"]]
    assert visible_runs["items"][0]["name"] == "中信电子跟踪策略"


def test_dashboard_candidate_lookup_does_not_materialize_every_result(quant, monkeypatch):
    monkeypatch.setattr("src.services.essay_quant_service.utc_naive_now", lambda: datetime(2026, 8, 10))
    quant.run(
        {"name": "后台全量机构预计算", "source_query": "", "lookback_days": 30, "holding_periods": [5]},
        refresh_prices=False,
        persist=True,
    )
    quant.run(
        {"name": "用户策略", "source_query": "中信", "lookback_days": 30, "holding_periods": [5]},
        refresh_prices=False,
        persist=True,
    )
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(quant.db._engine, "before_cursor_execute", capture)
    try:
        quant.latest_dashboard()
        quant.latest_institution_dashboard()
        quant.list_runs()
    finally:
        event.remove(quant.db._engine, "before_cursor_execute", capture)

    candidate_queries = [
        statement for statement in statements
        if "essay_quant_runs" in statement and "rule_json" in statement and "LIMIT" in statement
    ]
    assert candidate_queries
    assert all("result_json" not in statement.split("FROM", 1)[0] for statement in candidate_queries)


def test_institution_dashboard_reuses_snapshot_until_latest_run_changes(quant, monkeypatch):
    monkeypatch.setattr("src.services.essay_quant_service.utc_naive_now", lambda: datetime(2026, 8, 10))
    baseline = quant.run(
        {"name": "后台全量机构预计算", "source_query": "", "lookback_days": 30, "holding_periods": [5]},
        refresh_prices=False,
        persist=True,
    )
    assert quant.latest_institution_dashboard()["run_id"] == baseline["run_id"]

    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(quant.db._engine, "before_cursor_execute", capture)
    try:
        assert quant.latest_institution_dashboard()["run_id"] == baseline["run_id"]
    finally:
        event.remove(quant.db._engine, "before_cursor_execute", capture)

    assert any("max(essay_quant_runs.id)" in statement.lower() for statement in statements)
    assert not any("rule_json" in statement for statement in statements)
    assert not any("result_json" in statement for statement in statements)


def test_unanalyzed_history_with_explicit_symbol_is_available_to_backtest(quant, monkeypatch):
    monkeypatch.setattr("src.services.essay_quant_service.utc_naive_now", lambda: datetime(2026, 8, 10))
    with quant.db.get_session() as session:
        session.add(ResearchNote(
            topic_id="topic-raw", group_id="group-1", group_name="卖方研究纪要",
            title="【华泰电子组】重点推荐历史样本", content="胜宏科技明确看好，订单增长",
            author_name="华泰电子组", topic_type="talk", symbol_codes="",
            content_hash="raw-hash", created_at=datetime(2026, 8, 1, 3, 0),
        ))
        for offset, close in enumerate((20.0, 20.5, 21.0, 21.5, 22.0, 22.5)):
            session.add(StockDaily(
                code="300476", date=date(2026, 8, 2) + timedelta(days=offset),
                open=20.0 if offset == 0 else close - .2, close=close, data_source="test",
            ))
        session.commit()

    result = quant.run(
        {"lookback_days": 30, "holding_periods": [5], "min_importance": 99, "min_confidence": 1.0,
         "raw_note_policy": "include", "transaction_cost_bps": 0},
        refresh_prices=False,
        persist=False,
    )
    raw_event = next(item for item in result["events"] if item["topic_id"] == "topic-raw")
    assert raw_event["analysis_status"] == "raw_unanalyzed"
    assert raw_event["symbol"] == "300476.SZ"
    assert raw_event["stock_name"] == "胜宏科技"
    assert raw_event["stance"] == "bullish"
    assert raw_event["returns"]["5"] == 10.0
    assert result["data_quality"]["raw_unanalyzed_event_count"] == 1


def test_default_rule_excludes_raw_notes_and_reports_robustness(quant, monkeypatch):
    monkeypatch.setattr("src.services.essay_quant_service.utc_naive_now", lambda: datetime(2026, 8, 10))
    result = quant.run(
        {"lookback_days": 30, "holding_periods": [5], "transaction_cost_bps": 12},
        refresh_prices=False,
        persist=False,
    )
    assert result["rule"]["raw_note_policy"] == "exclude"
    assert result["events"][0]["returns"]["5"] == 19.88
    assert result["robustness"]["sample_count"] == 1
    assert len(result["robustness"]["sensitivity"]) == 5


def test_research_catalog_and_run_history_use_real_local_tables(quant, monkeypatch):
    monkeypatch.setattr("src.services.essay_quant_service.utc_naive_now", lambda: datetime(2026, 8, 10))
    quant.run({"lookback_days": 30, "holding_periods": [5]}, refresh_prices=False, persist=True)
    catalog = quant.research_catalog()
    essays = next(item for item in catalog["assets"] if item["key"] == "essays")
    assert essays["count"] == 1
    assert essays["status"] == "ready"
    assert quant.list_runs()["items"][0]["event_count"] == 1


def test_each_research_method_has_its_own_template_and_execution_path(quant, monkeypatch):
    monkeypatch.setattr("src.services.essay_quant_service.utc_naive_now", lambda: datetime(2026, 8, 10))
    methods = quant.research_catalog()["methods"]

    assert {item["key"] for item in methods} == {
        "event_study", "multi_factor", "hybrid_intelligence", "institution_track", "portfolio",
    }
    assert len({item["template"]["strategy_type"] for item in methods}) == 5
    assert all(item["used_data"] and item["engine"] and item["output"] for item in methods)

    results = [
        quant.run({**item["template"], "lookback_days": 30, "holding_periods": [5]}, refresh_prices=False, persist=False)
        for item in methods
    ]
    assert len({result["method_analysis"]["selection_rule"] for result in results}) == 5
    assert [result["rule"]["strategy_type"] for result in results] == [
        "essay_event", "multi_factor", "hybrid_intelligence", "institution_track", "portfolio",
    ]
    assert all("selected_event_count" in result["method_analysis"] for result in results)


def test_company_name_is_not_misclassified_as_research_group():
    note = ResearchNote(title="【东方电气】订单跟踪", content="公司经营更新", author_name="立秋")
    assert EssayQuantService._research_group(note) == "其他来源"


def test_raw_note_resolves_stock_name_from_local_index():
    note = ResearchNote(title="机构首次覆盖平安银行", content="基本面改善，维持推荐", symbol_codes="")
    mentions = EssayQuantService._raw_note_mentions(note, "bullish")
    assert any(item["ts_code"] == "000001.SZ" and item["name"] == "平安银行" for item in mentions)


def test_raw_note_rejects_date_like_six_digit_false_symbol():
    note = ResearchNote(title="湖南裕能中报更新", content="20260820发布业绩", symbol_codes="260820.SZ")
    mentions = EssayQuantService._raw_note_mentions(note, "bullish")
    assert all(item["ts_code"] != "260820.SZ" for item in mentions)
    assert any(item["ts_code"] == "301358.SZ" and item["name"] == "湖南裕能" for item in mentions)


class _CrossSectionTushare:
    available = True

    def __init__(self):
        self.calls = []

    def query(self, api_name, *, params=None, fields=None):
        self.calls.append((api_name, dict(params or {})))
        if api_name == "trade_cal":
            return {"rows": [
                {"cal_date": "20260819", "is_open": 1},
                {"cal_date": "20260820", "is_open": 1},
            ]}
        if api_name == "daily":
            trade_date = params["trade_date"]
            return {"rows": [
                {"ts_code": "000001.SZ", "trade_date": trade_date, "open": 12.0,
                 "high": 12.6, "low": 11.8, "close": 12.5, "pct_chg": 1.2,
                 "vol": 1234.0, "amount": 5678.0},
                # A cross-sectional response contains the whole market; the
                # quant synchronizer must persist only its resolved universe.
                {"ts_code": "600000.SH", "trade_date": trade_date, "open": 9.0,
                 "high": 9.1, "low": 8.9, "close": 9.0, "pct_chg": 0.0,
                 "vol": 100.0, "amount": 200.0},
            ]}
        if api_name == "index_daily":
            return {"rows": [
                {"ts_code": "000300.SH", "trade_date": "20260819", "open": 100.0,
                 "high": 101.0, "low": 99.0, "close": 100.5, "pct_chg": 0.5,
                 "vol": 1.0, "amount": 2.0},
                {"ts_code": "000300.SH", "trade_date": "20260820", "open": 100.5,
                 "high": 102.0, "low": 100.0, "close": 101.0, "pct_chg": 0.5,
                 "vol": 1.0, "amount": 2.0},
            ]}
        raise AssertionError(f"unexpected Tushare API: {api_name}")


def test_cross_section_refresh_updates_every_resolved_symbol_and_reports_honest_freshness(quant, monkeypatch):
    monkeypatch.setenv("ESSAY_QUANT_RECENT_TRADE_DAYS", "2")
    fake = _CrossSectionTushare()
    quant.tushare = fake

    target, refreshed_count, warnings = quant._hydrate_market_freshness(["000001.SZ"], "000300.SH")
    assert target == date(2026, 8, 20)
    assert refreshed_count == 1
    assert warnings == []
    assert [call[0] for call in fake.calls].count("daily") == 2
    assert not any(call[0] == "daily" and "ts_code" in call[1] for call in fake.calls)

    quant._target_price_date = target
    quality = quant._price_freshness(
        [{"symbol": "000001.SZ"}],
        quant._price_map(["000001.SZ"], 60),
    )
    assert quality["price_target_date"] == "2026-08-20"
    assert quality["price_freshness_ratio"] == 100.0
    assert quality["current_price_symbol_count"] == 1
    assert quality["stale_price_symbol_count"] == 0
    with quant.db.get_session() as session:
        refreshed = session.query(StockDaily).filter_by(code="000001", date=date(2026, 8, 20)).one()
        assert refreshed.close == 12.5
        assert refreshed.data_source == "tushare:daily:cross_section"
        assert session.query(StockDaily).filter_by(code="600000", date=date(2026, 8, 20)).count() == 0


def test_price_freshness_is_cross_sectional_not_global_max(quant):
    quant._target_price_date = date(2026, 8, 20)
    events = [{"symbol": "000001.SZ"}, {"symbol": "600000.SH"}]
    prices = {
        "000001": [{"date": date(2026, 8, 20)}],
        "600000": [{"date": date(2026, 8, 19)}],
    }
    quality = quant._price_freshness(events, prices)
    assert quality["price_latest_date"] == "2026-08-20"
    assert quality["price_oldest_symbol_date"] == "2026-08-19"
    assert quality["current_price_symbol_count"] == 1
    assert quality["stale_price_symbol_count"] == 1
    assert quality["price_freshness_ratio"] == 50.0
    assert quality["freshness_status"] == "stale"


def test_source_query_uses_terms_and_does_not_require_direction_word(quant, monkeypatch):
    monkeypatch.setattr("src.services.essay_quant_service.utc_naive_now", lambda: datetime(2026, 8, 10))
    result = quant.run(
        {"source_query": "中信电子组 看多", "signal_direction": "bullish", "lookback_days": 30,
         "holding_periods": [5], "transaction_cost_bps": 0},
        refresh_prices=False,
        persist=False,
    )
    assert result["summary"]["event_count"] == 1
    assert result["events"][0]["research_group"] == "中信电子组"


def test_source_query_ignores_generic_corpus_words():
    assert EssayQuantService._source_query_terms("小作文 看多") == []
    assert EssayQuantService._source_query_terms("研报观点 语料") == []
