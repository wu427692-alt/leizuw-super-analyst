"""Semantic export integration with real private SQLite jobs and ZIP files.

Completions are deterministic fixtures, not provider calls.  Relevance labels
are deliberately independent of title matching; source originals stay intact.
"""

from collections import Counter
from contextlib import contextmanager
import copy
import csv
from datetime import datetime, timedelta
import io
import json
import sqlite3
import zipfile

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from src.repositories.essay_analysis_repo import EssayAnalysisRepository
from src.request_identity import get_current_user_id, reset_current_user_id, set_current_user_id
from src.services.essay_export_service import EssayExportService
from src.services.essay_focus_service import EssayFocusService
from src.storage import EssayAnalysisRecord, ResearchNote


@pytest.fixture(autouse=True)
def owner():
    token = set_current_user_id(17)
    try:
        yield
    finally:
        reset_current_user_id(token)


def record(number, classification="focused", *, title=None, original=None):
    return {"topic_id": str(number), "title": title if title is not None else f"产业研究 {number}",
            "content": original if original is not None else
                f"宁德时代电池业务：{classification}，独立样本{number}。原文利润、产能及估值论证。",
            "source": "机构甲", "author": "研究员", "published_at": "2026-10-21T08:00:00Z",
            "status": "completed", "summary": "已有分析不能重写", "tags": ["已有标签", "电池"],
            "model": "existing-analysis-model", "fixture_classification": classification}


class Repository:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.snapshots, self.reads = [], []

    def iter_export_snapshot(self, *, requested_limit=None, batch_size=250, **filters):
        self.snapshots.append({"requested_limit": requested_limit, **filters})
        count = len(self.rows)
        target = min(count, requested_limit) if requested_limit else count
        yield {"total": count, "target_total": target, "items": [], "done": False}
        for start in range(0, target, batch_size):
            yield {"items": [(index + 1, self.rows[index]["topic_id"])
                             for index in range(start, min(target, start + batch_size))], "done": False}
        yield {"items": [], "done": True}

    def export_payloads(self, topic_ids):
        self.reads.extend(topic_ids)
        wanted = set(topic_ids)
        yield from (copy.deepcopy(row) for row in self.rows if row["topic_id"] in wanted)


class ControlledFocus:
    """Exercise the real service parser, evidence checks and coverage envelope."""
    prompt_version = EssayFocusService.prompt_version

    def __init__(self):
        self.calls = []
        self.overrides = {}
        self.classifier = EssayFocusService(self.complete)

    def classify(self, query, item, *, on_progress=None):
        self.calls.append(item["topic_id"])
        classification = self.overrides.get(item["topic_id"], item.get("fixture_classification", "focused"))
        self._classification = classification
        return self.classifier.classify(query, item, on_progress=on_progress)

    def complete(self, request):
        source = json.loads(request["messages"][1]["content"])
        classification = self._classification
        original = source["original"]
        role = {"focused": "central", "mentioned": "supporting", "irrelevant": "none", "uncertain": "unclear"}[classification]
        return {"classification": classification, "reason": f"确定性测试主体判定：{classification}。",
                "primary_subjects": ["宁德时代" if classification == "focused" else "全市场或其他公司"],
                "target_role": role, "substantive_analysis": classification == "focused",
                "document_shape": "single_company" if classification == "focused" else "roundup",
                "scope_assessment": {"organizing_question": "确定性主体研究测试。",
                    "coherence": "target_line" if classification == "focused" else "independent_branches",
                    "blocks": [{"block_id": block["block_id"],
                                "role": "target_core" if classification == "focused" else "independent_other",
                                "topic": "原文主体", "relation": "确定性测试结构，不代表模型质量评分。",
                                "supports_block_ids": []} for block in source["scope_blocks"]]},
                "evidence": [{"quote": original[:35], "subject": "原文研究对象"}]
                    if classification in {"focused", "mentioned"} else []}


def finish(service, task_id, max_steps=2000):
    for _ in range(max_steps):
        state = service.get(task_id)
        if state["status"] in {"completed", "no_results", "failed"}:
            return state
        assert service.run_once(), state
    pytest.fail("focused fixture export never reached a terminal state")


