"""Exact feed-count query regressions; all records live in a temporary database."""

import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import Session

import src.repositories.essay_analysis_repo as feed_module
from src.repositories.essay_analysis_repo import EssayAnalysisRepository, _cached_feed_count, _feed_count_cache
from src.storage import DatabaseManager, EssayAnalysisRecord, ResearchNote


FILTER_DEFAULTS = dict(
    cutoff=None, query=None, query_scope="full", analysis_status=None,
    sentiment=None, category=None, tag=None, stock=None, min_importance=None,
)


@pytest.fixture()
def feed_repo(tmp_path, monkeypatch):
    # Do not initialize the application, upstream services or the user's DB.
    engine = create_engine(f"sqlite:///{tmp_path / 'feed-count.db'}")
    ResearchNote.__table__.create(engine)
    EssayAnalysisRecord.__table__.create(engine)

    class TemporaryDatabase:
        @contextmanager
        def get_session(self):
            with Session(engine, autoflush=False) as session:
                yield session

    def forbidden_database_initialization():
        raise AssertionError("Tests must use the injected temporary database")

    monkeypatch.setattr(DatabaseManager, "get_instance", forbidden_database_initialization)
    repo = EssayAnalysisRepository(db_manager=TemporaryDatabase())
    _feed_count_cache.clear()
    with Session(engine) as session:
        sequence = 0

        def add(topic_id, *, note=None, analysis=None):
            nonlocal sequence
            sequence += 1
            session.add(ResearchNote(**{
                "topic_id": topic_id, "group_id": "synthetic-group", "group_name": "测试资料",
                "title": "普通纪要", "topic_type": "talk", "symbol_codes": "",
                "content_hash": topic_id, "created_at": datetime(2026, 9, 1, 9) + timedelta(minutes=sequence),
                **(note or {}),
            }))
            if analysis is not None:
                session.add(EssayAnalysisRecord(**{
                    "topic_id": topic_id, "status": "completed", "model": "synthetic",
                    "prompt_version": "test", "input_hash": topic_id, **analysis,
                }))

        for field in ("title", "content", "group_name", "author_name", "symbol_codes", "files_json"):
            value = json.dumps([{"name": "检索目标.pdf"}], ensure_ascii=False) if field == "files_json" else "检索目标"
            add(f"raw-{field}", note={field: value})
        for field in ("summary", "tags_json", "industries_json", "themes_json", "stock_mentions_json"):
            value = "检索目标" if field == "summary" else json.dumps(["检索目标"], ensure_ascii=False)
            add(f"ai-{field}", analysis={field: value})
        add("overlap", note={"title": "检索目标"}, analysis={"summary": "检索目标"})
        add("distinct-same-title", note={"title": "检索目标"})
        add("cross-note-ai", note={"title": "甲词"}, analysis={"summary": "乙词"})
        add("cross-ai-note", note={"content": "乙词"}, analysis={"themes_json": '["甲词"]'})
        add("both-raw", note={"content": "甲词 乙词"})
        add("both-ai", analysis={"summary": "甲词 乙词"})
        add("partial-a", note={"title": "甲词"})
        add("partial-b", analysis={"summary": "乙词"})
        add("alias-raw", note={"title": "NPO"})
        add("alias-ai", analysis={"themes_json": '["光模块"]'})
        add("literal-raw", note={"title": r"收益100%_A\B"})
        add("literal-ai", analysis={"summary": r"收益100%_A\B"})
        add("literal-distractor", note={"title": r"收益100xxA\B"})
        for status in (None, "completed", "pending", "processing", "failed", "media_only"):
            add(f"state-{status or 'not_queued'}", note={"title": "状态词"},
                analysis={"status": status, "summary": "状态词"} if status else None)
        for status in (None, "completed"):
            add(f"memo-{status or 'not_queued'}", note={"title": "状态词", "topic_type": "audio_memo"},
                analysis={"status": status, "summary": "状态词"} if status else None)
        add("filtered", note={"title": "筛选目标", "symbol_codes": "603306.SH",
                              "created_at": datetime(2026, 9, 2, 9)},
            analysis={"summary": "筛选目标", "sentiment": "bullish", "primary_category": "company_research",
                      "tags_json": '["重点标签"]', "stock_mentions_json": '[{"name":"目标公司"}]',
                      "importance_score": 80})
        add("filtered-null", note={"title": "筛选目标"})
        add("filtered-ai-only", analysis={"summary": "筛选目标", "sentiment": "bearish",
                                          "importance_score": 0})
        add("null-enrichment", analysis={})
        session.commit()
    try:
        yield repo, engine
    finally:
        _feed_count_cache.clear()
        engine.dispose()


