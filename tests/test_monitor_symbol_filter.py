"""Whole-security event filters against disposable SQLite, never the app DB."""

from contextlib import contextmanager
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.investment_monitor_service import InvestmentMonitorService
from src.storage import (
    DatabaseManager, MonitoringEventRecord, MonitoringSourceRecord, ResearchNote,
    StockDaily, utc_naive_now,
)


@pytest.fixture()
def symbol_repository(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'symbol-filter.db'}")
    for model in (MonitoringEventRecord, MonitoringSourceRecord, ResearchNote, StockDaily):
        model.__table__.create(engine)

    class TemporaryDatabase:
        @contextmanager
        def get_session(self):
            with Session(engine, autoflush=False) as session:
                yield session

    monkeypatch.setattr(DatabaseManager, "get_instance", lambda: pytest.fail("No app DB initialization"))
    repo = InvestmentMonitorRepository(TemporaryDatabase())
    sequence = 0

    def add(symbols, *, event_type="company_announcement", metrics=None):
        nonlocal sequence
        sequence += 1
        topic_id = f"symbol-fixture-{sequence}"
        with Session(engine) as session:
            row = MonitoringEventRecord(
                source_key="cninfo.announcements", source_name="测试来源", source_type="test",
                external_id=topic_id, event_type=event_type, perspective="company",
                title="测试证券事件", symbol_codes=",".join(symbols),
                metrics_json=json.dumps(metrics or {}), raw_payload="{}",
                event_at=utc_naive_now() - timedelta(hours=1) + timedelta(seconds=sequence),
            )
            session.add(row)
            if event_type == "essay":
                session.add(ResearchNote(
                    topic_id=topic_id, group_id="fixture", group_name="测试分组", title="测试资料",
                    content="仅临时测试内容", content_hash=topic_id,
                    symbol_codes=",".join(symbols), created_at=row.event_at,
                ))
            session.commit()
            return row.id

    try:
        yield repo, add
    finally:
        engine.dispose()


READERS = ("all", "recent", "overview", "full", "feed", "between", "announcements")
SYMBOL_CASES = (
    ("S", ("603306.SH", "300308.SZ", "SAP", "MS")),
    ("A", ("AAPL", "AA", "BABA")),
    ("603306.SH", ("1603306.SH", "603306.SHX", "603306.SZ")),
    ("300308.SZ", ("1300308.SZ", "300308.SZX", "300308.SH")),
    ("920748.BJ", ("1920748.BJ", "920748.BJX")),
    ("00700.HK", ("100700.HK", "00700.HKX", "00700")),
    ("HK00700", ("HK007000", "XHK00700")),
    ("AAPL", ("XAAPL", "AAPLX")),
    ("BRK.B", ("BRK.A", "BRK.BX")),
    ("BRK-B", ("BRK-A", "BRK-BX")),
    ("A%", ("AAPL", "ABC")),
    ("A_", ("AB", "AC")),
    ("A/B", ("AB", "A//B")),
)


def read_ids(repo, reader, symbol):
    if reader == "all":
        rows = repo.all_symbol_events(symbol=symbol, days=183)
    elif reader == "recent":
        rows = repo.recent_symbol_events(symbol=symbol, days=183)
    elif reader in {"overview", "full"}:
        bundle = repo.symbol_workspace_bundle(symbol=symbol, days=183, overview=reader == "overview")
        rows = [row for key in ("snapshots", "research", "announcements", "essays", "messages", "timeline")
                for row in bundle[key]]
    elif reader == "feed":
        rows, total = repo.list_events(symbol=symbol, days=183)
        assert total == len(rows)
    else:
        kwargs = {"symbol": symbol, "start_at": utc_naive_now() - timedelta(days=183),
                  "end_at": utc_naive_now()}
        if reader == "between":
            rows, total = repo.list_events_between(event_types=("company_announcement",), **kwargs)
        else:
            rows, total = repo.list_announcements(**kwargs)
        assert total == len(rows)
    return {row["id"] for row in rows}


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize(("symbol", "other_symbols"), SYMBOL_CASES)
def test_readers_match_only_complete_literal_security_tokens(symbol_repository, reader, symbol, other_symbols):
    repo, add = symbol_repository
    expected = {add([symbol]), add(["OTHER", symbol, "LAST"])}
    # Newer unrelated rows must not displace the correct rows before LIMIT.
    for other in other_symbols:
        add([other])
    assert read_ids(repo, reader, symbol) == expected


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("symbol", ("", "  ", "S,AAPL", ",S", "S,"))
def test_explicit_empty_or_multiple_security_filter_fails_closed(symbol_repository, reader, symbol):
    repo, add = symbol_repository
    add(["S", "AAPL"])
    add([])
    assert read_ids(repo, reader, symbol) == set()