def package(service, task_id):
    with zipfile.ZipFile(service.download_file(task_id)) as archive:
        assert archive.testzip() is None
        return {"rows": json.loads(archive.read("小作文.json")),
                "csv": list(csv.DictReader(io.StringIO(archive.read("小作文.csv").decode("utf-8-sig")))),
                "markdown": archive.read("清单.md").decode("utf-8"),
                "receipt": json.loads(archive.read("检索与完整性.json")),
                "audit": json.loads(archive.read("主旨筛选依据.json")),
                "names": archive.namelist()}


def create(service, *, limit=None, workspace="meeting"):
    return service.create({"query": "宁德时代", "query_scope": "focused", "days": 2},
                          workspace=workspace, requested_limit=limit)


def test_all_37_candidates_screened_without_old_20_cap_and_semantic_count_not_claimed_up_front(tmp_path):
    rows = [record(index, "focused" if index < 22 else "mentioned" if index < 32 else "irrelevant")
            for index in range(37)]
    classifier, repo = ControlledFocus(), Repository(rows)
    service = EssayExportService(root=tmp_path, repo=repo, focus_service=classifier)
    task = create(service)
    assert task["matched_total"] is task["target_total"] is task["candidate_total"] is None
    service.run_once()
    indexed = service.get(task["task_id"])
    assert indexed["candidate_total"] == 37
    assert indexed["matched_total"] is indexed["target_total"] is None
    assert repo.snapshots[0]["query_scope"] == "full" and repo.snapshots[0]["requested_limit"] is None
    service.run_once()
    collected = service.get(task["task_id"])
    assert collected["candidate_retrieved_count"] == 37 and collected["screened_count"] == 0
    assert collected["matched_total"] is None
    service.run_once()
    one = service.get(task["task_id"])
    assert one["screened_count"] == 1 and one["matched_total"] is None and 25 < one["progress"] < 26
    state = finish(service, task["task_id"])
    assert state["status"] == "completed", state
    assert classifier.calls == [str(index) for index in range(37)]
    assert state["screened_count"] == state["candidate_total"] == 37
    assert state["matched_total"] == state["target_total"] == state["packaged_count"] == 22 > 20
    assert state["focused_count"] == 22 and state["mentioned_count"] == 10 and state["irrelevant_count"] == 5
    assert state["focus_screening_complete"] and state["complete"] and state["library_complete"]
    data = package(service, task["task_id"])
    assert len(data["rows"]) == 22 and len(data["audit"]) == 37


def test_main_company_without_title_keyword_included_but_title_match_roundup_excluded(tmp_path):
    rows = [record(1, title="从成本和产能看电池龙头", original="宁德时代电池业务：整篇讨论公司业务、盈利、估值和验证。"),
            record(2, "mentioned", title="宁德时代等公司盘后汇总", original="宏观政策。宁德时代获新订单。其余十家行业动态。")]
    service = EssayExportService(root=tmp_path, repo=Repository(rows), focus_service=ControlledFocus())
    task = create(service)
    state = finish(service, task["task_id"])
    assert state["matched_total"] == 1 and state["mentioned_count"] == 1
    data = package(service, task["task_id"])
    assert [row["topic_id"] for row in data["rows"]] == ["1"]
    assert data["rows"][0]["title"] == rows[0]["title"]
    assert [entry["review"]["classification"] for entry in data["audit"]] == ["focused", "mentioned"]


def test_explicit_count_applies_after_screening_not_to_initial_candidate_page(tmp_path):
    rows = [record(index, "mentioned" if index < 28 else "focused") for index in range(37)]
    classifier, repo = ControlledFocus(), Repository(rows)
    service = EssayExportService(root=tmp_path, repo=repo, focus_service=classifier)
    task = create(service, limit=3)
    state = finish(service, task["task_id"])
    assert state["screened_count"] == 37 and len(classifier.calls) == 37
    assert state["matched_total"] == 9 and state["target_total"] == state["packaged_count"] == 3
    assert not state["library_complete"] and state["request_complete"]
    assert repo.snapshots[0]["requested_limit"] is None
    data = package(service, task["task_id"])
    assert [row["topic_id"] for row in data["rows"]] == ["28", "29", "30"]
    assert data["receipt"]["user_limited"] and data["receipt"]["requested_limit"] == 3
    assert len(data["audit"]) == 37


