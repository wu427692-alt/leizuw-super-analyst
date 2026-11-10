"""Keyword recall and full-export regressions; no network or model calls."""
import csv
import io
import json
import zipfile

import pytest

from api.v1.endpoints import command
from src.request_identity import reset_current_user_id, set_current_user_id
from src.services import report_pack_service as packs


def essay(number, *, content="宁德时代 原始正文"):
    return {"topic_id": str(number), "id": number, "summary": "AI摘要", "sentiment": "bullish",
            "note": {"title": f"宁德时代公司更新 {number}", "content": content,
                     "created_at": "2026-10-21T01:00:00Z", "group_name": "机构甲", "author_name": "研究员"}}


def install_feed(monkeypatch, total):
    calls = []
    def internal(method, path, cookie, params=None, **kwargs):
        query = params["query"]
        calls.append(dict(query))
        start = (query["page"] - 1) * query["page_size"]
        rows = [essay(i) for i in range(start, min(start + query["page_size"], total))]
        return {"ok": True, "data": {"items": rows, "total": total,
                                     "page": query["page"], "page_size": query["page_size"]}}
    monkeypatch.setattr(command, "_internal_call", internal)
    return calls


def test_schema_matches_real_feed_page_size_scope_and_total_type():
    definitions = {item["function"]["name"]: item["function"]["parameters"]["properties"] for item in command.TOOLS}
    feed = definitions["essay_radar"]
    assert feed["page_size"]["maximum"] == 100
    assert feed["query_scope"]["enum"] == ["focused", "full", "title"]
    assert feed["known_total"]["type"] == "integer"
    assert definitions["package_essays"]["query_scope"]["enum"] == ["focused", "full", "title"]


def test_invalid_upstream_filters_never_fall_back_to_unfiltered_twenty(monkeypatch):
    calls = []
    def internal(*args, **kwargs):
        calls.append(args)
        return {"ok": False, "status": 422, "data": {"detail": "bad filter"}}
    monkeypatch.setattr(command, "_internal_call", internal)
    result = command._query_essay_radar({"query": "宁德时代", "days": 2, "query_scope": "title", "page_size": 200}, "owned")
    assert not result["ok"]
    assert len(calls) == 1
    assert calls[0][3]["query"] == {"query": "宁德时代", "days": 2, "query_scope": "title", "page": 1, "page_size": 100}


@pytest.mark.parametrize("filters", [{"query_scope": "topic"}, {"days": -1}, {"known_total": True}, {"query": "字" * 201}])
def test_invalid_model_arguments_do_not_access_any_library(monkeypatch, filters):
    monkeypatch.setattr(command, "_internal_call", lambda *a, **k: pytest.fail("invalid filters must not run"))
    result = command._query_essay_radar({"query": "宁德时代", **filters}, "")
    assert not result["ok"]


def test_one_row_count_preview_preserves_keyword_date_and_scope(monkeypatch):
    calls = install_feed(monkeypatch, 237)
    result = command._query_essay_radar({"query": "宁德时代", "days": 2, "query_scope": "title", "page_size": 1}, "")
    data = result["data"]
    assert data["total"] == 237 and len(data["items"]) == 1
    assert data["has_more"] and data["preview_only"]
    assert data["items"][0]["title"] == "宁德时代公司更新 0"
    assert data["items"][0]["content"] == "宁德时代 原始正文"
    assert calls[0]["days"] == 2 and calls[0]["query_scope"] == "title"


def test_focused_preview_is_explicitly_full_candidates_not_subject_classification(monkeypatch):
    calls = install_feed(monkeypatch, 237)
    result = command._query_essay_radar({"query": "宁德时代", "days": 2, "query_scope": "focused", "page_size": 1}, "")
    data = result["data"]
    assert result["ok"] and calls[0]["query_scope"] == "full"
    assert data["filters"]["query_scope"] == "focused" and data["candidate_total"] == data["total"] == 237
    assert data["candidate_only"] and data["focused_count"] is None and not data["focus_screening_complete"]
    assert "不是以公司为整篇主体" in data["note"] and "package_essays" in data["note"]
    encoded = command._tool_result_for_model("essay_radar", result)["data"]
    assert encoded["candidate_only"] and encoded["candidate_total"] == 237 and encoded["focused_count"] is None
    assert "不能代替后台全文判定" in encoded["note"]


