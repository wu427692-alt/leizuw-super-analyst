"""Report attribution and numerical-consensus regressions, with synthetic data only."""

from contextlib import contextmanager
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event, select
from sqlalchemy.orm import Session

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.investment_monitor_service import InvestmentMonitorService
from src.storage import DatabaseManager, MonitoringEventRecord, MonitoringSourceRecord, StockDaily, utc_naive_now
from src.utils.research_stock_relation import direct_research_condition, research_stock_relation


TARGET = "300308.SZ"
OTHER = "300684.SZ"


def report(metrics=None, **kwargs):
    return {"id": 1, "event_type": "research_report_pdf", "source_key": "tushare.research_pdf",
            "symbols": [TARGET], "title": "研究报告", "summary": "", "metrics": metrics or {}, **kwargs}


CASES = [
    (report({"ts_code": TARGET, "name": "中际旭创"}), None, "company_direct"),
    (report({"ts_code": "sz300308"}), None, "company_direct"),
    (report({"ts_code": "300308"}), None, "company_direct"),
    (report({"ts_code": "300308.SH"}), None, "other_company_mention"),
    (report({"ts_code": OTHER, "name": "中石科技", "abstr": "中际旭创的需求值得关注"}),
     {"ts_code": OTHER, "name": "中石科技", "abstr": "中际旭创的需求值得关注"}, "other_company_mention"),
    (report({"ts_code": OTHER}, title="中际旭创深度研究"), None, "other_company_mention"),
    (report({"report_type": "行业研报", "ind_name": "光通信"}), None, "industry_related"),
    (report({"report_type": "个股研报", "ind_name": "电子化学品"}), None, "unknown"),
    (report({}, title="中际旭创深度研究"), None, "unknown"),
    (report(), None, "unknown"),
    (report({"ts_code": "not-a-stock"}), None, "unknown"),
    (report({"ts_code": TARGET}), {"ts_code": OTHER}, "unknown"),
    (report(), [{"ts_code": TARGET}, {"ts_code": OTHER}], "unknown"),
    (report(), [{"ts_code": OTHER}, {"ts_code": TARGET}], "unknown"),
    (report(), [{"ts_code": TARGET, "name": "中际旭创", "quarter": "2026"},
                {"ts_code": TARGET, "quarter": "2027"}], "company_direct"),
    (report({"records": [{"ts_code": TARGET}]}), None, "company_direct"),
    (report({"records": {"ts_code": TARGET}}), None, "company_direct"),
    (report({"ts_code": TARGET, "records": {"ts_code": OTHER}}), None, "unknown"),
    (report({"ts_code": TARGET}), "malformed-json", "unknown"),
    (report({"ts_code": OTHER}), "malformed-json", "other_company_mention"),
    (report({"ts_code": OTHER}), {"ts_code": "broken-code"}, "other_company_mention"),
    (report({"ts_code": "broken-code"}), {"ts_code": OTHER}, "other_company_mention"),
    (report({"ts_code": TARGET}), {"ts_code": "broken-code"}, "unknown"),
    (report({"report_symbol": TARGET, "_evidence": {"origin_apis": ["report_rc"]}},
            event_type="institution_forecast", source_key="tushare.institution"), None, "company_direct"),
    (report({"report_symbol": TARGET}, event_type="institution_forecast", source_key="tushare.institution"),
     None, "unknown"),
    (report({"report_symbol": TARGET, "_evidence": {"origin_apis": "report_rc"}},
            event_type="institution_forecast", source_key="tushare.institution"), None, "unknown"),
    (report({"report_symbol": TARGET, "_evidence": {"origin_apis": ["report_rc"]}},
            event_type="institution_forecast", source_key="custom.provider"), None, "unknown"),
    (report({"ts_code": TARGET}, event_type="institution_survey"), None, "company_direct"),
    (report({}, event_type="institution_survey", title="中际旭创机构调研"), None, "unknown"),
    (report({"ts_code": OTHER}, event_type="broker_recommendation"), None, "other_company_mention"),
]