def test_uncertain_pauses_without_complete_archive_and_manual_resume_only_rechecks_uncertain(tmp_path):
    rows = [record(0), record(1, "uncertain"), record(2, "mentioned"), record(3)]
    classifier = ControlledFocus()
    service = EssayExportService(root=tmp_path, repo=Repository(rows), focus_service=classifier)
    task = create(service)
    failed = finish(service, task["task_id"])
    assert failed["status"] == "failed" and failed["screened_count"] == 4 and failed["uncertain_count"] == 1
    assert not failed["complete"] and not failed["focus_screening_complete"]
    assert failed["matched_total"] is None and failed["result"] is None
    assert not list(tmp_path.rglob("*.zip"))
    assert not service.run_once()  # No automatic paid review loop.
    reviews = service.focus_reviews(task["task_id"])
    assert reviews["total"] == 4 and len(reviews["items"]) == 4
    classifier.overrides["1"] = "focused"
    service.resume(task["task_id"])
    state = finish(service, task["task_id"])
    assert state["status"] == "completed" and state["uncertain_count"] == 0 and state["focused_count"] == 3
    assert Counter(classifier.calls) == Counter({"0": 1, "1": 2, "2": 1, "3": 1})
    assert state["screened_count"] == 4
    assert len(package(service, task["task_id"])["rows"]) == 3


def test_restart_uses_each_committed_review_and_source_payload_without_repeating_classification(tmp_path):
    classifier, repo = ControlledFocus(), Repository([record(index) for index in range(7)])
    first = EssayExportService(root=tmp_path, repo=repo, focus_service=classifier)
    task = create(first)
    for _ in range(5):  # snapshot, collection, 3 independently durable reviews
        first.run_once()
    assert first.get(task["task_id"])["screened_count"] == 3
    assert classifier.calls == ["0", "1", "2"]
    second = EssayExportService(root=tmp_path, repo=repo, focus_service=classifier)
    state = finish(second, task["task_id"])
    assert state["status"] == "completed" and Counter(classifier.calls) == Counter(str(index) for index in range(7))
    assert len(repo.snapshots) == 1 and repo.reads == [str(index) for index in range(7)]


def test_same_owner_cross_task_and_workspace_reuses_complete_content_cache(tmp_path):
    classifier = ControlledFocus()
    service = EssayExportService(root=tmp_path, repo=Repository([record(0), record(1, "mentioned")]), focus_service=classifier)
    first = create(service, workspace="one")
    assert finish(service, first["task_id"])["semantic_new_reviews"] == 2
    second = create(service, workspace="two")
    state = finish(service, second["task_id"])
    assert state["semantic_cache_hits"] == 2 and state["semantic_new_reviews"] == 0
    assert classifier.calls == ["0", "1"]
    assert all(entry["review"]["cache_hit"] for entry in package(service, second["task_id"])["audit"])
    assert service.focus_reviews(first["task_id"], workspace="two") is None


def test_different_account_cannot_read_review_or_reuse_private_focus_cache(tmp_path):
    classifier = ControlledFocus()
    service = EssayExportService(root=tmp_path, repo=Repository([record(0)]), focus_service=classifier)
    first = create(service)
    finish(service, first["task_id"])
    token = set_current_user_id(18)
    try:
        assert service.focus_reviews(first["task_id"]) is None
        assert service.get(first["task_id"]) is None
        assert service.resume(first["task_id"]) is None
        with pytest.raises(FileNotFoundError):
            service.download_file(first["task_id"])
        second = create(service)
        state = finish(service, second["task_id"])
        assert state["semantic_new_reviews"] == 1 and state["semantic_cache_hits"] == 0
        assert service.focus_reviews(second["task_id"])["total"] == 1
    finally:
        reset_current_user_id(token)
    assert service.focus_reviews(second["task_id"]) is None
    assert classifier.calls == ["0", "0"]


def test_zero_focus_preserves_paginated_evidence_without_empty_success_zip(tmp_path):
    rows = [record(index, "mentioned" if index % 2 else "irrelevant") for index in range(7)]
    service = EssayExportService(root=tmp_path, repo=Repository(rows), focus_service=ControlledFocus())
    task = create(service)
    state = finish(service, task["task_id"])
    assert state["status"] == "no_results" and state["candidate_total"] == state["screened_count"] == 7
    assert state["matched_total"] == state["target_total"] == 0
    assert state["focus_screening_complete"] and state["result"] is None
    assert not list(tmp_path.rglob("*.zip"))
    pages = [service.focus_reviews(task["task_id"], page=page, page_size=3) for page in (1, 2, 3)]
    assert all(page["total"] == 7 for page in pages)
    assert [entry["topic_id"] for page in pages for entry in page["items"]] == [str(index) for index in range(7)]
    assert len(pages[-1]["items"]) == 1
    with pytest.raises(FileNotFoundError):
        service.download_file(task["task_id"])


