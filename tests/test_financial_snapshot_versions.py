"""Offline financial content-version persistence and per-table provenance."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
import json
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event, select
from sqlalchemy.orm import sessionmaker

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.storage import MonitoringEventRecord, utc_naive_now
from src.utils.financial_statement_selection import (
    financial_announcement_date, financial_calendar_date, financial_selection_rank,
)


@pytest.fixture()
def repository(tmp_path):
    # Create only the table under test; never construct the configured global DB.
    engine = create_engine(f"sqlite:///{tmp_path / 'financial-versions.db'}", connect_args={"timeout": 10})
    MonitoringEventRecord.__table__.create(engine)
    repo = InvestmentMonitorRepository(SimpleNamespace(get_session=sessionmaker(bind=engine)))
    yield repo, engine
    engine.dispose()


def financial_event(**sections):
    return {
        "source_key": "fixture.financial", "source_name": "Offline fixture", "source_type": "api",
        "external_id": "600519.SH:20260630", "event_type": "fundamental_snapshot", "perspective": "company",
        "title": "Financial fixture", "summary": "Derived text", "symbols": ["600519.SH"],
        "metrics": sections, "raw_payload": deepcopy(sections), "event_at": datetime(2026, 8, 20),
    }


def stored(repository):
    repo, _ = repository
    with repo.db.get_session() as session:
        return list(session.execute(select(MonitoringEventRecord).order_by(MonitoringEventRecord.id)).scalars())


def sections(repository, *, overview=True, symbol="600519.SH", days=3650):
    return repository[0].symbol_workspace_bundle(symbol=symbol, days=days, overview=overview)["financial_sections"]


def test_content_version_ignores_derived_fields_but_retains_original_record(repository):
    repo, _ = repository
    event = financial_event(income={"end_date": "20260630", "ann_date": "20260820", "revenue": 100})
    assert repo.upsert_events([event]) == {"created": 1, "updated": 0, "received": 1}
    original = stored(repository)[0]
    before = {column.name: getattr(original, column.name) for column in MonitoringEventRecord.__table__.columns}
    duplicate = {**event, "summary": "Changed generated summary", "event_at": datetime(2026, 9, 1),
                 "ingested_at": datetime(2026, 9, 2), "external_id": "a different adapter identity",
                 "metrics": {"period": "20991231", "income": dict(reversed(list(event["metrics"]["income"].items())))}}
    assert repo.upsert_events([duplicate]) == {"created": 0, "updated": 0, "received": 1}
    rows = stored(repository)
    assert len(rows) == 1
    assert {column.name: getattr(rows[0], column.name) for column in MonitoringEventRecord.__table__.columns} == before


@pytest.mark.parametrize("reverse", [False, True])
def test_old_only_response_never_overwrites_new_revision(repository, reverse):
    repo, _ = repository
    old = financial_event(income={"end_date": "20260630", "ann_date": "20260820", "revenue": 100})
    new = financial_event(income={"end_date": "20260630", "ann_date": "20260825", "update_flag": "1", "revenue": 120})
    for event in ([old, new] if reverse else [new, old]):
        assert repo.upsert_events([event])["created"] == 1
    assert len(stored(repository)) == 2
    for overview in (False, True):
        income = sections(repository, overview=overview)["income"]
        assert income["selection_status"] == "selected"
        assert income["row"] == new["metrics"]["income"]
        assert income["event"]["metrics"]["income"] == income["row"]


def test_missing_table_keeps_whole_older_table_with_its_real_source(repository):
    repo, _ = repository
    old = financial_event(income={"end_date": "20260331", "revenue": 100},
                          cashflow={"end_date": "20260630", "n_cashflow_act": 0})
    new = financial_event(income={"end_date": "20260630", "revenue": 200}, cashflow={})
    repo.upsert_events([old])
    old_id = stored(repository)[0].id
    repo.upsert_events([new])
    picked = sections(repository)
    assert picked["income"]["row"]["revenue"] == 200
    assert picked["cashflow"]["row"]["n_cashflow_act"] == 0
    assert picked["cashflow"]["event"]["id"] == old_id
    assert picked["income"]["event"]["id"] != old_id


def test_new_table_null_fields_do_not_borrow_older_values(repository):
    repo, _ = repository
    repo.upsert_events([financial_event(income={"end_date": "20260331", "revenue": 100, "n_income_attr_p": 10})])
    new = {"end_date": "20260630", "revenue": None, "n_income_attr_p": 0}
    repo.upsert_events([financial_event(income=new)])
    assert sections(repository)["income"]["row"] == new


@pytest.mark.parametrize("different", [False, True])
def test_two_connections_atomically_insert_same_or_distinct_content(repository, different):
    repo, engine = repository
    barrier = Barrier(2)
    with engine.connect() as first, engine.connect() as second:
        assert first.connection is not second.connection

    def insert(value):
        barrier.wait(timeout=5)
        return repo.upsert_events([financial_event(income={"end_date": "20260630", "revenue": value})])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(insert, [100, 101 if different else 100]))
    assert sum(result["created"] for result in results) == (2 if different else 1)
    assert all(result["updated"] == 0 for result in results)
    assert len(stored(repository)) == (2 if different else 1)


def test_legacy_original_id_and_payload_are_not_overwritten(repository):
    repo, _ = repository
    original = financial_event(income={"end_date": "20260630", "revenue": 100})
    with repo.db.get_session() as session:
        legacy = MonitoringEventRecord(**repo._event_fields(original))
        session.add(legacy)
        session.commit()
        old_id, old_raw, old_external = legacy.id, legacy.raw_payload, legacy.external_id
    repo.upsert_events([financial_event(income={"end_date": "20260630", "ann_date": "20260825", "revenue": 200})])
    with repo.db.get_session() as session:
        legacy = session.get(MonitoringEventRecord, old_id)
        assert (legacy.raw_payload, legacy.external_id) == (old_raw, old_external)
        assert json.loads(legacy.metrics_json)["income"]["revenue"] == 100
    assert sections(repository)["income"]["row"]["revenue"] == 200


def test_non_financial_upsert_still_updates_same_original_id(repository):
    repo, _ = repository
    event = {**financial_event(), "event_type": "market_snapshot"}
    repo.upsert_events([event])
    original_id = stored(repository)[0].id
    assert repo.upsert_events([{**event, "summary": "Updated quote", "metrics": {"close": 123}}])["updated"] == 1
    assert [(row.id, row.summary) for row in stored(repository)] == [(original_id, "Updated quote")]


@pytest.mark.parametrize("reverse", [False, True])
def test_no_revision_evidence_is_conflict_not_arrival_or_completeness_guess(repository, reverse):
    repo, _ = repository
    events = [financial_event(income={"end_date": "20260630", "ann_date": "20260820", "revenue": 100}),
              financial_event(income={"end_date": "20260630", "ann_date": "20260820", "revenue": 200, "n_income": 20})]
    for event in (list(reversed(events)) if reverse else events):
        repo.upsert_events([event])
    result = sections(repository)["income"]
    assert result["selection_status"] == "conflict"
    assert result["row"] == {} and result["event"] is None
    assert result["period"] == "20260630" and result["announcement"] == "20260820"
    assert result["candidate_count"] == 2
    assert set(result["candidate_event_ids"]) == {row.id for row in stored(repository)}


def test_identical_table_in_different_snapshots_is_not_a_conflict(repository):
    repo, _ = repository
    income = {"end_date": "20260630", "revenue": 100, "nested": {"a": 1, "b": [2, 3]}}
    repo.upsert_events([financial_event(income=income)])
    original_id = stored(repository)[0].id
    equivalent = {"nested": {"b": [2, 3], "a": 1}, "revenue": 100, "end_date": "20260630"}
    repo.upsert_events([financial_event(income=equivalent, indicator={"end_date": "20260630", "roe": 10})])
    picked = sections(repository)["income"]
    assert picked["selection_status"] == "selected"
    assert picked["candidate_count"] == 1
    assert picked["event"]["id"] == original_id


@pytest.mark.parametrize("values", [(1.0, 1.0000000000000002), (123456789012345.0, 123456789012345.1),
                                   (100000000000000000000, 100000000000000000001)])
def test_canonical_content_does_not_round_distinct_source_numbers(repository, values):
    repo, _ = repository
    repo.upsert_events([financial_event(income={"end_date": "20260630", "revenue": value}) for value in values])
    assert len(stored(repository)) == 2
    picked = sections(repository)["income"]
    assert picked["selection_status"] == "conflict"
    assert picked["candidate_count"] == 2


def test_duplicate_content_cannot_crowd_out_conflict_candidate(repository):
    repo, _ = repository
    repo.upsert_events([financial_event(income={"end_date": "20260630", "revenue": 100},
                                       forecast={"end_date": "20261231", "net_profit_min": value}) for value in range(8)])
    repo.upsert_events([financial_event(income={"end_date": "20260630", "revenue": 200})])
    picked = sections(repository)["income"]
    assert picked["selection_status"] == "conflict"
    assert picked["candidate_count"] == 2
    assert len(picked["candidate_event_ids"]) == 2


def test_content_identity_keeps_source_symbol_and_all_five_tables_distinct(repository):
    repo, _ = repository
    event = financial_event(income={"end_date": "20260630", "revenue": 100})
    variants = [event, {**event, "source_key": "other.financial"}, {**event, "symbols": ["600519.SZ"]}]
    for key in ("indicator", "balance_sheet", "cashflow", "forecast"):
        variants.append(financial_event(**{**event["metrics"], key: {"end_date": "20260630", "value": 0}}))
    assert repo.upsert_events(variants)["created"] == 7
    assert len({row.external_id for row in stored(repository)}) == 7


def test_indicator_boolean_report_type_cannot_gain_numeric_type_priority(repository):
    repo, _ = repository
    repo.upsert_events([financial_event(indicator={"end_date": "20260630", "report_type": True, "roe": 10}),
                       financial_event(indicator={"end_date": "20260630", "roe": 20})])
    picked = sections(repository)["indicator"]
    assert picked["selection_status"] == "conflict"
    assert picked["candidate_count"] == 2


@pytest.mark.parametrize(("actual", "announced", "expected"), [
    ("20260820", "20260830", "20260820"), (20260820, "20260830", "20260820"),
    ("2026-08-20", "20260830", "20260820"), ("\t20260820\n", "20260830", "20260820"),
    (None, "20260830", "20260830"), ("", "20260830", "20260830"),
    (True, "20260830", "20260830"), ("20260230", "20260830", "20260830"),
    ("invalid", "20260830", "20260830"), ("20260820", None, "20260820"),
    (None, "20260230", None),
    ("２０２６0820", "20260830", "20260830"), ("٢٠٢٦0820", "20260830", "20260830"),
])
def test_actual_announcement_date_fallback_matches_sql(repository, actual, announced, expected):
    row = {"end_date": "20260630", "f_ann_date": actual, "ann_date": announced, "revenue": 0}
    assert financial_announcement_date(row) == expected
    assert financial_selection_rank(row, statement=True)[-1] == (expected or "")
    repository[0].upsert_events([financial_event(income=row)])
    picked = sections(repository)["income"]
    assert picked["row"] == row
    assert picked["announcement"] == (expected or "")


@pytest.mark.parametrize("period", ["２０２６0630", "٢٠٢٦0630", "２０２６-06-30", "٢٠٢٦-06-30"])
def test_non_ascii_calendar_year_is_ineligible_in_both_collector_rank_and_sql(repository, period):
    row = {"end_date": period, "ann_date": "20260820", "revenue": 900}
    assert financial_calendar_date(period) is None
    assert financial_selection_rank(row, statement=True) is None
    repository[0].upsert_events([financial_event(income=row)])
    assert "income" not in sections(repository)


@pytest.mark.parametrize(("flag", "expected"), [
    ("1", 1), (1, 1), ("\t1\n", 1), ("\u30001\u3000", 1),
    (True, 0), (False, 0), (1.0, 0), ("true", 0), ("y", 0), ("yes", 0),
    ("0", 0), (0, 0), (None, 0), ("", 0), ([], 0), ({}, 0),
])
def test_explicit_latest_marker_policy_matches_sql(repository, flag, expected):
    base = {"end_date": "20260630", "ann_date": "20260820", "report_type": "1", "revenue": 0}
    row = {**base, "update_flag": flag}
    assert financial_selection_rank(row, statement=True) == ("20260630", 2, expected, "20260820")
    repository[0].upsert_events([financial_event(income={**base, "update_flag": "0"}), financial_event(income=row)])
    picked = sections(repository)["income"]
    if expected:
        assert picked["selection_status"] == "selected" and picked["row"] == row
    elif flag == "0":
        assert picked["selection_status"] == "selected" and picked["candidate_count"] == 1
    else:
        assert picked["selection_status"] == "conflict" and picked["candidate_count"] == 2


def test_conflict_candidates_are_bounded_and_forecast_does_not_change_actual_period(repository):
    repo, _ = repository
    repo.upsert_events([financial_event(income={"end_date": "20260630", "revenue": value},
                                       forecast={"end_date": "20261231", "type": "预增"}) for value in range(9)])
    picked = sections(repository)
    assert picked["income"]["candidate_count"] == 9
    assert len(picked["income"]["candidate_event_ids"]) == 5
    assert picked["income"]["period"] == "20260630"
    assert picked["forecast"]["period"] == "20261231"
    assert picked["forecast"]["selection_status"] == "selected"


def test_per_table_selection_retains_scope_dates_and_exact_symbol_day_constraints(repository):
    repo, _ = repository
    valid = financial_event(income={"end_date": "20260630", "report_type": "1", "ann_date": "20260820", "revenue": 100},
                            cashflow={"end_date": "20260331", "n_cashflow_act": -10},
                            balance_sheet={"end_date": "20260630", "report_type": "4", "total_assets": 200})
    repo.upsert_events([valid, financial_event(income={"end_date": "20260930", "report_type": "2", "revenue": 999}),
                       financial_event(income={"end_date": "20260630", "report_type": "5", "ann_date": "20260901", "revenue": 888}),
                       {**financial_event(income={"end_date": "20261231", "revenue": 777}), "symbols": ["600519.SZ"]},
                       {**financial_event(income={"end_date": "20261231", "revenue": 666}),
                        "event_at": utc_naive_now() - timedelta(days=4000)}])
    picked = sections(repository)
    assert picked["income"]["row"] == valid["metrics"]["income"]
    assert picked["cashflow"]["period"] == "20260331"
    assert picked["balance_sheet"]["row"] == valid["metrics"]["balance_sheet"]


def test_overview_per_table_selection_does_not_add_queries_or_load_raw_payload(repository):
    repo, engine = repository
    repo.upsert_events([financial_event(income={"end_date": "20260630", "revenue": 100})])
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)
    sqlalchemy_event.listen(engine, "before_cursor_execute", capture)
    try:
        assert sections(repository)["income"]["row"]["revenue"] == 100
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 6
    # Existing report preview queries may select raw attribution; financial
    # source selection must not add any original-payload load of its own.
    assert "raw_payload" not in statements[0]
    assert all(statement.lstrip().upper().startswith(("SELECT", "WITH")) for statement in statements)