@pytest.mark.parametrize(("item", "raw", "kind"), CASES)
def test_relationship_requires_original_subject_not_recall_symbol(item, raw, kind):
    relation = research_stock_relation(item, TARGET, raw=raw, stock_name="中际旭创")
    assert relation["kind"] == kind
    assert relation["target_symbol"] == TARGET
    if kind == "company_direct":
        assert relation["subject_symbol"] == TARGET
    if kind == "other_company_mention":
        assert relation["subject_symbol"] != TARGET


def test_real_other_company_counterexample_is_explained_without_losing_its_identity():
    original = {"ts_code": OTHER, "name": "中石科技", "report_type": "个股研报",
                "title": "中石科技研究", "abstr": "散热需求延伸，行业客户中际旭创值得关注。"}
    relation = research_stock_relation(report(original), TARGET, raw=original, stock_name="中际旭创")
    assert relation == {"kind": "other_company_mention", "target_symbol": TARGET,
                        "subject_symbol": OTHER, "subject_name": "中石科技", "match_field": "abstract",
                        "match_text": original["abstr"]}


def test_excerpt_is_bounded_plain_text_and_originals_are_not_returned():
    item = report({"ts_code": OTHER, "name": "<b>中石科技</b>",
                   "abstr": "前文" * 400 + "中际旭创<script>测试</script>\x00" + "后文" * 400})
    relation = research_stock_relation(item, TARGET, stock_name="中际旭创")
    assert relation["subject_name"] == "中石科技"
    assert "中际旭创" in relation["match_text"]
    assert len(relation["match_text"]) <= 240
    assert "<" not in relation["match_text"] and "\x00" not in relation["match_text"]
    assert "metrics" not in relation and "raw_payload" not in relation


@pytest.fixture()
def repository(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'relations.db'}")
    for model in (MonitoringSourceRecord, MonitoringEventRecord, StockDaily):
        model.__table__.create(engine)

    class TemporaryDatabase:
        @contextmanager
        def get_session(self):
            with Session(engine, autoflush=False) as session:
                yield session

    monkeypatch.setattr(DatabaseManager, "get_instance", lambda: pytest.fail("No application DB initialization"))
    repo = InvestmentMonitorRepository(db_manager=TemporaryDatabase())

    def add(item, raw=None, age=0):
        with Session(engine) as session:
            row = MonitoringEventRecord(
                source_key=item["source_key"], source_name="测试来源", source_type="test",
                external_id=str(item.get("external_id") or item["id"]), event_type=item["event_type"],
                perspective="institution", title=item["title"], summary=item.get("summary"),
                symbol_codes=",".join(item.get("symbols") or []),
                metrics_json=json.dumps(item.get("metrics") or {}, ensure_ascii=False),
                raw_payload=raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                event_at=utc_naive_now() - timedelta(days=age),
            )
            session.add(row)
            session.commit()
            return row.id

    try:
        yield repo, engine, add
    finally:
        engine.dispose()


def test_sql_priority_predicate_and_python_relationship_have_identical_direct_results(repository):
    repo, engine, add = repository
    expected = []
    for index, (item, raw, kind) in enumerate(CASES):
        row_id = add({**item, "id": index + 1}, raw)
        if kind == "company_direct":
            expected.append(row_id)
    with Session(engine) as session:
        direct = list(session.execute(select(MonitoringEventRecord.id).where(
            direct_research_condition(MonitoringEventRecord, TARGET),
        )).scalars())
    assert sorted(direct) == sorted(expected)
    bundle = repo.symbol_workspace_bundle(symbol=TARGET, stock_name="中际旭创", days=183)
    assert all("stock_relation" in item for item in bundle["research"])