def test_all_formats_and_audit_retain_exact_original_tags_evidence_and_private_files(tmp_path):
    original = "=宁德时代完整原文第一行。\n" + "逐字不能改写。" * 70 + "\n最后一行。"
    rows = [record(0, original=original), record(1, "mentioned")]
    before = copy.deepcopy(rows)
    repo = Repository(rows)
    service = EssayExportService(root=tmp_path, repo=repo, focus_service=ControlledFocus())
    task = create(service)
    assert finish(service, task["task_id"])["status"] == "completed"
    data = package(service, task["task_id"])
    assert set(data["names"]) == {"小作文.json", "小作文.csv", "清单.md", "主旨筛选依据.json", "检索与完整性.json"}
    assert data["rows"][0]["content"] == original and original in data["markdown"]
    assert data["rows"][0]["tags"] == before[0]["tags"]
    assert data["rows"][0]["summary"] == before[0]["summary"]
    assert data["csv"][0]["content"] == "'" + original  # CSV formula protection only.
    assert data["rows"][0]["focus_review"]["evidence"][0]["quote"] in original
    assert len(data["audit"]) == 2 and "全文主旨筛选依据" in data["markdown"]
    assert data["receipt"]["semantic_screening_performed"] and data["receipt"]["new_ai_analysis_performed"]
    assert not data["receipt"]["source_record_analysis_modified"]
    assert not data["receipt"]["source_attachments_downloaded"]
    assert repo.rows == before
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o077 == 0


def test_full_or_title_exports_never_invoke_semantic_classifier(tmp_path):
    class Forbidden:
        def classify(self, *_, **__):
            pytest.fail("non-focused export unexpectedly called AI")
    service = EssayExportService(root=tmp_path, repo=Repository([record(0)]), focus_service=Forbidden())
    for scope in ("full", "title"):
        task = service.create({"query": "宁德时代", "query_scope": scope})
        assert finish(service, task["task_id"])["status"] == "completed"
        assert service.focus_reviews(task["task_id"]) is None


def test_real_readonly_source_repository_recalls_nontitle_focus_and_never_changes_existing_analysis(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'source.sqlite3'}")
    ResearchNote.__table__.create(engine)
    EssayAnalysisRecord.__table__.create(engine)
    originals = [record(index, title="产业链深度分析" if index % 2 else "宁德时代市场汇总") for index in range(37)]
    with engine.begin() as connection:
        connection.execute(insert(ResearchNote), [
            {"topic_id": row["topic_id"], "group_id": "fixture", "group_name": row["source"],
             "title": row["title"], "content": row["content"], "topic_type": "talk",
             "content_hash": row["topic_id"], "created_at": datetime(2026, 10, 21) + timedelta(seconds=index)}
            for index, row in enumerate(originals)])
        connection.execute(insert(EssayAnalysisRecord), {"topic_id": "1", "status": "completed", "model": "existing",
            "prompt_version": "existing", "input_hash": "existing", "summary": "已有分析不能重写", "tags_json": '["保留标签"]'})

    class Database:
        @contextmanager
        def get_session(self):
            with Session(engine) as session:
                yield session

    classifier = ControlledFocus()
    repository = EssayAnalysisRepository(db_manager=Database())
    service = EssayExportService(root=tmp_path / "jobs", repo=repository, focus_service=classifier)
    task = service.create({"query": "宁德时代", "query_scope": "focused",
        "start_date": "2026-10-21", "end_date": "2026-10-22"})
    state = finish(service, task["task_id"])
    assert state["candidate_total"] == state["screened_count"] == state["matched_total"] == 37
    assert len(classifier.calls) == 37
    data = package(service, task["task_id"])
    assert len(data["rows"]) == 37 and any(row["title"] == "产业链深度分析" for row in data["rows"])
    assert {row["topic_id"]: row["content"] for row in data["rows"]} == {row["topic_id"]: row["content"] for row in originals}
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM research_notes").scalar() == 37
        existing = connection.exec_driver_sql("SELECT summary,tags_json,model FROM essay_analysis_records WHERE topic_id='1'").one()
        assert tuple(existing) == ("已有分析不能重写", '["保留标签"]', "existing")
    engine.dispose()