def test_focused_package_keeps_scope_for_durable_semantic_worker(monkeypatch):
    from src.services import essay_export_service as exports
    observed = []
    class Service:
        def create(self, filters, workspace=None, requested_limit=None):
            observed.append((filters, workspace, requested_limit))
            return {"task_id": "essay-export-" + "a" * 32, "status": "queued", "progress": None}
        def start_worker(self):
            pass
    monkeypatch.setattr(exports, "get_essay_export_service", Service)
    result = command._package_essay_feed({"query": "宁德时代", "query_scope": "focused", "limit": 3,
        "start_date": "2026-10-21", "end_date": "2026-10-22"}, "private-cookie", "meeting")
    assert result["ok"] and result["task"]["status"] == "queued"
    assert observed[0][0]["query_scope"] == "focused" and observed[0][1:] == ("meeting", 3)
    assert "limit" not in observed[0][0] and "days" not in observed[0][0]


@pytest.mark.parametrize("total", [21, 101, 1001])
def test_export_collects_every_page_above_preview_and_old_thousand_cap(monkeypatch, total):
    calls = install_feed(monkeypatch, total)
    result = command._collect_essay_feed({"query": "宁德时代", "days": 2, "query_scope": "full"}, "owned-cookie")
    assert result["ok"] and result["data"]["complete"]
    assert result["data"]["count"] == total == len(result["items"])
    assert result["data"]["expected_total"] == total
    assert len({row["topic_id"] for row in result["items"]}) == total
    assert [call["page"] for call in calls] == list(range(1, (total + 99) // 100 + 1))
    assert all(call["page_size"] == 100 and call["query"] == "宁德时代" and call["days"] == 2
               and call["query_scope"] == "full" for call in calls)


def test_diagnostic_collector_has_no_implicit_ten_thousand_cap(monkeypatch):
    calls = install_feed(monkeypatch, 10001)
    result = command._collect_essay_feed({"query": "宁德时代"}, "")
    data = result["data"]
    assert data["complete"] and not data["truncated"] and not data["errors"]
    assert data["count"] == data["expected_total"] == 10001
    assert len(calls) == 101


def test_user_limit_does_not_change_offset_page_size(monkeypatch):
    calls = install_feed(monkeypatch, 400)
    result = command._collect_essay_feed({"query": "宁德时代", "limit": 150}, "")
    assert result["data"]["count"] == 150
    assert result["data"]["complete"] and not result["data"]["library_complete"]
    assert [call["page_size"] for call in calls] == [100, 100]
    assert result["data"]["requested_limit"] == 150 and not result["data"]["errors"]
    assert result["items"][-1]["topic_id"] == "149"


@pytest.mark.parametrize("failure", ["api", "empty", "duplicate", "changed", "truncated"])
def test_failed_or_unstable_next_page_cannot_be_a_complete_export(monkeypatch, failure):
    calls = []
    def internal(*args, **kwargs):
        query = args[3]["query"]
        calls.append(dict(query))
        if query["page"] == 1:
            return {"ok": True, "data": {"items": [essay(i) for i in range(100)], "total": 101}}
        if failure == "api":
            return {"ok": False, "status": 504, "error": "timeout"}
        if failure == "empty":
            return {"ok": True, "data": {"items": [], "total": 101}}
        if failure == "duplicate":
            return {"ok": True, "data": {"items": [essay(i) for i in range(100)], "total": 101}}
        if failure == "truncated":
            return {"ok": True, "data": {"items": [essay(100)], "total": 101, "truncated": True}}
        return {"ok": True, "data": {"items": [essay(100)], "total": 102}}
    monkeypatch.setattr(command, "_internal_call", internal)
    result = command._collect_essay_feed({"query": "宁德时代", "days": 2, "query_scope": "title"}, "")
    assert not result["ok"] and not result["data"]["complete"] and result["data"]["truncated"]
    assert result["data"]["errors"] and len(calls) == 2
    if failure == "duplicate":
        assert result["data"]["duplicates"] == 100
    assert all(call["query"] == "宁德时代" and call["days"] == 2 for call in calls)


def test_legacy_inline_package_preserves_long_full_original_and_integrity_receipt(tmp_path, monkeypatch):
    raw_text = "宁德时代完整原文。" * 3000 + "最后一行不能丢失"
    monkeypatch.setattr(command, "_internal_call", lambda *a, **k: {
        "ok": True, "data": {"items": [essay(1, content=raw_text)], "total": 1}})
    monkeypatch.setattr(packs, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(packs, "CACHE_DIR", tmp_path / "cache")
    token = set_current_user_id(17)
    try:
        collected = command._collect_essay_feed({"query": "宁德时代", "days": 2, "query_scope": "title"}, "")
        result = packs.ReportPackService().package_essays(collected["items"], subject="宁德时代",
                                                       workspace="session", retrieval=collected["data"])
        assert result["ok"] and collected["data"]["complete"]
        archive = next((tmp_path / "downloads").rglob("*.zip"))
        with zipfile.ZipFile(archive) as package:
            entries = json.loads(package.read("小作文.json"))
            assert entries[0]["content"] == raw_text
            rows = list(csv.DictReader(io.StringIO(package.read("小作文.csv").decode("utf-8-sig"))))
            assert rows[0]["content"] == raw_text
            manifest = package.read("清单.md").decode("utf-8-sig")
            assert raw_text in manifest and "完整获取" in manifest
            receipt = json.loads(package.read("检索与完整性.json"))
            assert receipt["count"] == receipt["expected_total"] == 1 and receipt["complete"]
            assert receipt["filters"]["query_scope"] == "title"
        assert result["size"] == archive.stat().st_size
    finally:
        reset_current_user_id(token)


@pytest.mark.parametrize("requested_limit", [None, 20, 10001])
def test_command_submits_durable_job_without_collecting_inside_http(monkeypatch, requested_limit):
    from src.services import essay_export_service as exports
    observed = []
    class Service:
        def create(self, filters, workspace=None, requested_limit=None):
            observed.append((filters, workspace, requested_limit))
            return {"task_id": "essay-export-" + "a" * 32, "status": "queued", "progress": None}
        def start_worker(self):
            observed.append("worker")
    monkeypatch.setattr(exports, "get_essay_export_service", Service)
    monkeypatch.setattr(command, "_collect_essay_feed", lambda *a: pytest.fail("HTTP must not collect"))
    filters = {"query": "宁德时代", "query_scope": "full", "days": 2}
    if requested_limit is not None:
        filters["limit"] = requested_limit
    result = command._package_essay_feed(filters, "must-not-be-persisted-cookie", "session")
    assert result["ok"] and result["status"] == "submitted"
    assert not result["data"]["download_ready"] and not result["data"].get("download_url")
    assert observed[0][1:] == ("session", requested_limit)
    assert observed[0][0]["query"] == "宁德时代" and "limit" not in observed[0][0]
    assert "cookie" not in json.dumps(observed)


def test_essay_archives_are_unique_and_published_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(packs, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(packs, "CACHE_DIR", tmp_path / "cache")
    real_replace, replacements = packs.os.replace, []
    def replace(source, target):
        assert source.name.endswith(".tmp") and not target.exists()
        with zipfile.ZipFile(source) as package:
            assert package.testzip() is None
            assert "小作文.json" in package.namelist()
        replacements.append((source, target))
        return real_replace(source, target)
    monkeypatch.setattr(packs.os, "replace", replace)
    token = set_current_user_id(17)
    try:
        service = packs.ReportPackService()
        first = service.package_essays([essay(1)], subject="宁德时代", workspace="session")
        second = service.package_essays([essay(1)], subject="宁德时代", workspace="session")
        assert first["filename"] != second["filename"]
        assert len(list((tmp_path / "downloads").rglob("*.zip"))) == 2
        assert len(replacements) == 2
        assert not list((tmp_path / "downloads").rglob("*.tmp"))
    finally:
        reset_current_user_id(token)


def test_diagnostic_collector_does_not_stop_at_old_interactive_time_budget(monkeypatch):
    calls = install_feed(monkeypatch, 101)
    ticks = iter([0, 0, 181])
    monkeypatch.setattr(command.time, "monotonic", lambda: next(ticks))
    result = command._collect_essay_feed({"query": "宁德时代", "query_scope": "title", "days": 2}, "")
    assert len(calls) == 2 and result["data"]["count"] == 101
    assert result["data"]["complete"] and not result["data"]["errors"]
