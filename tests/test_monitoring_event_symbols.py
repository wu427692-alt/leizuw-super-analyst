# -*- coding: utf-8 -*-
"""Indexed monitoring-event symbol projection: writes, backfill and parity.

The legacy symbol filter matched ``LIKE '%token%'`` against a comma-concatenated
``symbol_codes`` expression, which cannot use an index. These tests pin the two
properties that replaced it: the association table is always consistent with the
event row, and the indexed predicate returns exactly the same event ids as the
old whole-token LIKE predicate.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import delete, false, func, literal, select, update
from sqlalchemy.dialects import sqlite as sqlite_dialect

from src.config import Config
from src.repositories.investment_monitor_repo import (
    InvestmentMonitorRepository,
    _event_symbol_condition,
)
from src.storage import (
    DatabaseManager,
    DatabaseSchemaMigration,
    MONITOR_EVENT_SYMBOL_MIGRATION,
    MonitoringEventRecord,
    MonitoringEventSymbolRecord,
    event_symbol_tokens,
    install_monitoring_event_symbol_projection,
    utc_naive_now,
)


def _event(external_id: str, symbols, *, event_type: str = "news", at=None):
    return {
        "source_key": "test.source",
        "source_name": "测试源",
        "source_type": "news",
        "external_id": external_id,
        "event_type": event_type,
        "perspective": "company",
        "title": f"事件 {external_id}",
        "symbols": list(symbols),
        "event_at": at or utc_naive_now(),
    }


def _legacy_like_condition(symbol: str):
    """Verbatim copy of the predicate this migration replaced."""
    value = str(symbol or "").strip()
    if not value or "," in value:
        return false()
    tokens = literal(",") + func.coalesce(MonitoringEventRecord.symbol_codes, "") + literal(",")
    return tokens.contains(f",{value},", autoescape=True)


def _insert_legacy_event(session, external_id: str, symbol_codes: str, event_at) -> None:
    """Insert a row the way pre-migration code did, bypassing the projection."""
    session.add(MonitoringEventRecord(
        source_key="legacy.source", source_name="legacy", source_type="news",
        external_id=external_id, event_type="news", perspective="company",
        title=external_id, symbol_codes=symbol_codes, sentiment="neutral",
        importance_score=50, confidence_score=0.5,
        tags_json="[]", actors_json="[]", metrics_json="{}", raw_payload="{}",
        event_at=event_at, ingested_at=event_at,
    ))


def _projection_rows(repository: InvestmentMonitorRepository):
    with repository.db.get_session() as session:
        return sorted(
            (int(event_id), str(symbol))
            for event_id, symbol in session.execute(
                select(MonitoringEventSymbolRecord.event_id, MonitoringEventSymbolRecord.symbol)
            ).all()
        )


def _rerun_backfill(repository: InvestmentMonitorRepository) -> None:
    """Replay the one-off migration the way a restart after legacy writes would."""
    with repository.db.get_session() as session:
        session.execute(delete(MonitoringEventSymbolRecord))
        session.execute(delete(DatabaseSchemaMigration).where(
            DatabaseSchemaMigration.version == MONITOR_EVENT_SYMBOL_MIGRATION
        ))
        session.commit()
    repository.db._ensure_monitoring_event_symbol_index()


def _event_id(repository: InvestmentMonitorRepository, external_id: str) -> int:
    with repository.db.get_session() as session:
        return int(session.execute(
            select(MonitoringEventRecord.id).where(MonitoringEventRecord.external_id == external_id)
        ).scalar_one())


@pytest.fixture()
def repository(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "symbols.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    repo = InvestmentMonitorRepository(DatabaseManager.get_instance())
    try:
        yield repo
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def test_event_symbol_tokens_keeps_legacy_csv_bytes() -> None:
    assert event_symbol_tokens("600519.SH,000001.SZ") == ["000001.SZ", "600519.SH"]
    assert event_symbol_tokens("600519.SH,600519.SH") == ["600519.SH"]
    assert event_symbol_tokens(None) == []
    assert event_symbol_tokens("") == []
    assert event_symbol_tokens(",,") == []
    # The legacy LIKE compared raw CSV bytes: a padded token never matched, so
    # it must stay a distinct token instead of silently becoming matchable.
    assert event_symbol_tokens("000001.SZ, 600519.SH") == [" 600519.SH", "000001.SZ"]


def test_upsert_events_projects_symbols_into_index(repository) -> None:
    repository.upsert_events([_event("a1", ["600519.SH", "000001.SZ"])])
    event_id = _event_id(repository, "a1")
    assert _projection_rows(repository) == [(event_id, "000001.SZ"), (event_id, "600519.SH")]


def test_upsert_rewrites_projection_when_symbol_list_changes(repository) -> None:
    repository.upsert_events([_event("b1", ["600519.SH", "000001.SZ"])])
    repository.upsert_events([_event("b1", ["600519.SH"])])
    event_id = _event_id(repository, "b1")
    assert _projection_rows(repository) == [(event_id, "600519.SH")]


def test_upsert_clears_projection_when_symbols_removed(repository) -> None:
    repository.upsert_events([_event("c1", ["600519.SH"])])
    repository.upsert_events([_event("c1", [])])
    assert _projection_rows(repository) == []


def test_backfill_projects_legacy_rows_and_is_idempotent(repository) -> None:
    now = utc_naive_now()
    with repository.db.get_session() as session:
        _insert_legacy_event(session, "legacy-1", "600519.SH,000001.SZ", now)
        _insert_legacy_event(session, "legacy-2", "300308.SZ", now)
        _insert_legacy_event(session, "legacy-3", "", now)
        session.commit()
    _rerun_backfill(repository)
    first = _projection_rows(repository)
    assert first == sorted([
        (_event_id(repository, "legacy-1"), "000001.SZ"),
        (_event_id(repository, "legacy-1"), "600519.SH"),
        (_event_id(repository, "legacy-2"), "300308.SZ"),
    ])

    # Re-running the migration (e.g. after an interrupted start) must not
    # duplicate projection rows, and the marker must be recorded.
    repository.db._ensure_monitoring_event_symbol_index()
    assert _projection_rows(repository) == first
    with repository.db.get_session() as session:
        assert session.get(DatabaseSchemaMigration, MONITOR_EVENT_SYMBOL_MIGRATION) is not None


def test_indexed_condition_matches_only_whole_csv_tokens(repository) -> None:
    now = utc_naive_now()
    cases = {
        "exact": "600519.SH",
        "among-many": "000001.SZ,600519.SH,300308.SZ",
        "suffix-only": "X600519.SH",
        "prefix-only": "600519.SHX",
        "empty": "",
    }
    with repository.db.get_session() as session:
        for name, codes in cases.items():
            _insert_legacy_event(session, name, codes, now)
        session.commit()
    _rerun_backfill(repository)

    with repository.db.get_session() as session:
        matched = {
            str(row) for row in session.execute(
                select(MonitoringEventRecord.external_id).where(_event_symbol_condition("600519.SH"))
            ).scalars().all()
        }
    assert matched == {"exact", "among-many"}


def test_indexed_condition_matches_legacy_like_predicate(repository) -> None:
    now = utc_naive_now()
    legacy_rows = {
        "single": "600519.SH",
        "two": "000001.SZ,600519.SH",
        "padded": "000001.SZ, 600519.SH",
        "prefix": "X600519.SH",
        "suffix": "600519.SHX",
        "exchange-swap": "600519.SZ",
        "trailing-comma": "600519.SH,",
        "leading-comma": ",600519.SH",
        "empty-tokens": "600519.SH,,000001.SZ",
        "empty": "",
        "none": None,
        "bare-code": "600519",
    }
    with repository.db.get_session() as session:
        for name, codes in legacy_rows.items():
            if codes is None:
                continue
            _insert_legacy_event(session, name, codes, now)
        session.commit()
    _rerun_backfill(repository)

    probes = [
        "600519.SH", "000001.SZ", "600519", "600519.SZ", " 600519.SH",
        "600519.SHX", "", "600519.SH,000001.SZ",
    ]
    with repository.db.get_session() as session:
        for symbol in probes:
            legacy_ids = set(session.execute(
                select(MonitoringEventRecord.id).where(_legacy_like_condition(symbol))
            ).scalars().all())
            indexed_ids = set(session.execute(
                select(MonitoringEventRecord.id).where(_event_symbol_condition(symbol))
            ).scalars().all())
            assert indexed_ids == legacy_ids, f"symbol={symbol!r} diverged from legacy LIKE"


def test_symbol_filter_query_plan_is_an_index_probe(repository) -> None:
    cutoff = utc_naive_now() - timedelta(days=7)
    query = select(MonitoringEventRecord.id).where(
        MonitoringEventRecord.event_at >= cutoff,
        _event_symbol_condition("600519.SH"),
        MonitoringEventRecord.event_type != "realtime_quote",
    ).order_by(MonitoringEventRecord.event_at.desc())
    compiled = query.compile(
        dialect=sqlite_dialect.dialect(), compile_kwargs={"literal_binds": True},
    )
    with repository.db._engine.connect() as connection:
        plan = [str(row[3]) for row in connection.exec_driver_sql(
            f"EXPLAIN QUERY PLAN {compiled}"
        ).fetchall()]
    details = " | ".join(plan)
    # The symbol test must be a key probe on the projection, and the outer scan
    # must stay on the selective event_at index — never a full table scan.
    assert any(
        "SEARCH monitoring_event_symbols USING PRIMARY KEY" in step
        or "SEARCH monitoring_event_symbols USING COVERING INDEX" in step
        for step in plan
    ), details
    assert "ix_monitoring_events_event_at" in details, details
    assert not any(step.startswith("SCAN monitoring_events") for step in plan), details


def test_projection_triggers_are_installed_idempotently(repository) -> None:
    # DatabaseManager installed them at init; a second install (another process
    # starting against the same database) must be a harmless no-op.
    install_monitoring_event_symbol_projection(repository.db._engine)
    with repository.db._engine.connect() as connection:
        triggers = {
            str(row[0]) for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'monitoring_event_symbols_%'"
            ).fetchall()
        }
    assert triggers == {
        "monitoring_event_symbols_ai",
        "monitoring_event_symbols_au",
        "monitoring_event_symbols_ad",
    }


def test_projection_follows_core_updates_and_deletes(repository) -> None:
    """Maintenance triggers keep the projection honest for bulk SQL too."""
    repository.upsert_events([_event("f1", ["600519.SH"])])
    event_id = _event_id(repository, "f1")
    with repository.db.get_session() as session:
        session.execute(
            update(MonitoringEventRecord)
            .where(MonitoringEventRecord.id == event_id)
            .values(symbol_codes="000001.SZ,300308.SZ")
        )
        session.commit()
    assert _projection_rows(repository) == [(event_id, "000001.SZ"), (event_id, "300308.SZ")]

    with repository.db.get_session() as session:
        session.execute(delete(MonitoringEventRecord).where(MonitoringEventRecord.id == event_id))
        session.commit()
    assert _projection_rows(repository) == []


def test_reindex_keyword_symbols_refreshes_projection(repository) -> None:
    repository.upsert_events([_event("d1", [], event_type="essay")])
    with repository.db.get_session() as session:
        session.execute(
            MonitoringEventRecord.__table__.update()
            .where(MonitoringEventRecord.external_id == "d1")
            .values(title="贵州茅台渠道跟踪", summary="茅台渠道稳定")
        )
        session.commit()
    result = repository.reindex_keyword_symbols(
        aliases_by_symbol={"600519.SH": ["贵州茅台"]}, days=30, event_types=("essay",),
    )
    assert result["updated"] == 1
    event_id = _event_id(repository, "d1")
    assert _projection_rows(repository) == [(event_id, "600519.SH")]

    with repository.db.get_session() as session:
        matched = session.execute(
            select(MonitoringEventRecord.id).where(_event_symbol_condition("600519.SH"))
        ).scalars().all()
    assert list(matched) == [event_id]
