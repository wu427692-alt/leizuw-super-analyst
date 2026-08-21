from datetime import date, datetime, timedelta
import json
import os

import pytest

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