COUNT_FILTER_CASES = [
    {}, {"query": ""}, {"query": "  "},
    {"query": "检索目标"}, {"query": "不存在"},
    {"query": "检索目标", "query_scope": "title"},
    {"query": "检索目标", "query_scope": " TITLE "},
    {"query": "甲词 乙词"}, {"query": "乙词 甲词"}, {"query": "甲词\n乙词  甲词"},
    {"query": "CPO"}, {"query": "光通信", "query_scope": "title"},
    {"query": r"100%_A\B"}, {"query": "%"}, {"query": "_"},
    *[{"query": "状态词", "analysis_status": status} for status in (
        "essay", "completed", "uncompleted", "not_queued", "pending", "processing", "failed", "media_only",
    )],
    *[{"analysis_status": status} for status in ("essay", "completed", "uncompleted", "not_queued")],
    {"query": "状态词", "query_scope": "title", "analysis_status": "essay"},
    {"query": "甲词 乙词", "analysis_status": "uncompleted"},
    {"query": "甲词 乙词", "analysis_status": "completed"},
    {"query": "筛选目标", "sentiment": "bullish"},
    {"query": "筛选目标", "category": "company_research"},
    {"query": "筛选目标", "tag": "重点"},
    {"query": "筛选目标", "stock": "603306.sh"},
    {"query": "筛选目标", "stock": "目标公司"},
    {"query": "筛选目标", "min_importance": 0},
    {"query": "筛选目标", "min_importance": 80},
    {"query": "筛选目标", "min_importance": 81},
    {"query": "筛选目标", "cutoff": datetime(2026, 9, 2, 9)},
    {"query": "筛选目标", "cutoff": datetime(2026, 9, 2, 9, 0, 1)},
    {"query": "筛选目标", "analysis_status": "essay", "sentiment": "bullish",
     "category": "company_research", "tag": "重点", "stock": "603306.SH", "min_importance": 80,
     "cutoff": datetime(2026, 9, 2)},
    {"query": "筛选目标", "analysis_status": "not_queued", "sentiment": "bullish"},
]


@pytest.mark.parametrize("filters", COUNT_FILTER_CASES)
def test_optimized_count_equals_joined_rows_and_export(feed_repo, filters):
    repo, engine = feed_repo
    filters = {**FILTER_DEFAULTS, **filters}
    where, _ = repo._feed_where_clause(**filters)
    original_ids = (
        select(ResearchNote.topic_id)
        .select_from(ResearchNote)
        .outerjoin(EssayAnalysisRecord, EssayAnalysisRecord.topic_id == ResearchNote.topic_id)
        .where(where)
        .order_by(desc(ResearchNote.created_at), desc(ResearchNote.id))
    )
    with Session(engine) as session:
        expected = list(session.execute(original_ids).scalars())
        exact_count = session.execute(repo._feed_count_statement(where, filters)).scalar_one()
    # The original joined row query remains the count equivalence oracle.
    assert exact_count == len(expected)
    rows, total = repo.list_feed(**filters, page_size=100, known_total=999999)
    assert total == len(expected)
    assert [row["topic_id"] for row in rows] == expected
    assert [row["topic_id"] for row in repo.iter_feed(**filters)] == expected


@pytest.mark.parametrize(("filters", "expected"), [
    ({"query": "检索目标"}, {
        "raw-title", "raw-content", "raw-group_name", "raw-author_name", "raw-symbol_codes", "raw-files_json",
        "ai-summary", "ai-tags_json", "ai-industries_json", "ai-themes_json", "ai-stock_mentions_json",
        "overlap", "distinct-same-title",
    }),
    ({"query": "检索目标", "query_scope": "title"}, {"raw-title", "overlap", "distinct-same-title"}),
    ({"query": "甲词 乙词"}, {"cross-note-ai", "cross-ai-note", "both-raw", "both-ai"}),
    ({"query": "乙词 甲词"}, {"cross-note-ai", "cross-ai-note", "both-raw", "both-ai"}),
    ({"query": "CPO"}, {"alias-raw", "alias-ai"}),
    ({"query": r"100%_A\B"}, {"literal-raw", "literal-ai"}),
    ({"query": "状态词", "analysis_status": "essay"}, {
        "state-not_queued", "state-completed", "state-pending", "state-processing", "state-failed",
    }),
    ({"query": "筛选目标", "analysis_status": "essay", "sentiment": "bullish",
      "category": "company_research", "tag": "重点", "stock": "603306.SH", "min_importance": 80}, {"filtered"}),
])
def test_feed_keeps_literal_alias_cross_source_and_privacy_semantics(feed_repo, filters, expected):
    repo, _ = feed_repo
    rows, total = repo.list_feed(**filters, page_size=100)
    assert total == len(expected)
    assert {row["topic_id"] for row in rows} == expected


