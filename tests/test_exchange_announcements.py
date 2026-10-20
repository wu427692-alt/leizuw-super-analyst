"""Offline contracts for exact-symbol, complete public exchange announcement metadata."""

from datetime import date, datetime
import json
import time

import pytest
import requests

from src.services.cninfo_announcement_service import CninfoAnnouncementError
from src.services.exchange_announcement_service import ExchangeAnnouncementService


START, END = date(2026, 9, 1), date(2026, 9, 8)


def sh_row(index=1):
    return {"SECURITY_CODE": "603306", "SECURITY_NAME": "华懋科技", "TITLE": f"公告{index}",
            "SSEDATE": "2026-09-08", "ADDDATE": "2026-09-07 17:02:52",
            "URL": f"/disclosure/listedinfo/announcement/c/new/2026-09-08/603306_{index}.pdf"}


def sz_row(index=1):
    return {"secCode": ["300308"], "secName": ["中际旭创"], "title": f"公告{index}",
            "publishTime": "2026-09-04 19:10:08", "annId": 1225549679 + index,
            "attachPath": f"/disc/disk03/finalpage/2026-09-04/file-{index}.PDF",
            "attachFormat": "PDF", "attachSize": 161}


class Response:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status_code = data, status
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.content = json.dumps(data).encode()

    def json(self):
        return self.data


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    post = get


def sh_response(rows=None, total=1, pages=1, page=1, size=50):
    return Response({"pageHelp": {"data": [sh_row()] if rows is None else rows, "total": total,
                                  "pageCount": pages, "pageNo": page, "pageSize": size}})


def run(session, symbol="603306.SH", **kwargs):
    return ExchangeAnnouncementService(session, request_interval=0).fetch(
        symbol=symbol, start_date=START, end_date=END, **kwargs)


def test_sse_uses_disclosure_date_not_backend_insert_timestamp_and_verified_static_host():
    session = Session(sh_response())
    row = run(session)[0]
    assert row["announcement_at"] == datetime(2026, 9, 7, 16)
    assert row["disclosure_date"] == "2026-09-08" and row["time_precision"] == "date"
    assert row["raw"]["ADDDATE"] == "2026-09-07 17:02:52"
    assert row["source_key"] == "sse.announcements"
    assert row["pdf_url"].startswith("https://static.sse.com.cn/disclosure/listedinfo/")
    assert session.calls[0][1]["params"]["productId"] == "603306"
    assert session.calls[0][1]["allow_redirects"] is False
    assert len(session.calls) == 1  # No PDF download.


def test_szse_verified_post_schema_id_and_precise_time():
    session = Session(Response({"announceCount": 1, "data": [sz_row()]}))
    row = run(session, "300308.SZ")[0]
    assert row["announcement_at"] == datetime(2026, 9, 4, 11, 10, 8)
    assert row["announcement_id"] == "1225549680"
    assert row["pdf_url"] == "https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-09-04/file-1.PDF"
    assert row["source_key"] == "szse.announcements" and row["time_precision"] == "second"
    assert session.calls[0][1]["json"] == {
        "channelCode": ["listedNotice_disc"], "stock": ["300308"],
        "seDate": ["2026-09-01", "2026-09-08"], "pageSize": 50, "pageNum": 1,
    }


def test_complete_pagination_shares_request_budget_and_diagnostics():
    session = Session(sh_response(total=2, pages=2, size=1),
                      sh_response([sh_row(2)], total=2, pages=2, page=2, size=1))
    budget, diag = {"remaining": 3, "used": 4}, {}
    rows = run(session, page_size=1, request_budget=budget, diagnostics=diag)
    assert len(rows) == 2 and budget == {"remaining": 1, "used": 6}
    assert diag["pages_fetched"] == 2 and diag["request_attempts"] == 2
    assert not diag["truncated"]
    assert session.calls[1][1]["params"]["pageHelp.pageNo"] == 2


@pytest.mark.parametrize("symbol", ["603306", "603306.SZ", "300308.SH", "300308.BJ", "603306.SH,300308.SZ"])
def test_invalid_or_mismatched_symbol_never_queries(symbol):
    session = Session()
    with pytest.raises(CninfoAnnouncementError):
        run(session, symbol)
    assert session.calls == []


