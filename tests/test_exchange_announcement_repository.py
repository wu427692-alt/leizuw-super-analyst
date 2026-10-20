from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading

import pytest

from src.config import Config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.storage import DatabaseManager


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "exchange-announcements.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    repo = InvestmentMonitorRepository(DatabaseManager.get_instance())
    try:
        yield repo
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _announcement(
    source_key: str,
    external_id: str,
    *,
    title: str = "华懋科技：关于召开股东大会的公告",
    symbol: str = "603306.SH",
    event_at: datetime = datetime(2026, 9, 8, 1, 30),
    disclosure_date: str | None = "2026-09-08",
    url: str | None = None,
):
    names = {
        "cninfo.announcements": "巨潮资讯公告",
        "sse.announcements": "上海证券交易所公告",
        "szse.announcements": "深圳证券交易所公告",
        "other.announcements": "其他公告",
    }
    metrics = {"file_type": "PDF"}
    if disclosure_date is not None:
        metrics["disclosure_date"] = disclosure_date
    return {
        "source_key": source_key,
        "source_name": names[source_key],
        "source_type": "exchange",
        "external_id": external_id,
        "event_type": "company_announcement",
        "perspective": "company",
        "title": title,
        "summary": "上市公司公告。",
        "url": url or f"https://example.test/{source_key}/{external_id}.pdf",
        "symbols": symbol.split(",") if symbol else [],
        "sentiment": "neutral",
        "importance_score": 80,
        "confidence_score": 1.0,
        "tags": ["公司公告"],
        "actors": ["华懋科技"],
        "metrics": metrics,
        "raw_payload": {"id": external_id},
        "event_at": event_at,
    }


def test_szse_same_announcement_id_merges_provenance_and_is_idempotent(repository):
    primary = _announcement(
        "cninfo.announcements", "1212345678",
        url="https://static.cninfo.example/1212345678.pdf",
    )
    fallback = _announcement(
        "szse.announcements", "1212345678",
        title="交易所版本标题也可不同",
        url="https://disc.static.szse.example/1212345678.pdf",
    )

    assert repository.upsert_events([primary])["created"] == 1
    merged = repository.upsert_events([fallback])
    repeated = repository.upsert_events([fallback])
    rows, total = repository.list_announcements(
        start_at=datetime(2026, 9, 8), end_at=datetime(2026, 9, 8, 23, 59, 59),
    )

    assert merged == {"created": 0, "updated": 1, "received": 1}
    assert repeated == {"created": 0, "updated": 0, "received": 1}
    assert total == 1
    assert rows[0]["source_key"] == "cninfo.announcements"
    assert rows[0]["url"] == primary["url"]
    assert [item["source_key"] for item in rows[0]["metrics"]["_source_provenance"]] == [
        "cninfo.announcements", "szse.announcements",
    ]
    assert [item["disclosure_date"] for item in rows[0]["metrics"]["_source_provenance"]] == [
        "2026-09-08", "2026-09-08",
    ]


def test_sse_exact_symbol_day_and_normalized_title_merges_without_fuzzy_matching(repository):
    primary = _announcement(
        "cninfo.announcements", "cninfo-1",
        title="<em>华懋科技</em>&nbsp;： 关于召开股东大会的公告",
        disclosure_date=None,
        # Naive event timestamps are UTC; this is 2026-09-08 in Shanghai.
        event_at=datetime(2026, 9, 7, 16, 30),
    )
    fallback = _announcement(
        "sse.announcements", "sse-991",
        title="关于召开股东大会的公告",
        disclosure_date="2026-09-08",
    )

    repository.upsert_events([primary])
    result = repository.upsert_events([fallback])
    rows, total = repository.list_announcements(
        start_at=datetime(2026, 9, 7), end_at=datetime(2026, 9, 9),
    )

    assert result["created"] == 0
    assert result["updated"] == 1
    assert total == 1
    assert rows[0]["source_key"] == "cninfo.announcements"


def test_shared_numeric_id_still_requires_matching_disclosure_day(repository):
    repository.upsert_events([
        _announcement("cninfo.announcements", "1212345678", disclosure_date="2026-09-08"),
    ])

    result = repository.upsert_events([
        _announcement(
            "szse.announcements", "1212345678",
            title="交易所标题可能不同", disclosure_date="2026-09-09",
            event_at=datetime(2026, 9, 9, 1, 30),
        ),
    ])

    assert result["created"] == 1


def test_sse_does_not_share_numeric_id_namespace_with_cninfo(repository):
    repository.upsert_events([
        _announcement("cninfo.announcements", "1212345678", title="关于事项甲的公告"),
    ])

    result = repository.upsert_events([
        _announcement("sse.announcements", "1212345678", title="关于事项乙的公告"),
    ])

    assert result["created"] == 1


def test_provenance_alias_survives_title_change_and_blocks_second_same_source_id(repository):
    primary = _announcement("cninfo.announcements", "cninfo-a", title="华懋科技：事项公告")
    first_sse = _announcement("sse.announcements", "sse-a", title="事项公告")
    second_sse = _announcement("sse.announcements", "sse-b", title="事项公告")
    repository.upsert_events([primary])
    assert repository.upsert_events([first_sse])["created"] == 0

    revised_first = {**first_sse, "title": "事项公告（交易所标题修订）"}
    alias_result = repository.upsert_events([revised_first])
    distinct_result = repository.upsert_events([second_sse])
    rows, total = repository.list_announcements(
        start_at=datetime(2026, 9, 8), end_at=datetime(2026, 9, 8, 23, 59, 59),
    )

    assert alias_result == {"created": 0, "updated": 0, "received": 1}
    assert distinct_result["created"] == 1
    assert total == 2
    primary_row = next(row for row in rows if row["source_key"] == "cninfo.announcements")
    assert [item["external_id"] for item in primary_row["metrics"]["_source_provenance"]] == [
        "cninfo-a", "sse-a",
    ]


