"""Safety/real-route regressions; the irreversible guard runs only in children."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scripts.readonly_http_acceptance import (
    CAPITAL_DATE_FIELDS, broker_sample_contract, capital_contract, direct_retention_contract, research_contract,
)
from src.storage import Base, MonitoringEventRecord, ResearchNote, utc_naive_now


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "readonly_http_acceptance.py"
TARGET = "300308.SZ"
OTHER = "300684.SZ"
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Native write enforcement uses macOS sandbox-exec")


def run_harness(database, *args):
    # This sentinel must not reach application configuration or output.
    env = {**os.environ, "DEEPSEEK_API_KEY": "readonly-test-secret-must-not-appear"}
    result = subprocess.run(
        [sys.executable, "-B", str(HARNESS), "--database", str(database), *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
    )
    assert "readonly-test-secret-must-not-appear" not in result.stdout + result.stderr
    return result, json.loads(result.stdout)


def add_report(session, identifier, *, metrics=None, raw=None, age=0,
               event_type="research_report_pdf", external_id=None):
    """Create real ORM rows only in the caller's explicitly temporary database."""
    session.add(MonitoringEventRecord(
        id=identifier, source_key="tushare.institution" if event_type == "institution_forecast" else "tushare.research_pdf",
        source_name="验收样例来源", source_type="test", external_id=external_id or f"fixture-{identifier}",
        event_type=event_type, perspective="institution", title="样例标题不应进入验收输出",
        symbol_codes=TARGET, metrics_json=json.dumps(metrics or {}, ensure_ascii=False),
        raw_payload=json.dumps(raw, ensure_ascii=False), event_at=utc_naive_now() - timedelta(days=age),
    ))


def relation(kind="company_direct", **fields):
    return {"kind": kind, "target_symbol": TARGET,
            **({"subject_symbol": TARGET} if kind == "company_direct" else {}), **fields}


def broker_sample():
    return {
        "loaded": True, "total_record_count": 5, "loaded_record_count": 5, "input_record_count": 3,
        "relation_excluded_count": 2, "report_count": 2, "duplicate_record_count": 1,
        "valid_report_count": 2, "unknown_relation_count": 1, "missing_period_report_count": 0,
        "sample_limit": 200, "truncated": False,
        "source_notes": [{"event_id": 2, "stock_relation": relation()},
                         {"event_id": 3, "stock_relation": relation("unknown")}],
    }


def capital_fixture():
    capital = {
        "chip_trade_date": "2026-08-28", "moneyflow_trade_date": "2026-09-10",
        "margin_trade_date": "2026-09-09", "northbound_trade_date": "2024-08-16",
        "moneyflow": {"trade_date": "20260910", "net_mf_amount": 0},
        "margin": {"trade_date": "2026-09-09", "rzye": 0},
        "northbound": {"trade_date": 20240816, "ratio": 0},
    }
    stock = {"capital": capital, "overview": {"data_as_of": {"capital": "2026-08-28"}}}
    context = {"chip_distribution": deepcopy(capital)}
    return stock, context


@pytest.mark.parametrize("view", ["overview", "full"])
def test_capital_contract_checks_independently_dated_sources_and_structured_bindings(view):
    stock, context = capital_fixture()
    result = capital_contract(stock, view, context)
    assert result["passed"] is True
    assert result["date_fields_present"] is True
    assert result["normalized_dates_schema_ok"] is True
    assert all(result["nested_source_date_matches"].values())
    assert result["chip_source_check"] == "raw_chip_trade_date_not_exposed_by_workspace_api"
    assert result["overview_chip_binding_ok"] is (True if view == "overview" else None)
    assert result["agent_context_date_binding_ok"] is (True if view == "full" else None)