@pytest.mark.parametrize("change", [
    {"SECURITY_CODE": "600519"}, {"SSEDATE": "2026-08-31"}, {"SSEDATE": None},
    {"SSEDATE": "2026-09-08 12:00:00"}, {"TITLE": None},
    {"URL": "http://static.sse.com.cn/disclosure/listedinfo/announcement/a.pdf"},
    {"URL": "https://evil.example/a.pdf"}, {"URL": "//evil.example/a.pdf"},
    {"URL": "/disclosure/listedinfo/announcement/../a.pdf"},
    {"URL": "/disclosure/listedinfo/announcement/%2e%2e/a.pdf"},
    {"URL": "/disclosure/listedinfo/announcement/a.pdf?redirect=evil"},
    {"URL": "/disclosure/listedinfo/announcement/a.exe"},
])
def test_sse_rejects_untrusted_misdated_or_wrong_stock_rows(change):
    with pytest.raises(CninfoAnnouncementError, match="校验失败"):
        run(Session(sh_response([{**sh_row(), **change}])))


@pytest.mark.parametrize("change", [
    {"secCode": ["300476"]}, {"secCode": "300308"}, {"secName": []},
    {"publishTime": "2026-09-04"}, {"publishTime": "2026-09-04 19:60:08"},
    {"annId": None}, {"annId": True}, {"attachFormat": "HTML"},
    {"attachPath": "https://evil.example/a.PDF"},
])
def test_szse_rejects_invalid_fields(change):
    with pytest.raises(CninfoAnnouncementError, match="校验失败"):
        run(Session(Response({"announceCount": 1, "data": [{**sz_row(), **change}]})), "300308.SZ")


def test_truncation_never_returns_partial_success():
    diag = {}
    with pytest.raises(CninfoAnnouncementError, match="截断"):
        run(Session(sh_response(total=100, pages=2)), max_pages=1, diagnostics=diag)
    assert diag["truncated"] and diag["pages_fetched"] == 1


@pytest.mark.parametrize("payload", [None, {}, {"announceCount": "0", "data": []},
                                      {"announceCount": 0, "data": None},
                                      {"announceCount": 2, "data": [sz_row()]}])
def test_bad_pagination_cannot_be_reported_as_empty_success(payload):
    with pytest.raises(CninfoAnnouncementError):
        run(Session(Response(payload)), "300308.SZ")


def test_duplicate_pages_fail():
    session = Session(sh_response(total=2, pages=2, size=1),
                      sh_response(total=2, pages=2, page=2, size=1))
    with pytest.raises(CninfoAnnouncementError, match="重复"):
        run(session, page_size=1)


def test_provider_total_change_fails():
    session = Session(sh_response(total=2, pages=2, size=1),
                      sh_response([sh_row(2)], total=3, pages=3, page=2, size=1))
    with pytest.raises(CninfoAnnouncementError, match="总数.*变化"):
        run(session, page_size=1)