def test_count_branches_do_not_load_large_bodies_or_order_results(feed_repo):
    repo, engine = feed_repo
    filters = {**FILTER_DEFAULTS, "query": "检索目标", "analysis_status": "essay"}
    where, _ = repo._feed_where_clause(**filters)
    statement = repo._feed_count_statement(where, filters)
    sql = str(statement.compile(engine, compile_kwargs={"literal_binds": True}))
    assert "UNION ALL" not in sql  # A note matching raw content AND tags counts once.
    assert "UNION" in sql
    assert "ORDER BY" not in sql and "LIMIT" not in sql
    assert "raw_response" not in sql and "raw_payload" not in sql
    with engine.connect() as connection:
        plan = [str(row[-1]) for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + sql)]
    assert any("UNION USING TEMP B-TREE" in step for step in plan)
    assert any("SCAN essay_analysis_records" in step for step in plan)


def test_later_page_reuses_exact_count_without_trusting_client(feed_repo, monkeypatch):
    repo, _ = feed_repo
    rows, total = repo.list_feed(query="检索目标", page_size=5)
    assert len(rows) == 5 and total == 13

    def unexpected_count(*args, **kwargs):
        raise AssertionError("A cached next page must not repeat the full-library count")

    monkeypatch.setattr(repo, "_feed_count_statement", unexpected_count)
    next_rows, next_total = repo.list_feed(query="检索目标", page=2, page_size=5, known_total=1)
    assert len(next_rows) == 5 and next_total == 13
    assert not {row["topic_id"] for row in rows}.intersection(row["topic_id"] for row in next_rows)


@pytest.mark.parametrize("fails", [False, True])
def test_simultaneous_identical_counts_share_one_scan_but_not_other_filters(monkeypatch, fails):
    key = ("singleflight", fails)
    other_key = ("other-database-or-filters", fails)
    started = threading.Event()
    all_waiting = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    calls = []
    waiting = 0

    class ObservedFuture(Future):
        def result(self, timeout=None):
            nonlocal waiting
            with counter_lock:
                waiting += 1
                if waiting == 5:
                    all_waiting.set()
            return super().result(timeout)

    monkeypatch.setattr(feed_module, "Future", ObservedFuture)
    _feed_count_cache.clear()

    def loader():
        calls.append(1)
        started.set()
        assert release.wait(5)
        if fails:
            raise RuntimeError("synthetic count failure")
        return 1432

    try:
        with ThreadPoolExecutor(max_workers=7) as pool:
            owner = pool.submit(_cached_feed_count, key, loader)
            try:
                assert started.wait(2)
                followers = [pool.submit(_cached_feed_count, key, loader) for _ in range(5)]
                assert all_waiting.wait(2)
                # A different cache key must not be held behind the slow scan.
                assert pool.submit(_cached_feed_count, other_key, lambda: 7).result(timeout=2) == 7
            finally:
                release.set()
            for result in [owner, *followers]:
                if fails:
                    with pytest.raises(RuntimeError, match="synthetic count failure"):
                        result.result(timeout=2)
                else:
                    assert result.result(timeout=2) == 1432
        assert len(calls) == 1
        assert key not in feed_module._feed_count_inflight
        if fails:
            assert key not in _feed_count_cache
            assert _cached_feed_count(key, lambda: 9) == 9  # A manual retry can recover.
        else:
            assert _cached_feed_count(key, lambda: 999) == 1432
    finally:
        _feed_count_cache.clear()


def test_expired_count_does_not_get_reused_as_a_current_total():
    key = ("expired",)
    _feed_count_cache[key] = (time.monotonic() - feed_module._FEED_COUNT_CACHE_TTL_SECONDS - 1, 1)
    try:
        assert _cached_feed_count(key, lambda: 2) == 2
    finally:
        _feed_count_cache.pop(key, None)


@pytest.mark.parametrize("filters", [
    {"query_scope": "title"}, {"analysis_status": "completed"}, {"sentiment": "bullish"},
    {"category": "company_research"}, {"tag": "重点"}, {"min_importance": 80},
])
def test_already_selective_count_does_not_add_duplicate_scans(feed_repo, filters):
    repo, engine = feed_repo
    filters = {**FILTER_DEFAULTS, "query": "检索目标", **filters}
    where, _ = repo._feed_where_clause(**filters)
    sql = str(repo._feed_count_statement(where, filters).compile(engine))
    assert "UNION" not in sql