@pytest.mark.parametrize("raw", [None, "", 0, True, False, "20260230", "2026-02-30", "not-a-date", "2026-09-10T12:00:00Z"])
def test_capital_contract_requires_unknown_for_invalid_nested_source_dates(raw):
    stock, _ = capital_fixture()
    for name in ("moneyflow", "margin", "northbound"):
        stock["capital"][name]["trade_date"] = raw
        stock["capital"][f"{name}_trade_date"] = None
    assert capital_contract(stock, "overview")["passed"] is True
    stock["capital"]["moneyflow_trade_date"] = "2026-09-10"
    result = capital_contract(stock, "overview")
    assert result["nested_source_date_matches"]["moneyflow"] is False
    assert result["passed"] is False


@pytest.mark.parametrize("raw", ["20240229", "2024-02-29", 20240229])
def test_capital_contract_accepts_valid_compact_and_iso_source_calendar_dates(raw):
    stock, _ = capital_fixture()
    for name in ("moneyflow", "margin", "northbound"):
        stock["capital"][name]["trade_date"] = raw
        stock["capital"][f"{name}_trade_date"] = "2024-02-29"
    assert capital_contract(stock, "overview")["passed"] is True


@pytest.mark.parametrize("invalid", [True, 20260910, "20260910", "2026-02-30", " 2026-09-10 ", "2026-09-10T12:00:00Z"])
def test_capital_contract_rejects_noncanonical_public_date_fields(invalid):
    for key in CAPITAL_DATE_FIELDS:
        stock, _ = capital_fixture()
        stock["capital"][key] = invalid
        result = capital_contract(stock, "overview")
        assert result["normalized_dates_schema_ok"] is False
        assert result["passed"] is False


def test_capital_contract_does_not_borrow_dates_for_missing_chip_or_missing_source():
    stock, _ = capital_fixture()
    stock["capital"]["chip_trade_date"] = None
    stock["overview"]["data_as_of"]["capital"] = None
    assert capital_contract(stock, "overview")["passed"] is True
    stock["overview"]["data_as_of"]["capital"] = stock["capital"]["moneyflow_trade_date"]
    assert capital_contract(stock, "overview")["overview_chip_binding_ok"] is False
    stock["overview"]["data_as_of"]["capital"] = None
    del stock["capital"]["margin"]["trade_date"]
    stock["capital"]["margin_trade_date"] = None
    assert capital_contract(stock, "overview")["passed"] is True
    del stock["capital"]["chip_trade_date"]
    assert capital_contract(stock, "overview")["date_fields_present"] is False


def test_capital_contract_rejects_missing_or_changed_bindings_without_emitting_source_text():
    stock, context = capital_fixture()
    stock["capital"]["moneyflow"]["private_note"] = "原文不应进入资本验收输出"
    context["analysis_context_pack_summary"] = "不解析或输出上下文原文"
    assert capital_contract(stock, "full", context)["passed"] is True
    for key in CAPITAL_DATE_FIELDS:
        changed = deepcopy(context)
        changed["chip_distribution"][key] = None
        assert capital_contract(stock, "full", changed)["agent_context_date_binding_ok"] is False
    assert capital_contract(stock, "full", {})["passed"] is False
    del stock["overview"]["data_as_of"]["capital"]
    result = capital_contract(stock, "overview", context)
    assert result["passed"] is False
    assert "原文" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("name", ["moneyflow", "margin", "northbound"])
def test_capital_contract_rejects_absent_or_malformed_nested_source_objects(name):
    stock, _ = capital_fixture()
    stock["capital"][name] = None
    stock["capital"][f"{name}_trade_date"] = None
    result = capital_contract(stock, "overview")
    assert result["nested_source_date_matches"][name] is False
    assert result["passed"] is False