def test_unchanged_title_body_deduplicates_but_changed_source_must_be_reviewed_again(tmp_path):
    classifier, repo = ControlledFocus(), Repository([record(0)])
    service = EssayExportService(root=tmp_path, repo=repo, focus_service=classifier)
    first = create(service)
    finish(service, first["task_id"])
    repo.rows[0]["content"] += "新增的完整原文。"
    second = create(service)
    state = finish(service, second["task_id"])
    assert state["semantic_cache_hits"] == 0 and state["semantic_new_reviews"] == 1
    assert classifier.calls == ["0", "0"]


def test_multi_batch_prepare_closes_partly_consumed_cursor_and_resumes_without_model_redo(tmp_path):
    # Both the first 250-row batch and explicit final limit leave unread source
    # cursor rows. A retained SQLite read cursor used to deadlock metadata writes.
    rows = [record(index, "focused" if index < 256 else "mentioned") for index in range(266)]
    classifier, repo = ControlledFocus(), Repository(rows)
    first = EssayExportService(root=tmp_path, repo=repo, focus_service=classifier)
    task = create(first, limit=253)
    for _ in range(1000):
        state = first.get(task["task_id"])
        if state["packaged_count"] == 250:
            break
        assert first.run_once()
    else:
        pytest.fail("first bounded preparation checkpoint did not complete")
    assert not state["checkpoint"]["prepared_complete"]
    assert state["matched_total"] == 256 and state["target_total"] == 253
    assert len(classifier.calls) == 266
    second = EssayExportService(root=tmp_path, repo=repo, focus_service=classifier)
    finished = finish(second, task["task_id"])
    assert finished["status"] == "completed" and finished["packaged_count"] == 253
    assert len(classifier.calls) == 266  # restart reuses all semantic checkpoints
    data = package(second, task["task_id"])
    assert [row["topic_id"] for row in data["rows"]] == [str(index) for index in range(253)]
    assert len(data["audit"]) == 266 and len(data["csv"]) == 253
    assert not finished["library_complete"]


def test_background_worker_temporarily_uses_persisted_owner_for_model_usage_and_restores_context(tmp_path):
    identities = []
    class OwnerAware(ControlledFocus):
        def classify(self, query, item, *, on_progress=None):
            identities.append(get_current_user_id())
            return super().classify(query, item, on_progress=on_progress)
    service = EssayExportService(root=tmp_path, repo=Repository([record(0)]), focus_service=OwnerAware())
    task = create(service)
    token = set_current_user_id(None)
    try:
        for _ in range(6):
            service.run_once()
            assert get_current_user_id() is None
    finally:
        reset_current_user_id(token)
    assert service.get(task["task_id"])["status"] == "completed"
    assert identities == [17]


def test_invalid_identity_envelope_is_failure_not_cached_or_exported(tmp_path):
    class WrongSource(ControlledFocus):
        def classify(self, query, item, *, on_progress=None):
            result = super().classify(query, item, on_progress=on_progress)
            result["source_sha256"] = "some-other-original"
            return result
    bad = WrongSource()
    service = EssayExportService(root=tmp_path, repo=Repository([record(0)]), focus_service=bad)
    task = create(service)
    failed = finish(service, task["task_id"])
    assert failed["status"] == "failed" and not failed["complete"] and failed["screened_count"] == 0
    assert not list(tmp_path.rglob("*.zip"))
    assert not list(tmp_path.rglob("focus-cache/*.json"))
    service.focus_service = ControlledFocus()
    service.resume(task["task_id"])
    assert finish(service, task["task_id"])["status"] == "completed"


