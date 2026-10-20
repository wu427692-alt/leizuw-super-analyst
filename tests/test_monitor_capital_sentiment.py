"""Legacy capital snapshot sentiment is neutral on every read, without writes."""

from contextlib import contextmanager
from datetime import timedelta
import json

import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event, select
from sqlalchemy.orm import Session

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.storage import DatabaseManager, MonitoringEventRecord, MonitoringSourceRecord, utc_naive_now


@pytest.fixture()
def saved_sentiments(tmp_path, monkeypatch):
    monkeypatch.setattr(DatabaseManager, "get_instance", lambda: pytest.fail("Application DB must not initialize"))
    engine = create_engine(f"sqlite:///{tmp_path / 'capital-sentiment.db'}")
    for model in (MonitoringSourceRecord, MonitoringEventRecord):
        model.__table__.create(engine)
    now = utc_naive_now()
    rows = [
        ("capital-bull", "capital_chip_snapshot", "bullish", 90, "unit.capital"),
        ("capital-bear", "capital_chip_snapshot", "bearish", 70, "unit.capital"),
        ("capital-neutral", "capital_chip_snapshot", "neutral", 60, "unit.capital"),
        ("capital-mixed", "capital_chip_snapshot", "mixed", 55, "unit.capital"),
        # Same source, different type: do not neutralize a genuine opinion.
        ("news-bull", "news", "bullish", 80, "unit.capital"),
        ("news-neutral", "news", "neutral", 50, "unit.capital"),
        ("news-bear", "news", "bearish", 70, "unit.other"),
        ("technical-bull", "technical_factor", "bullish", 72, "unit.other"),
        ("fundamental-bear", "fundamental_snapshot", "bearish", 64, "unit.other"),
        ("announcement-bull", "company_announcement", "bullish", 75, "cninfo.announcements"),
    ]
    original = {name: sentiment for name, _kind, sentiment, _importance, _source in rows}
    expected = {name: "neutral" if kind == "capital_chip_snapshot" else sentiment
                for name, kind, sentiment, _importance, _source in rows}
    with Session(engine) as session:
        for name, kind, sentiment, importance, source_key in rows:
            session.add(MonitoringEventRecord(
                source_key=source_key, source_name="测试来源", source_type="test", external_id=name,
                event_type=kind, perspective="investor", title=name, summary="已保存来源摘录",
                symbol_codes="603306.SH", sentiment=sentiment, importance_score=importance,
                confidence_score=0.8, tags_json="[]", actors_json="[]",
                metrics_json=json.dumps({"chip_performance": {"winner_rate": 3.52}}),
                raw_payload=json.dumps({"retained": name}), event_at=now, ingested_at=now,
            ))
        session.commit()

    def original_rows():
        with engine.connect() as connection:
            return connection.execute(select(
                MonitoringEventRecord.id, MonitoringEventRecord.external_id,
                MonitoringEventRecord.sentiment, MonitoringEventRecord.summary,
                MonitoringEventRecord.metrics_json, MonitoringEventRecord.raw_payload,
            ).order_by(MonitoringEventRecord.id)).all()

    before = original_rows()
    identifiers = {row.external_id: row.id for row in before}
    statements = []

    def readonly_sql(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement.lower())
        assert statement.lstrip().split(None, 1)[0].lower() not in {
            "insert", "update", "delete", "replace", "create", "drop", "alter",
        }, "Read APIs must not mutate saved events or schema"

    sqlalchemy_event.listen(engine, "before_cursor_execute", readonly_sql)
    sqlalchemy_event.listen(engine, "commit", lambda _connection: pytest.fail("Read APIs must not commit"))

    class TemporaryDatabase:
        @contextmanager
        def get_session(self):
            with Session(engine, autoflush=False) as session:
                yield session

    yield InvestmentMonitorRepository(TemporaryDatabase()), identifiers, expected, statements, now

    after = original_rows()
    assert after == before
    assert {row.external_id: row.sentiment for row in after} == original
    engine.dispose()