def test_guard_refuses_writes_private_reads_network_processes_threads_and_unsafe_sql(tmp_path):
    database = tmp_path / "guard.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE retained_fact (id INTEGER)")
        connection.execute("INSERT INTO retained_fact VALUES (7)")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result, report = run_harness(database, "--self-test-only")
    assert result.returncode == 0, report
    assert report["passed"] is True
    assert len(report["guard_self_tests"]) == 11
    assert all(report["guard_self_tests"].values())
    assert report["blocked_operations_during_acceptance"] == {}
    assert report["production_auth_verified"] is False
    assert report["native_write_guard_verified"] is True
    assert not (tmp_path / "readonly-audit-must-not-exist").exists()
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT id FROM retained_fact").fetchall() == [(7,)]


def test_current_get_routes_use_real_repositories_and_preserve_search_scope(tmp_path):
    database = tmp_path / "facts.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)  # Only this explicitly temporary fixture.
    with Session(engine) as session:
        for index, title in enumerate(("验收关键词 标题证据", "其他标题")):
            session.add(ResearchNote(
                topic_id=f"readonly-fixture-{index}", group_id="fixture", group_name="fixture",
                title=title, content="验收关键词 原文内容不应进入报告",
                content_hash=f"fixture-hash-{index}", created_at=datetime(2026, 9, 10),
            ))
        # These are actual stored reports, not mocked HTTP or repository results.
        for identifier, subject, np, identity in (
            (1, TARGET, 9999, None), (2, TARGET, 100, "report-A"), (3, None, 300, "unknown"),
            (4, OTHER, 999, "other"), (5, TARGET, 888, "conflict"),
        ):
            metrics = {"forecasts": [{"quarter": "2027", "np": np}], "org_name": "测试证券"}
            if identity:
                metrics["report_identity"] = identity
            if identifier == 5:
                metrics["ts_code"] = OTHER  # Conflict with the original's current-stock subject.
            raw = {"source_only_text": "原始报告载荷不应进入报告"}
            if subject:
                raw["ts_code"] = subject
            add_report(session, identifier, metrics=metrics, raw=raw, age=6 - identifier,
                       event_type="institution_forecast", external_id="report-A" if identifier == 1 else f"{TARGET}:{identity}")
        add_report(session, 6, metrics={"report_type": "行业研报"})
        add_report(session, 7, metrics={"ts_code": OTHER, "abstr": "中际旭创的行业关联"})
        add_report(session, 8, metrics={"ts_code": TARGET}, event_type="institution_survey")
        add_report(session, 9, event_type="broker_recommendation")
        session.commit()
    engine.dispose()
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result, report = run_harness(database, "--query", "验收关键词", "--symbol", "300308.SZ")
    assert result.returncode == 0, report
    assert report["passed"] is True
    assert report["blocked_operations_during_acceptance"] == {}
    assert all(report["http_boundary_checks"].values())
    assert report["production_auth_verified"] is False
    assert report["network_listener"] is False
    assert report["native_write_guard_verified"] is True
    assert len(report["unverified_task_channels"]) == 5
    rows = report["requests"]
    assert len(rows) == 6
    assert all(row["status"] == 200 and row["schema_ok"] for row in rows)
    feed = rows[:3]
    assert [row["request_params"]["query_scope"] for row in feed] == ["full", "title", "full"]
    assert [row["total"] for row in feed] == [2, 1, 2]
    assert all(row["request_params"]["analysis_status"] == "essay" for row in feed)
    assert all(row["request_params"]["days"] == 0 and row["request_params"]["page"] == 1 for row in feed)
    assert [row["nonempty_original_count"] for row in feed] == [2, 1, 2]
    assert all(report["repeated_full_search"].values())
    assert all(row["sql_reads"] > 0 for row in feed)
    workspaces = rows[3:]
    assert [row["view"] for row in workspaces] == ["overview", "overview", "full"]
    assert workspaces[0]["cache"]["hit"] is False
    assert workspaces[1]["cache"]["hit"] is True
    assert all(row["evidence_schema_ok"] and row["broker_sample_schema_ok"] for row in workspaces)
    assert all(row["capital_contract"]["passed"] for row in workspaces)
    assert [row["evidence_counts_complete"] for row in workspaces] == [False, False, True]
    assert all(row["research_contract"]["passed"] and row["broker_contract"]["passed"] for row in workspaces)
    assert workspaces[0]["broker_sample"]["loaded"] is False
    full = workspaces[-1]
    assert full["research_contract"]["unique_events"] == 9
    assert full["research_contract"]["relation_counts"] == {
        "company_direct": 3, "industry_related": 1, "other_company_mention": 2, "unknown": 3,
    }
    for key, expected in {"total_record_count": 5, "loaded_record_count": 5, "input_record_count": 3,
                          "relation_excluded_count": 2, "report_count": 2, "duplicate_record_count": 1,
                          "valid_report_count": 2, "unknown_relation_count": 1, "missing_period_report_count": 0}.items():
        assert full["broker_sample"][key] == expected
    assert full["forecast_np_numeric_count"] == 1
    assert full["broker_contract"]["source_notes_count"] == 2
    assert full["overview_direct_retention"] == {
        "overview_direct_count": 3, "retained_count": 3, "missing_count": 0, "passed": True,
    }
    assert "原文内容不应进入报告" not in result.stdout
    assert "原始报告载荷不应进入报告" not in result.stdout
    assert "样例标题不应进入验收输出" not in result.stdout
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_real_http_full_keeps_older_overview_direct_reports_after_200_newer_related_rows(tmp_path):
    database = tmp_path / "retention.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index in range(201):
            add_report(session, index + 1, metrics={"report_type": "行业研报"}, age=index / 100)
        for index in range(3):
            add_report(session, 300 + index, raw={"ts_code": TARGET}, age=30 + index)
        session.commit()
    engine.dispose()
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result, report = run_harness(database, "--symbol", TARGET)
    assert result.returncode == 0, report
    assert report["passed"] and report["native_write_guard_verified"]
    assert all(report["guard_self_tests"].values()) and report["blocked_operations_during_acceptance"] == {}
    overview, cached, full = report["requests"][3:]
    assert overview["preview_counts"]["research"] == 3
    assert cached["cache"]["hit"] is True
    assert full["research_contract"]["unique_events"] == 203  # Bounded 200 plus all three older direct reports.
    assert full["research_contract"]["relation_counts"]["company_direct"] == 3
    assert full["event_count"] == 204  # The full census is not the bounded report list.
    assert full["overview_direct_retention"] == {
        "overview_direct_count": 3, "retained_count": 3, "missing_count": 0, "passed": True,
    }
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("kind,fields", [
    ("company_direct", {}), ("industry_related", {}),
    ("other_company_mention", {"subject_symbol": OTHER}), ("unknown", {"has_subject_conflict": True}),
])
def test_research_contract_accepts_actual_relation_enum_without_hiding_unknown_or_other(kind, fields):
    row = {"id": 1, "event_type": "research_report_pdf", "metrics": {"_evidence": {}},
           "stock_relation": relation(kind, **fields)}
    result = research_contract({"symbol": TARGET, "institution": {"latest": [row]}, "timeline": [row]})
    assert result["passed"]
    assert result["unique_events"] == 1 and result["event_occurrences"] == 2
    assert result["relation_counts"][kind] == 1