def test_retry_after_is_retained_and_no_burst_retry_or_body_leak():
    session = Session(Response({"secret": "do-not-show"}, 429, {"Retry-After": "120"}))
    service = ExchangeAnnouncementService(session)
    for _ in range(2):
        with pytest.raises(CninfoAnnouncementError) as raised:
            service.fetch(symbol="603306.SH", start_date=START, end_date=END)
        assert raised.value.retry_after_seconds > 119
        assert raised.value.endpoint == "sse.announcements"
        assert "do-not-show" not in str(raised.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize("status", [301, 403, 504])
def test_http_error_keeps_source_and_status_without_following_redirects(status):
    with pytest.raises(CninfoAnnouncementError) as raised:
        run(Session(Response({}, status)))
    assert raised.value.status_code == status and raised.value.source_key == "sse.announcements"
    assert raised.value.attempts == 1


@pytest.mark.parametrize("exception", [requests.Timeout("secret"), requests.ConnectionError("secret")])
def test_network_errors_do_not_leak_exception_text_or_retry(exception):
    session = Session(exception)
    with pytest.raises(CninfoAnnouncementError) as raised:
        run(session)
    assert "secret" not in str(raised.value) and len(session.calls) == 1


def test_deadline_or_empty_budget_never_queries():
    for kwargs in ({"deadline_monotonic": time.monotonic() - 1}, {"request_budget": {"remaining": 0}}):
        session = Session()
        with pytest.raises(CninfoAnnouncementError, match="预算"):
            run(session, **kwargs)
        assert session.calls == []


def test_empty_page_is_valid_only_with_zero_total():
    assert run(Session(sh_response([], total=0, pages=0))) == []
    assert run(Session(Response({"announceCount": 0, "data": []})), "300308.SZ") == []


@pytest.mark.parametrize("extension", ["pdf", "doc", "docx", "txt", "html", "htm", "xls", "xlsx"])
@pytest.mark.parametrize("exchange", ["SH", "SZ"])
def test_known_attachment_types_keep_real_type_and_never_disguise_non_pdf(extension, exchange):
    if exchange == "SH":
        raw = sh_row()
        raw["URL"] = raw["URL"].rsplit(".", 1)[0] + "." + extension
        response, symbol = sh_response([raw]), "603306.SH"
    else:
        raw = sz_row()
        raw["attachPath"] = raw["attachPath"].rsplit(".", 1)[0] + "." + extension
        raw["attachFormat"] = extension.upper()
        response, symbol = Response({"announceCount": 1, "data": [raw]}), "300308.SZ"
    session = Session(response)
    row = run(session, symbol)[0]
    assert row["file_type"] == extension.upper()
    assert row["original_url"].endswith("." + extension)
    assert row["pdf_url"] == (row["original_url"] if extension == "pdf" else None)
    assert len(session.calls) == 1  # Metadata only; no file fetch or type sniff download.


@pytest.mark.parametrize("exchange", ["SH", "SZ"])
def test_non_pdf_announcement_is_counted_in_complete_mixed_pagination(exchange):
    if exchange == "SH":
        non_pdf = {**sh_row(2), "URL": "/disclosure/listedinfo/announcement/c/new/2026-09-08/file2.docx"}
        session = Session(sh_response(total=2, pages=2, size=1),
                          sh_response([non_pdf], total=2, pages=2, page=2, size=1))
        symbol = "603306.SH"
    else:
        non_pdf = {**sz_row(2), "attachPath": "/disc/disk03/finalpage/2026-09-04/file2.docx",
                   "attachFormat": "DOCX"}
        session = Session(Response({"announceCount": 2, "data": [sz_row()]}),
                          Response({"announceCount": 2, "data": [non_pdf]}))
        symbol = "300308.SZ"
    diag = {}
    rows = run(session, symbol, page_size=1, diagnostics=diag)
    assert len(rows) == diag["total_count"] == 2 and diag["pages_fetched"] == 2
    assert diag["truncated"] is False
    assert sorted(row["file_type"] for row in rows) == ["DOCX", "PDF"]
    assert sum(row["pdf_url"] is not None for row in rows) == 1


@pytest.mark.parametrize("extension,declared", [("txt", "PDF"), ("pdf", "TXT"), ("exe", "EXE"),
                                              ("doc", ""), ("doc", None), ("html", "link")])
def test_szse_rejects_unknown_types_and_extension_format_mismatch(extension, declared):
    raw = {**sz_row(), "attachPath": "/disc/disk03/finalpage/2026-09-04/file." + extension,
           "attachFormat": declared}
    with pytest.raises(CninfoAnnouncementError, match="校验失败"):
        run(Session(Response({"announceCount": 1, "data": [raw]})), "300308.SZ")


@pytest.mark.parametrize("exchange,path", [
    ("SH", "/disclosure/listedinfo/announcement/c/new/file.docx"),
    ("SZ", "/disc/disk03/finalpage/file.txt"),
])
def test_pdf_only_validator_remains_strict(exchange, path):
    with pytest.raises(ValueError, match="file type"):
        ExchangeAnnouncementService._pdf_url(exchange, path)