def test_ambiguous_content_rows_are_not_paired_one_to_one_by_arrival_order(repository):
    primary = _announcement("cninfo.announcements", "cninfo-a", title="华懋科技：事项公告")
    first_sse = _announcement("sse.announcements", "sse-a", title="事项公告")
    second_sse = _announcement("sse.announcements", "sse-b", title="事项公告")
    second_cninfo = _announcement("cninfo.announcements", "cninfo-b", title="华懋科技：事项公告")
    repository.upsert_events([primary])
    repository.upsert_events([first_sse])
    repository.upsert_events([second_sse])

    result = repository.upsert_events([second_cninfo])

    assert result["created"] == 1
    _, total = repository.list_announcements(
        start_at=datetime(2026, 9, 8), end_at=datetime(2026, 9, 8, 23, 59, 59),
    )
    assert total == 3


def test_same_source_different_ids_and_ambiguous_cross_source_match_are_not_merged(repository):
    first = _announcement("cninfo.announcements", "cninfo-a")
    second = _announcement("cninfo.announcements", "cninfo-b")
    fallback = _announcement("sse.announcements", "sse-a")

    assert repository.upsert_events([first, second])["created"] == 2
    result = repository.upsert_events([fallback])
    rows, total = repository.list_announcements(
        start_at=datetime(2026, 9, 8), end_at=datetime(2026, 9, 8, 23, 59, 59),
    )

    assert result == {"created": 1, "updated": 0, "received": 1}
    assert total == 3
    assert {row["external_id"] for row in rows} == {"cninfo-a", "cninfo-b", "sse-a"}


@pytest.mark.parametrize(
    "fallback",
    [
        _announcement("sse.announcements", "different-day", disclosure_date="2026-09-07"),
        _announcement("sse.announcements", "different-title", title="华懋科技：关于董事会决议的公告"),
        _announcement("sse.announcements", "multiple-symbols", symbol="603306.SH,300308.SZ"),
    ],
)
def test_sse_content_match_requires_same_day_title_and_one_exact_symbol(repository, fallback):
    repository.upsert_events([_announcement("cninfo.announcements", "cninfo-1")])

    result = repository.upsert_events([fallback])

    assert result["created"] == 1


def test_primary_same_key_refresh_keeps_fallback_provenance(repository):
    primary = _announcement("cninfo.announcements", "shared-id")
    fallback = _announcement("szse.announcements", "shared-id")
    repository.upsert_events([primary])
    repository.upsert_events([fallback])

    refreshed = {**primary, "summary": "巨潮同一公告刷新后的摘要。", "metrics": {"file_type": "PDF"}}
    result = repository.upsert_events([refreshed])
    rows, total = repository.list_announcements(
        start_at=datetime(2026, 9, 8), end_at=datetime(2026, 9, 8, 23, 59, 59),
    )

    assert result["created"] == 0
    assert total == 1
    assert rows[0]["summary"] == "巨潮同一公告刷新后的摘要。"
    assert [item["source_key"] for item in rows[0]["metrics"]["_source_provenance"]] == [
        "cninfo.announcements", "szse.announcements",
    ]


def test_listing_includes_three_official_sources_with_stable_total_and_pages(repository):
    events = [
        _announcement("cninfo.announcements", "cninfo", title="公告一", event_at=datetime(2026, 9, 8, 3)),
        _announcement("sse.announcements", "sse", title="公告二", event_at=datetime(2026, 9, 8, 2)),
        _announcement("szse.announcements", "szse", title="公告三", event_at=datetime(2026, 9, 8, 1)),
        _announcement("other.announcements", "other", title="公告四", event_at=datetime(2026, 9, 8, 4)),
    ]
    repository.upsert_events(events)

    page_one, first_total = repository.list_announcements(
        start_at=datetime(2026, 9, 8), end_at=datetime(2026, 9, 8, 23, 59, 59),
        page=1, page_size=2,
    )
    page_two, second_total = repository.list_announcements(
        start_at=datetime(2026, 9, 8), end_at=datetime(2026, 9, 8, 23, 59, 59),
        page=2, page_size=2,
    )

    assert first_total == second_total == 3
    assert [row["external_id"] for row in page_one] == ["cninfo", "sse"]
    assert [row["external_id"] for row in page_two] == ["szse"]


def test_concurrent_cross_source_writers_serialize_lookup_and_insert(repository):
    barrier = threading.Barrier(2)
    cninfo = _announcement("cninfo.announcements", "1212345678")
    szse = _announcement("szse.announcements", "1212345678", title="交易所标题可能不同")

    def write(event):
        barrier.wait(timeout=2)
        return InvestmentMonitorRepository(repository.db).upsert_events([event])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, (cninfo, szse)))

    rows, total = repository.list_announcements(
        start_at=datetime(2026, 9, 8), end_at=datetime(2026, 9, 8, 23, 59, 59),
    )
    assert sum(result["created"] for result in results) == 1
    assert total == 1
    assert len(rows[0]["metrics"]["_source_provenance"]) == 2