@pytest.mark.parametrize("patch", [
    {"stock_relation": relation("guess")},
    {"stock_relation": relation(target_symbol=OTHER)},
    {"stock_relation": relation(subject_symbol=OTHER)},
    {"stock_relation": relation("other_company_mention")},
    {"stock_relation": relation("other_company_mention", subject_symbol=TARGET)},
    {"stock_relation": relation("unknown", has_subject_conflict="true")},
    {"stock_relation": relation(raw_payload="secret")},
    {"raw_payload": "secret"}, {"metrics": {"records": [{"ts_code": TARGET}]}},
])
def test_research_contract_rejects_wrong_relation_and_noncompact_originals(patch):
    row = {"id": 1, "event_type": "institution_forecast", "metrics": {}, "stock_relation": relation(), **patch}
    assert not research_contract({"symbol": TARGET, "institution": {"latest": [row]}})["passed"]


def test_broker_contract_accepts_unknown_but_requires_exact_counts_and_current_stock_notes():
    sample = broker_sample()
    assert broker_sample_contract(sample, TARGET, "full")["passed"]
    sample["source_notes"][1]["stock_relation"] = relation("industry_related")
    assert broker_sample_contract(sample, TARGET, "full")["passed"]
    sample["total_record_count"] = 201
    sample["truncated"] = True
    assert broker_sample_contract(sample, TARGET, "full")["passed"]