@pytest.mark.parametrize("reader", ("feed", "between", "announcements"))
def test_omitted_optional_symbol_remains_unfiltered(symbol_repository, reader):
    repo, add = symbol_repository
    expected = {add(["S"]), add(["AAPL"])}
    assert read_ids(repo, reader, None) == expected


@pytest.mark.parametrize(("symbol", "other_symbols"), SYMBOL_CASES)
def test_dashboard_and_consensus_candidate_reads_share_exact_security_filter(symbol_repository, symbol, other_symbols):
    repo, add = symbol_repository
    expected = {add([symbol], event_type="essay"), add(["OTHER", symbol], event_type="essay")}
    for other in other_symbols:
        add([other], event_type="essay")
    assert repo.symbol_dashboard_stats(symbol=symbol, days=183)["event_count"] == 2
    candidates = repo.consensus_note_candidates(symbol=symbol, cutoff=utc_naive_now() - timedelta(days=183))
    assert {row["event_id"] for row in candidates} == expected


@pytest.mark.parametrize("symbol", ("", "  ", "S,AAPL"))
def test_dashboard_and_consensus_reject_explicit_empty_or_multiple_symbols(symbol_repository, symbol):
    repo, add = symbol_repository
    add(["S", "AAPL"], event_type="essay")
    assert repo.symbol_dashboard_stats(symbol=symbol, days=183)["event_count"] == 0
    assert repo.consensus_note_candidates(symbol=symbol, cutoff=utc_naive_now() - timedelta(days=183)) == []


@pytest.mark.parametrize("reader", ("between", "announcements"))
def test_free_text_query_keeps_its_existing_fuzzy_symbol_search(symbol_repository, reader):
    repo, add = symbol_repository
    expected = {add(["603306.SH"]), add(["300308.SZ"])}
    add(["AAPL"])
    kwargs = {"query": "S", "start_at": utc_naive_now() - timedelta(days=183), "end_at": utc_naive_now()}
    if reader == "between":
        rows, total = repo.list_events_between(event_types=("company_announcement",), **kwargs)
    else:
        rows, total = repo.list_announcements(**kwargs)
    assert total == 2
    assert {row["id"] for row in rows} == expected


@pytest.mark.parametrize("tokens", (("S", "OTHER"), ("OTHER", "S"), ("OTHER", "S", "LAST")))
def test_symbol_token_is_found_at_any_csv_position(symbol_repository, tokens):
    repo, add = symbol_repository
    expected = add(tokens)
    assert read_ids(repo, "all", "S") == {expected}


@pytest.mark.parametrize("overview", (True, False))
@pytest.mark.parametrize("symbol", ("S", "603306.SH", "300308.SZ", "00700.HK", "HK00700", "AAPL", "BRK.B"))
def test_workspace_does_not_assemble_another_securitys_snapshots(symbol_repository, monkeypatch, symbol, overview):
    repo, add = symbol_repository
    own_id = add([symbol], event_type="fundamental_snapshot", metrics={
        "indicator": {"end_date": "20260630", "q_sales_yoy": 5},
        "income": {"end_date": "20260630", "revenue": 100},
    })
    add([f"X{symbol}"], event_type="fundamental_snapshot", metrics={"income": {"revenue": 999}})
    add(["603306.SH" if symbol == "S" else f"{symbol}X"],
        event_type="fundamental_snapshot", metrics={"income": {"revenue": 888}})
    monitor = InvestmentMonitorService.__new__(InvestmentMonitorService)
    monitor.repo, monitor._name_cache = repo, {symbol: "测试公司"}
    monkeypatch.setattr("src.services.investment_monitor_service.get_index_stock_name", lambda _: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **_: SimpleNamespace(snapshot=lambda *a, **k: {}))
    bundle = repo.symbol_workspace_bundle(symbol=symbol, days=183, overview=overview)
    assert [row["id"] for row in bundle["snapshots"]] == [own_id]
    stock = monitor._super_stock(symbol, days=183, overview=overview)
    assert stock["symbol"] == symbol
    assert stock["fundamentals"]["revenue"] == 100
    assert stock["fundamentals"]["revenue_yoy"] == 5