def test_stale_second_tab_resume_cannot_overwrite_new_review_or_completed_counts(tmp_path, monkeypatch):
    classifier = ControlledFocus()
    repo = Repository([record(0), record(1, "uncertain"), record(2)])
    first = EssayExportService(root=tmp_path, repo=repo, focus_service=classifier)
    task = create(first)
    assert finish(first, task["task_id"])["status"] == "failed"
    # Tab B read a failed task before tab A resumed and resolved its one
    # uncertainty. This deliberately retains the stale _find result.
    second = EssayExportService(root=tmp_path, repo=repo, focus_service=classifier)
    old_found = second._find(task["task_id"])
    assert old_found[1]["uncertain_count"] == 1 and old_found[1]["screened_count"] == 3
    classifier.overrides["1"] = "focused"
    first.resume(task["task_id"])
    assert first.run_once()
    fresh = first.get(task["task_id"])
    assert fresh["status"] == "screening" and fresh["screened_count"] == fresh["focused_count"] == 3
    assert fresh["uncertain_count"] == 0
    with monkeypatch.context() as patch:
        patch.setattr(second, "_find", lambda *_args, **_kwargs: old_found)
        resumed = second.resume(task["task_id"])
    assert resumed["status"] == fresh["status"]
    assert resumed["screened_count"] == resumed["focused_count"] == 3
    assert resumed["uncertain_count"] == 0
    assert second.get(task["task_id"])["screened_count"] == 3
    completed = finish(second, task["task_id"])
    assert completed["status"] == "completed" and completed["matched_total"] == completed["packaged_count"] == 3
    assert completed["screened_count"] == completed["focused_count"] == 3
    assert Counter(classifier.calls) == Counter({"0": 1, "1": 2, "2": 1})
    assert len(package(second, task["task_id"])["audit"]) == 3


def attachment_record(number, *, original="「文件」"):
    row = record(number, title=f"宁德时代电话会{number}.pdf", original=original)
    row["note"] = {"title": row["title"], "content": original,
                   "files": [{"file_id": f"file-{number}", "name": row["title"], "asset_kind": "file"}]}
    return row


def test_two_text_originals_and_attachment_index_are_counted_separately_without_reading_pdf(tmp_path):
    rows = [record(0), record(1, "mentioned"), attachment_record(2)]
    classifier = ControlledFocus()
    service = EssayExportService(root=tmp_path, repo=Repository(rows), focus_service=classifier)
    task = create(service)
    state = finish(service, task["task_id"])
    assert state["status"] == "completed" and state["candidate_total"] == state["screened_count"] == 3
    assert state["focused_count"] == state["mentioned_count"] == state["source_only_count"] == 1
    assert state["uncertain_count"] == 0 and state["semantic_new_reviews"] == 2
    assert classifier.calls == ["0", "1"]  # Never ask the model to guess a PDF from its filename.
    data = package(service, task["task_id"])
    assert [row["topic_id"] for row in data["rows"]] == ["0"]
    assert len(data["audit"]) == 3
    index = next(entry["review"] for entry in data["audit"] if entry["topic_id"] == "2")
    assert index["classification"] == "source_only" and index["decision_kind"] == "source_type_exclusion"
    assert not index["attachment_content_read"] and not index["semantic_review_performed"]
    assert not index["scope_eligible"] and not index["coverage"]["required"]
    assert not index["coverage"]["read_complete"] and not index["coverage"]["complete"]
    assert index["source_files"][0]["file_id"] == "file-2"
    assert data["receipt"]["text_originals_reviewed"] == 2
    assert data["receipt"]["source_only_count"] == 1
    assert not data["receipt"]["source_attachment_content_read"]


def test_real_short_company_text_with_pdf_attachment_still_gets_semantic_review(tmp_path):
    original = "宁德时代电池业务产能紧张，交付能力决定竞争优势，本文分析该机制。"
    classifier = ControlledFocus()
    service = EssayExportService(root=tmp_path, repo=Repository([attachment_record(0, original=original)]),
                                 focus_service=classifier)
    task = create(service)
    state = finish(service, task["task_id"])
    assert state["status"] == "completed" and state["focused_count"] == 1 and state["source_only_count"] == 0
    assert classifier.calls == ["0"]
    data = package(service, task["task_id"])
    assert data["rows"][0]["content"] == original
    assert data["rows"][0]["focus_review"]["coverage"]["reviewed_chars"] == len(original)
    assert not data["receipt"]["source_attachments_downloaded"]


def test_missing_original_without_asset_metadata_stays_uncertain_not_source_type_exclusion(tmp_path):
    classifier = ControlledFocus()
    service = EssayExportService(root=tmp_path, repo=Repository([record(0, original="")]), focus_service=classifier)
    task = create(service)
    state = finish(service, task["task_id"])
    assert state["status"] == "failed" and state["source_only_count"] == 0 and state["uncertain_count"] == 1
    review = service.focus_reviews(task["task_id"])["items"][0]["review"]
    assert review["classification"] == "uncertain" and review["error_code"] == "missing_original"
    assert not review["coverage"]["complete"]
    assert not state["complete"] and not list(tmp_path.rglob("*.zip"))