@pytest.mark.parametrize("key,value", [
    ("loaded_record_count", 6), ("input_record_count", 4), ("relation_excluded_count", 1),
    ("duplicate_record_count", 0), ("report_count", 3), ("valid_report_count", 3),
    ("unknown_relation_count", 0), ("missing_period_report_count", 3), ("sample_limit", 4),
    ("total_record_count", 4), ("truncated", True), ("loaded", False),
])
def test_broker_contract_rejects_invalid_count_or_coverage_state(key, value):
    sample = broker_sample()
    sample[key] = value
    assert not broker_sample_contract(sample, TARGET, "full")["passed"]


@pytest.mark.parametrize("replacement", [
    {"event_id": 2, "stock_relation": relation("unknown")},  # Duplicate source citation.
    {"event_id": 3, "stock_relation": relation("other_company_mention", subject_symbol=OTHER)},
    {"event_id": 3, "stock_relation": relation("unknown", has_subject_conflict=True)},
    {"event_id": 3, "stock_relation": relation("unknown", target_symbol=OTHER)},
    {"event_id": 3, "stock_relation": relation("unknown"), "raw_payload": "secret"},
])
def test_broker_contract_rejects_other_conflicting_raw_or_duplicate_source_notes(replacement):
    sample = deepcopy(broker_sample())
    sample["source_notes"][1] = replacement
    assert not broker_sample_contract(sample, TARGET, "full")["passed"]


def test_retention_contract_requires_event_id_subset_not_just_equal_list_sizes():
    assert direct_retention_contract({1, 2}, {"institution": {"latest": [{"id": 1}, {"id": 3}]}}) == {
        "overview_direct_count": 2, "retained_count": 1, "missing_count": 1, "passed": False,
    }


@pytest.mark.parametrize("file_readonly", [False, True])
def test_native_guard_cannot_be_claimed_by_passing_child_flag(tmp_path, file_readonly):
    database = tmp_path / "no-sandbox.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE retained_fact (id INTEGER)")
    if file_readonly:
        database.chmod(0o444)  # Only the temporary fixture, never a real DB.
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result, report = run_harness(database, "--native-readonly-child", "--self-test-only")
    assert result.returncode == 1
    assert report["native_write_guard_verified"] is False
    assert report["blocked_operations_during_acceptance"] == {"native_sandbox_policy_missing": 1}
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_native_sandbox_preserves_existing_live_wal_and_shared_memory(tmp_path):
    database = tmp_path / "live-wal.sqlite"
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE retained_fact (id INTEGER)")
        writer.execute("INSERT INTO retained_fact VALUES (7)")
        writer.commit()
        paths = [database, Path(str(database) + "-wal"), Path(str(database) + "-shm")]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        result, report = run_harness(database, "--self-test-only")
        assert result.returncode == 0, report
        assert report["native_write_guard_verified"] is True
        assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths} == before
    finally:
        writer.close()


def test_missing_schema_is_reported_without_creating_or_migrating_tables(tmp_path):
    database = tmp_path / "old.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE retained_fact (id INTEGER)")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result, report = run_harness(database)
    assert result.returncode == 1
    assert report["passed"] is False
    assert any(not row["schema_ok"] for row in report["requests"])
    assert "research_notes" not in result.stdout  # Never print raw SQL errors.
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