def test_overview_finds_older_direct_reports_before_limit_and_full_preserves_wide_order(repository, monkeypatch):
    repo, engine, add = repository
    for index in range(8):
        add(report({"report_type": "行业研报", "ind_name": "光通信"}, id=index + 1), age=index)
    own_id = add(report({"ts_code": TARGET, "name": "中际旭创"}, id=100), age=15)
    same_title_other = add(report({"ts_code": OTHER, "name": "中石科技"}, id=101), age=16)
    raw_id = add(report({}, id=102, event_type="institution_forecast", source_key="tushare.institution"),
                 [{"ts_code": TARGET, "name": "中际旭创", "quarter": "2026"}], age=20)
    add(report({"ts_code": TARGET}, id=103), age=184)  # Outside the requested window.
    statements = []
    sqlalchemy_event.listen(engine, "before_cursor_execute",
                            lambda _c, _cu, sql, _p, _co, _e: statements.append(sql.lower()))
    overview = repo.symbol_workspace_bundle(symbol=TARGET, stock_name="中际旭创", days=183, overview=True)
    assert [item["id"] for item in overview["research"][:2]] == [own_id, raw_id]
    assert [item["stock_relation"]["kind"] for item in overview["research"]] == [
        "company_direct", "company_direct", "industry_related",
    ]
    assert len(statements) == 6
    assert all(not statement.lstrip().startswith(("insert", "update", "delete")) for statement in statements)
    full = repo.symbol_workspace_bundle(symbol=TARGET, stock_name="中际旭创", days=183)
    assert len(full["research"]) == 11
    assert full["research"][-2]["id"] == same_title_other
    assert full["research"][-2]["stock_relation"]["kind"] == "other_company_mention"
    assert full["research"][-1]["id"] == raw_id
    assert all("raw_payload" not in item for item in full["research"])

    monitor = InvestmentMonitorService.__new__(InvestmentMonitorService)
    monitor.repo = repo
    monitor._name_cache = {TARGET: "中际旭创"}
    monkeypatch.setattr("src.services.investment_monitor_service.get_index_stock_name", lambda _: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **_: SimpleNamespace(snapshot=lambda *a, **k: {}))
    for is_overview in (True, False):
        stock = monitor._super_stock(TARGET, days=183, overview=is_overview)
        assert all("stock_relation" in item for item in stock["institution"]["latest"])
        assert all("stock_relation" in item for item in stock["timeline"])
        if is_overview:
            section = next(section for section in stock["overview"]["sections"] if section["key"] == "research")
            assert section["items"][0]["stock_relation"]["kind"] == "company_direct"


def test_consensus_excludes_other_subjects_and_conflicts_without_counting_them_as_duplicates():
    def forecast(identifier, np, relation=None, identity=None):
        item = report({"forecasts": [{"quarter": "2027", "np": np}], "org_name": "测试证券",
                       "report_identity": identity or str(identifier)}, id=identifier,
                      event_type="institution_forecast", source_key="tushare.institution")
        if relation is not None:
            item["stock_relation"] = relation
        return item

    direct = {"kind": "company_direct", "target_symbol": TARGET, "subject_symbol": TARGET}
    other = {"kind": "other_company_mention", "target_symbol": TARGET, "subject_symbol": OTHER}
    conflict = {"kind": "unknown", "target_symbol": TARGET, "has_subject_conflict": True}
    items = [forecast(1, 10, direct, "same"), forecast(2, 10, direct, "same"),
             forecast(3, 999, other), forecast(4, 888, conflict), forecast(5, 20)]
    items[0]["symbols"] = [TARGET, OTHER]  # Old broad recall must not create a second copy of the same report.
    result = InvestmentMonitorService._consensus_payload(
        items, [], target_symbol=TARGET, broker_total_record_count=5, broker_sample_limit=200,
    )
    sample = result["broker_sample"]
    assert sample["loaded_record_count"] == 5
    assert sample["relation_excluded_count"] == 2
    assert sample["input_record_count"] == 3
    assert sample["duplicate_record_count"] == 1
    assert sample["report_count"] == 2
    assert sample["unknown_relation_count"] == 1
    assert sample["total_record_count"] == 5 and sample["truncated"] is False
    assert result["forecasts"][0]["np_median"] == 15
    assert result["forecasts"][0]["report_count"] == 2
    assert {row["event_id"] for row in sample["source_notes"]} == {2, 5}