def test_all_attachment_indexes_have_auditable_no_text_result_without_empty_zip_or_model_calls(tmp_path):
    classifier = ControlledFocus()
    service = EssayExportService(root=tmp_path, repo=Repository([attachment_record(0), attachment_record(1, original="")]),
                                 focus_service=classifier)
    task = create(service)
    state = finish(service, task["task_id"])
    assert state["status"] == "no_results" and state["candidate_total"] == state["source_only_count"] == 2
    assert state["matched_total"] == state["target_total"] == state["focused_count"] == 0
    assert state["semantic_new_reviews"] == state["semantic_cache_hits"] == 0 and classifier.calls == []
    assert state["result"] is None and not list(tmp_path.rglob("*.zip"))
    reviews = service.focus_reviews(task["task_id"])
    assert reviews["total"] == 2
    assert all(entry["review"]["classification"] == "source_only" and
               not entry["review"]["attachment_content_read"] for entry in reviews["items"])
    with pytest.raises(FileNotFoundError):
        service.download_file(task["task_id"])


class FuturePolicy(ControlledFocus):
    prompt_version = "next-semantic-policy-test"

    def classify(self, *_, **__):
        pytest.fail("an old-policy task must not receive a new-policy judgment")


def test_policy_upgrade_resume_retains_old_uncertainty_and_requires_explicit_new_task(tmp_path):
    classifier = ControlledFocus()
    service = EssayExportService(root=tmp_path, repo=Repository([record(0), record(1, "uncertain")]),
                                 focus_service=classifier)
    task = create(service)
    failed = finish(service, task["task_id"])
    assert failed["status"] == "failed" and failed["uncertain_count"] == 1
    reviews = service.focus_reviews(task["task_id"])
    old_policy = failed["focus_policy_version"]
    service.focus_service = FuturePolicy()
    changed = service.resume(task["task_id"])
    assert changed["status"] == "failed" and not changed["resumable"] and changed["requires_new_task"]
    assert changed["focus_policy_outdated"] and changed["expected_focus_policy_version"] == FuturePolicy.prompt_version
    assert changed["focus_policy_version"] == old_policy and changed["uncertain_count"] == 1
    assert changed["screened_count"] == 2 and not changed["complete"]
    assert service.focus_reviews(task["task_id"]) == reviews  # no deletion of the old uncertain row
    assert classifier.calls == ["0", "1"] and not service.run_once()
    assert not list(tmp_path.rglob("*.zip"))


@pytest.mark.parametrize("prepared", [False, True])
def test_policy_upgrade_blocks_unfinished_preparation_or_zip_publication_with_old_judgments(tmp_path, prepared):
    classifier = ControlledFocus()
    service = EssayExportService(root=tmp_path, repo=Repository([record(0), record(1)]), focus_service=classifier)
    task = create(service)
    for _ in range(30):
        state = service.get(task["task_id"])
        if state["focus_screening_complete"] and state["checkpoint"]["prepared_complete"] == prepared:
            break
        assert service.run_once()
    else:
        pytest.fail("could not freeze the chosen old-policy package stage")
    reviews = service.focus_reviews(task["task_id"])
    assert state["status"] != "completed"
    service.focus_service = FuturePolicy()
    assert service.run_once()
    changed = service.get(task["task_id"])
    assert changed["status"] == "failed" and changed["requires_new_task"] and changed["focus_policy_outdated"]
    assert changed["expected_focus_policy_version"] == FuturePolicy.prompt_version
    assert changed["screened_count"] == changed["focused_count"] == 2
    assert service.focus_reviews(task["task_id"]) == reviews and classifier.calls == ["0", "1"]
    assert not list(tmp_path.rglob("*.zip"))
    with pytest.raises(FileNotFoundError):
        service.download_file(task["task_id"])


def test_completed_old_policy_archive_remains_available_and_is_not_rewritten_on_upgrade(tmp_path):
    service = EssayExportService(root=tmp_path, repo=Repository([record(0)]), focus_service=ControlledFocus())
    task = create(service)
    before = finish(service, task["task_id"])
    original_zip = service.download_file(task["task_id"]).read_bytes()
    reviews = service.focus_reviews(task["task_id"])
    service.focus_service = FuturePolicy()
    resumed = service.resume(task["task_id"])
    assert resumed == before and resumed["status"] == "completed"
    assert not service.run_once()
    assert service.download_file(task["task_id"]).read_bytes() == original_zip
    assert service.focus_reviews(task["task_id"]) == reviews
