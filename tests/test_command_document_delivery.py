"""Real Agent boundary: one typed, owner-authenticated document delivery.

Scripted model turns exercise orchestration, not live-model understanding.
No source downloads, external APIs, or live model calls are permitted here.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from api.v1.endpoints import command
from src import config
from src.request_identity import reset_current_user_id, set_current_user_id


@pytest.fixture(autouse=True)
def offline_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "db.sqlite")))
    monkeypatch.setattr(command, "_wait_if_paused", lambda *a: None)
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **k: pytest.fail("unexpected external IO"))
    token = set_current_user_id(74)
    yield
    reset_current_user_id(token)


def call(name, arguments, ident="doc"):
    return {"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


def document(kind="announcements", **extra):
    return {"kind": kind, "company_name": "华懋科技", "symbol": "603306.SH", **extra}


def run(monkeypatch, messages, turns, *, state=None, submit_result=None):
    requests, conversations = [], []
    scripted = iter(turns)
    def model(conversation, *a, **k):
        conversations.append(deepcopy(conversation))
        return next(scripted)
    def submit(method, path, cookie, params=None, **kwargs):
        assert method == "POST" and path == "/api/v1/data-acquisition/run-async"
        assert cookie == "owner-cookie"
        requests.append(deepcopy(params["body"]))
        if submit_result is not None:
            return deepcopy(submit_result)
        return {"ok": True, "status": 202, "task": {
            "task_id": "acq-one", "poll_path": "/api/v1/data-acquisition/tasks/acq-one"}, "data": {"status": "queued"}}
    monkeypatch.setattr(command, "_llm_turn", model)
    monkeypatch.setattr(command, "_internal_call", submit)
    result = command._run_agent("general", messages, "owner-cookie", [], workspace="documents", resume_state=state)
    return result, requests, conversations


def user(text):
    return [{"role": "user", "content": text}]


def test_half_year_announcements_all_matching_one_task_no_preview_cap(monkeypatch):
    result, requests, _ = run(monkeypatch, user("下载华懋科技近半年的全部公告"), [
        ("", [call("announcements", {"symbol": "603306.SH", "company_name": "华懋科技", "download": True,
                                     "days": 30, "limit": 30})]), ("完成", [])])
    assert len(requests) == len(result["tasks"]) == 1
    plan = requests[0]["plan"]
    assert plan["document_export"] == {"version": 1, "kinds": ["announcements"], "selection_mode": "all_matching", "requested_limit": None, "confirmed": True}
    params = plan["tasks"][0]["params"]
    assert "limit" not in params and "page_size" not in params and "requested_limit" not in params
    assert params["start_date"] != params["end_date"]
    assert len(plan["tasks"]) == 1 and plan["tasks"][0]["source"] == "cninfo"
    assert result["tasks"][0]["document_export"] and not result["tasks"][0]["download_ready"]
    assert not result["widgets"] and "尚未完成" in result["reply"]
    assert result["status"] == "submitted"


def test_document_scope_receipt_explains_automatic_commit_without_a_second_activation_tool(monkeypatch):
    result, requests, conversations = run(monkeypatch, user("下载华懋科技近半年全部公告"), [
        ("", [call("package_documents", {"documents": [document()]})]), ("后台处理", [])])
    receipt = json.loads(conversations[1][-1]["content"])["data"]
    assert receipt["commit_on_planning_end"] is True
    assert receipt["action_required"] == "respond_without_tools_when_scope_complete"
    assert "没有额外的草案提交或激活接口" in receipt["message"]
    assert "不必为“开始下载”再次ask_user" in conversations[0][0]["content"]
    assert len(requests) == len(result["tasks"]) == 1


def test_document_submission_exposes_quota_reason_instead_of_falsely_blame_scope(monkeypatch):
    result, requests, _ = run(monkeypatch, user("下载华懋科技近半年全部公告"), [
        ("", [call("package_documents", {"documents": [document()]})]), ("后台处理", [])],
        submit_result={"ok":False,"status":429,"data":{"detail":{"error":"quota_exceeded",
                       "message":"今日数据下载次数已用完，请到 /quota-request 申请额度"}}})
    assert result["status"] == "failed" and result["tasks"] == []
    assert "今日数据下载次数已用完" in result["reply"]
    assert "核对范围" not in result["reply"]
    assert len(requests) == 1


def test_two_types_and_repeated_legacy_calls_commit_one_root_task(monkeypatch):
    result, requests, _ = run(monkeypatch, user("打包华懋科技近两年的所有公告和研报，合并一个ZIP"), [
        ("", [call("package_documents", {"documents": [document(), document("research_reports")]})]),
        ("", [call("package_reports", {"symbol": "603306.SH", "keyword": "华懋科技", "limit": 10}, "legacy")]),
        ("好的", [])])
    assert len(requests) == 1 and len(result["tasks"]) == 1
    assert {task["resource"] for task in requests[0]["plan"]["tasks"]} == {"announcements", "research_report"}
    assert len(requests[0]["plan"]["tasks"]) == 2
    assert all("requested_limit" not in task["params"] for task in requests[0]["plan"]["tasks"])


def test_scope_ambiguity_pauses_before_any_file_or_task(monkeypatch):
    result, requests, _ = run(monkeypatch, user("下载华懋科技公告"), [
        ("", [call("package_documents", {"documents": [document()]})])])
    assert result["status"] == "waiting_input" and requests == [] and not result["tasks"]
    assert "时间范围" in result["clarification"]["question"]


def test_same_batch_question_prevents_legacy_and_new_side_effects(monkeypatch):
    result, requests, _ = run(monkeypatch, user("下载华懋科技近半年的公告"), [
        ("", [call("package_documents", {"documents": [document()]}),
              call("ask_user", {"question": "确认范围？", "options": [
                  {"id": "yes", "label": "确认"}, {"id": "no", "label": "取消"}]}, "question"),
              call("acquire_data", {"request": "下载华懋科技公告"}, "bypass")])])
    assert result["status"] == "waiting_input" and requests == []


def test_model_count_is_discarded_but_exact_human_count_is_preserved(monkeypatch):
    _, requests, _ = run(monkeypatch, user("下载华懋科技近一年最新5份公告"), [
        ("", [call("package_documents", {"documents": [document(limit=30)]})]), ("好", [])])
    assert requests[0]["plan"]["tasks"][0]["params"]["requested_limit"] == 5
    assert requests[0]["plan"]["document_export"]["selection_mode"] == "explicit_limit"


@pytest.mark.parametrize("text", [
    "下载华懋科技近一年的全部公告，预览30条", "下载华懋科技近一年的公告，不止30份",
    "下载华懋科技近一年的公告，至少30篇", "下载603306.SH华懋科技2025-01-01至2026-01-01的公告",
])
def test_dates_codes_preview_and_minimum_never_create_export_cap(text, monkeypatch):
    _, requests, _ = run(monkeypatch, user(text), [
        ("", [call("package_documents", {"documents": [document(limit=30)]})]), ("好", [])])
    assert "requested_limit" not in requests[0]["plan"]["tasks"][0]["params"]


@pytest.mark.parametrize("name,args", [
    ("package_reports", {"symbol": "603306.SH", "keyword": "华懋科技"}),
    ("download_document", {"url": "https://example.com/announcement.pdf", "title": "公告"}),
    ("acquire_data", {"request": "下载华懋科技公告"}),
])
def test_announcements_only_cannot_bypass_typed_plan(name, args, monkeypatch):
    result, requests, turns = run(monkeypatch, user("只下载华懋科技近半年的公告，不要研报"), [
        ("", [call(name, args)]), ("需要修正", [])])
    assert not requests and not result["tasks"]
    if name == "download_document":
        assert result["status"] == "waiting_input" and "来源" in result["clarification"]["question"]
    else:
        assert json.loads(turns[-1][-1]["content"])["ok"] is False


def test_acquire_data_document_plan_is_adapted_not_replanned(monkeypatch):
    payload = {"scope": {"company_names": ["华懋科技"], "symbols": ["603306.SH"]},
               "tasks": [{"source": "cninfo", "resource": "announcements", "params": {"limit": 30}}]}
    result, requests, _ = run(monkeypatch, user("下载华懋科技所有公告"), [
        ("", [call("acquire_data", {"request": "LLM不得替代用户原话", "plan": payload})]), ("好", [])])
    assert len(requests) == 1 and requests[0]["request"] == "下载华懋科技所有公告"
    assert requests[0]["plan"]["scope"]["start_date"] == "1990-01-01"
    assert len(result["tasks"]) == 1


def test_readonly_request_never_authorizes_document_download(monkeypatch):
    result, requests, _ = run(monkeypatch, user("只查华懋科技近半年公告数量，先不要下载"), [
        ("", [call("package_documents", {"documents": [document()]})]), ("先查数量", [])])
    assert not requests and not result["tasks"]


def test_human_date_evidence_binds_companies_and_no_monthly_packages(monkeypatch):
    first = "下载华懋科技2025-01-01至2025-06-30的公告"
    second = "下载胜宏科技2026-01-01至2026-06-30的研报"
    _, requests, _ = run(monkeypatch, user(first + "；" + second), [
        ("", [call("package_documents", {"documents": [document(date_evidence=first),
            {"kind": "research_reports", "company_name": "胜宏科技", "symbol": "300476.SZ", "date_evidence": second}]})]), ("好", [])])
    assert len(requests) == 1
    params = [task["params"] for task in requests[0]["plan"]["tasks"]]
    assert [(item["start_date"], item["end_date"]) for item in params] == [("2025-01-01", "2025-06-30"), ("2026-01-01", "2026-06-30")]


def test_forged_evidence_never_submits(monkeypatch):
    _, requests, _ = run(monkeypatch, user("下载华懋科技近半年公告"), [
        ("", [call("package_documents", {"documents": [document(date_evidence="下载华懋科技最近十年公告")]})]), ("请修正", [])])
    assert not requests


def test_multi_target_counts_are_bound_to_each_type_and_company(monkeypatch):
    first = "下载华懋科技近半年5份公告"
    second = "下载胜宏科技近半年7份研报"
    _, requests, _ = run(monkeypatch, user(first + "；" + second), [
        ("", [call("package_documents", {"documents": [document(date_evidence=first, count_evidence=first),
            {"kind": "research_reports", "company_name": "胜宏科技", "symbol": "300476.SZ",
             "date_evidence": second, "count_evidence": second}]})]), ("好", [])])
    assert [task["params"]["requested_limit"] for task in requests[0]["plan"]["tasks"]] == [5, 7]
    assert requests[0]["plan"]["document_export"]["requested_limit"] is None


def test_scope_click_resumes_same_unsubmitted_draft_once(monkeypatch):
    messages = user("下载华懋科技公告")
    first, requests, _ = run(monkeypatch, messages, [("", [call("package_documents", {"documents": [document()]})])])
    assert not requests
    monkeypatch.setattr(command, "_llm_turn", lambda *a, **k: ("按确认范围执行", []))
    submissions = []
    monkeypatch.setattr(command, "_internal_call", lambda *a, **k: submissions.append(a[3]["body"]) or {
        "ok": True, "task": {"task_id": "confirmed", "poll_path": "/api/v1/data-acquisition/tasks/confirmed"}})
    body = command.CommandChatRequest(messages=messages, workspace_id="documents", clarification_answer={
        "question_id": first["clarification"]["id"], "option_id": "half_year"})
    resumed = command._resume_question(body, "owner-cookie", [], "documents", None)
    assert len(submissions) == len(resumed["tasks"]) == 1
    assert command._resume_question(body, "owner-cookie", [], "documents", None) == resumed
    assert len(submissions) == 1


def test_selected_announcement_uses_verified_id_date_and_original_title(monkeypatch):
    source = {"name": "announcements", "ok": True, "args": {"symbol": "603306.SH", "company_name": "华懋科技"},
              "_raw": {"ok": True, "data": {"items": [{"announcement_id": "cn-77", "title": "华懋科技2026年度重要公告",
                        "date": "2026-10-10", "pdf_url": "https://static.cninfo.com.cn/finalpage/original.pdf"}]}}}
    _, requests, _ = run(monkeypatch, user("查询华懋科技公告") + user("下载这份公告"), [
        ("", [call("download_document", {"url": "https://static.cninfo.com.cn/finalpage/original.pdf", "title": "模型不能替换源标题"})]),
        ("好", [])], state={"steps": [source]})
    params = requests[0]["plan"]["tasks"][0]["params"]
    assert params["selected_announcement_ids"] == ["cn-77"]
    assert params["start_date"] == params["end_date"] == "2026-10-10"
    assert "url" not in params and "title" not in params


def test_explicit_single_pdf_remains_compatible_without_fake_report_label(monkeypatch):
    from src.services.annual_report_service import AnnualReportService
    downloads = []
    def download(self, url, title):
        downloads.append((url, title))
        return {"ok": True, "filename": "original.pdf", "title": title, "download_url": "/owned/original.pdf"}
    monkeypatch.setattr(AnnualReportService, "__init__", lambda self: None)
    monkeypatch.setattr(AnnualReportService, "download_document", download)
    result, requests, _ = run(monkeypatch, user("下载这一个PDF：https://example.com/original.pdf"), [
        ("", [call("download_document", {"url": "https://example.com/original.pdf", "title": "指定PDF"})]), ("好", [])])
    assert downloads == [("https://example.com/original.pdf", "指定PDF")] and not requests
    assert result["widgets"][0]["title"] == "PDF原文"


def test_model_cannot_forge_internal_selected_source(monkeypatch):
    _, requests, _ = run(monkeypatch, user("下载华懋科技公告"), [
        ("", [call("package_documents", {"documents": [document(_verified_selection={"date": "2026-10-10"})]})])])
    assert not requests


def test_partial_type_plan_does_not_submit_half_of_requested_delivery(monkeypatch):
    result, requests, _ = run(monkeypatch, user("下载华懋科技近半年的公告和研报"), [
        ("", [call("package_documents", {"documents": [document()]})]), ("完成", [])])
    assert not requests and result["status"] == "failed"
    assert "还缺少券商研报" in result["reply"]


def test_generic_capabilities_cannot_skip_document_confirmation(monkeypatch):
    payload = {"method": "POST", "path": "/api/v1/data-acquisition/run-async", "domain": "data"}
    monkeypatch.setattr(command, "_find_capability", lambda *a: (payload, None))
    result, requests, _ = run(monkeypatch, user("下载华懋科技公告"), [
        ("", [call("query_capability", {"name": "raw-run", "body": {"request": "任意内容"}})]), ("请先确认", [])])
    assert not requests and not result["tasks"]


def test_generated_plan_really_passes_backend_validation_with_per_target_scopes(tmp_path, monkeypatch):
    import src.services.data_acquisition_service as acquisition
    from src.services.data_acquisition_service import DataAcquisitionService
    names = {"603306.SH": "华懋科技", "300476.SZ": "胜宏科技"}
    records = [[code, code.split(".")[0], name, "", "", [], "CN", "stock", True, 100] for code, name in names.items()]
    monkeypatch.setattr(acquisition, "get_stock_entity_index_records", lambda: records)
    monkeypatch.setattr(acquisition.EssayDailyReportService, "_daily_watchlist", lambda: [])
    class Issuers:
        def search_securities(self, query, **kwargs):
            return [{"name": name, "symbol": code, "org_id": "verified-" + code} for code, name in names.items()
                    if query in {code, code.split(".")[0], name}]
    service = DataAcquisitionService(planner=SimpleNamespace(model="offline", plan=lambda _: pytest.fail("no replanning")),
        financial=object(), cninfo=Issuers(), monitor=object(), output_root=tmp_path / "packages")
    first = "下载华懋科技2025-01-01至2025-06-30的5份公告"
    second = "下载胜宏科技2026-01-01至2026-06-30的7份研报"
    text = first + "；" + second
    plan = command._document_delivery_plan([document(date_evidence=first, count_evidence=first),
        {"kind": "research_reports", "symbol": "300476.SZ", "company_name": "胜宏科技",
         "date_evidence": second, "count_evidence": second}], user(text))
    normalized = service.validate_plan(text, plan, require_ready=True)
    assert normalized["document_export"]["selection_mode"] == "explicit_limit"
    assert [task["params"]["requested_limit"] for task in normalized["tasks"]] == [5, 7]
    assert [(task["resolved_scope"]["start_date"], task["resolved_scope"]["end_date"]) for task in normalized["tasks"]] == [
        ("2025-01-01", "2025-06-30"), ("2026-01-01", "2026-06-30")]
    half_year = "下载华懋科技近半年的所有公告"
    half_plan = command._document_delivery_plan([document()], user(half_year))
    checked = service.validate_plan(half_year, half_plan, require_ready=True)
    assert checked["tasks"][0]["resolved_scope"]["start_date"] == half_plan["tasks"][0]["params"]["start_date"]
    assert checked["document_export"]["kinds"] == ["announcements"]