@pytest.mark.parametrize("reader", [
    "list", "all_recent", "all_symbol", "recent_symbol", "between", "all_between", "by_ids", "detail",
])
def test_every_event_read_projects_only_capital_snapshots_as_neutral(saved_sentiments, reader):
    repo, identifiers, expected, _statements, now = saved_sentiments
    if reader == "list":
        records, total = repo.list_events(days=7, symbol="603306.SH")
        assert total == len(expected)
    elif reader == "all_recent":
        records = repo.all_recent_events(days=7)
    elif reader == "all_symbol":
        records = repo.all_symbol_events(symbol="603306.SH", days=7)
    elif reader == "recent_symbol":
        records = repo.recent_symbol_events(symbol="603306.SH", days=7)
    elif reader == "between":
        records, total = repo.list_events_between(
            event_types=("capital_chip_snapshot", "news", "technical_factor", "fundamental_snapshot", "company_announcement"),
            start_at=now - timedelta(days=1), end_at=now + timedelta(days=1), symbol="603306.SH",
        )
        assert total == len(expected)
    elif reader == "all_between":
        records = repo.all_events_between(
            event_types=("capital_chip_snapshot", "news", "technical_factor", "fundamental_snapshot", "company_announcement"),
            start_at=now - timedelta(days=1), end_at=now + timedelta(days=1),
        )
    elif reader == "by_ids":
        records = repo.get_events_by_ids(identifiers.values())
    else:
        records = [repo.get_event(identifier) for identifier in identifiers.values()]
    assert {row["external_id"]: row["sentiment"] for row in records} == expected
    assert all(row["summary"] == "已保存来源摘录" for row in records)
    assert all(row["metrics"] == {"chip_performance": {"winner_rate": 3.52}} for row in records)


@pytest.mark.parametrize("overview", [True, False])
def test_workspace_snapshot_and_timeline_use_the_same_read_sentiment(saved_sentiments, overview):
    repo, _identifiers, expected, _statements, _now = saved_sentiments
    bundle = repo.symbol_workspace_bundle(symbol="603306.SH", days=7, overview=overview)
    assert bundle["snapshots"]
    for collection in ("snapshots", "timeline", "announcements", "messages"):
        for row in bundle[collection]:
            assert row["sentiment"] == expected[row["external_id"]]
    capital = next(row for row in bundle["snapshots"] if row["event_type"] == "capital_chip_snapshot")
    assert capital["sentiment"] == "neutral"


def test_announcement_reader_keeps_noncapital_sentiment(saved_sentiments):
    repo, _identifiers, _expected, _statements, now = saved_sentiments
    records, total = repo.list_announcements(start_at=now - timedelta(days=1), end_at=now + timedelta(days=1))
    assert total == 1
    assert records[0]["external_id"] == "announcement-bull"
    assert records[0]["sentiment"] == "bullish"


def test_recent_sql_counts_group_legacy_capital_with_neutral_records(saved_sentiments):
    repo, _identifiers, _expected, statements, _now = saved_sentiments
    stats = repo.recent_event_stats(days=7)
    assert stats["event_count"] == 10
    assert stats["bullish_count"] == 3
    assert stats["bearish_count"] == 2
    assert stats["source_sentiments"] == {
        "unit.capital": {"neutral": 5, "bullish": 1},
        "unit.other": {"bullish": 1, "bearish": 2},
        "cninfo.announcements": {"bullish": 1},
    }
    assert sum(sum(counts.values()) for counts in stats["source_sentiments"].values()) == stats["event_count"]
    assert any("case when" in statement and "group by" in statement for statement in statements)


def test_symbol_sql_counts_and_weight_do_not_use_legacy_capital_direction(saved_sentiments):
    repo, _identifiers, _expected, statements, _now = saved_sentiments
    stats = repo.symbol_dashboard_stats(symbol="603306.SH", days=7)
    assert stats["event_count"] == 10
    assert stats["sentiment"] == {"neutral": 5, "bullish": 3, "bearish": 2}
    assert stats["weighted_sentiment"] == 53
    assert stats["high_priority_count"] == 3
    assert any("case when" in statement and "sum(" in statement for statement in statements)