def test_full_workspace_retains_overview_direct_report_after_200_newer_related_reports(repository, monkeypatch):
    repo, _engine, add = repository
    for index in range(201):
        add(report({"ts_code": OTHER, "name": "中石科技"}, id=index), age=index / 100)
    direct_id = add(report({"ts_code": TARGET, "name": "中际旭创"}, id=300), age=30)
    overview = repo.symbol_workspace_bundle(symbol=TARGET, stock_name="中际旭创", days=183, overview=True)
    full = repo.symbol_workspace_bundle(symbol=TARGET, stock_name="中际旭创", days=183)
    assert overview["research"][0]["id"] == direct_id
    assert len(full["research"]) == 201
    assert full["research"][-1]["id"] == direct_id
    assert full["research"][-1]["stock_relation"] == overview["research"][0]["stock_relation"]
    monitor = InvestmentMonitorService.__new__(InvestmentMonitorService)
    monitor.repo = repo
    monitor._name_cache = {TARGET: "中际旭创"}
    monkeypatch.setattr("src.services.investment_monitor_service.get_index_stock_name", lambda _: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **_: SimpleNamespace(snapshot=lambda *a, **k: {}))
    stock = monitor._super_stock(TARGET, days=183, overview=False)
    assert len(stock["institution"]["latest"]) == 201
    assert stock["institution"]["latest"][-1]["id"] == direct_id
    assert stock["institution"]["research_count"] == 202  # Census is not the bounded sample.


@pytest.mark.parametrize("raw", ["malformed-json", {"ts_code": "broken-code", "name": "错误名称"}])
def test_bad_original_cannot_launder_explicit_other_company_numbers(repository, raw):
    repo, _engine, add = repository
    item = report({"ts_code": OTHER, "name": "中石科技", "forecasts": [{"quarter": "2027", "np": 999}]},
                  event_type="institution_forecast", source_key="tushare.institution")
    add(item, raw)
    rows = repo.recent_symbol_events(symbol=TARGET, days=183, event_types=("institution_forecast",), limit=200)
    assert rows[0]["stock_relation"]["kind"] == "other_company_mention"
    assert rows[0]["stock_relation"]["subject_name"] == "中石科技"
    result = InvestmentMonitorService._consensus_payload(rows, [], target_symbol=TARGET)
    assert result["forecasts"] == []
    assert result["broker_sample"]["relation_excluded_count"] == 1
    assert result["broker_sample"]["unknown_relation_count"] == 0


def test_forecast_metrics_explicit_other_company_is_excluded_even_without_relation_metadata():
    item = report({"ts_code": OTHER, "forecasts": [{"quarter": "2027", "np": 999}]},
                  event_type="institution_forecast", source_key="tushare.institution")
    result = InvestmentMonitorService._consensus_payload([item], [], target_symbol=TARGET)
    assert result["forecasts"] == []
    assert result["broker_sample"]["relation_excluded_count"] == 1


def test_consensus_uses_revalidated_relation_for_deduplication_and_source_notes():
    original_relation = {"kind": "company_direct", "target_symbol": OTHER, "subject_symbol": OTHER}
    first = report({"ts_code": TARGET, "report_identity": "same-original-report",
                    "forecasts": [{"quarter": "2027", "np": 10}]}, id=1,
                   event_type="institution_forecast", source_key="tushare.institution",
                   stock_relation=original_relation, symbols=[TARGET, OTHER])
    second = report({"ts_code": TARGET, "report_identity": "same-original-report",
                     "forecasts": [{"quarter": "2027", "np": 20}]}, id=2,
                    event_type="institution_forecast", source_key="tushare.institution")
    result = InvestmentMonitorService._consensus_payload([first, second], [], target_symbol=TARGET)
    sample = result["broker_sample"]
    assert sample["input_record_count"] == 2
    assert sample["duplicate_record_count"] == 1
    assert sample["report_count"] == 1 and sample["unknown_relation_count"] == 0
    assert result["forecasts"][0]["np_median"] == 20
    assert sample["source_notes"][0]["stock_relation"]["subject_symbol"] == TARGET
    assert sample["source_notes"][0]["stock_relation"]["target_symbol"] == TARGET
    # Aggregation must not mutate cached rows reused by another stock workspace.
    assert first["stock_relation"] is original_relation
    assert first["stock_relation"]["target_symbol"] == OTHER
    assert "stock_relation" not in second
