# -*- coding: utf-8 -*-
"""CNInfo query normalization and investment-monitor integration tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path
import threading
import time
import zipfile

import pytest

from src.config import Config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.cninfo_announcement_service import CninfoAnnouncementError, CninfoAnnouncementService
from src.services.announcement_artifact_service import (
    AnnouncementArtifactError,
    AnnouncementArtifactService,
    _PdfExtractionFailure,
)
from src.services.investment_monitor_service import InvestmentMonitorService
from src.services.watchlist_announcement_sync_worker import WatchlistAnnouncementSyncWorker
from src.storage import DatabaseManager


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakePdfResponse:
    headers = {}

    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield self.content


class FakePdfSession:
    def __init__(self, content):
        self.content = content

    def get(self, *args, **kwargs):
        del args, kwargs
        return FakePdfResponse(self.content)


class _ConcurrentPdfResponse(FakePdfResponse):
    def __init__(self, content, barrier):
        super().__init__(content)
        self.barrier = barrier

    def iter_content(self, chunk_size):
        del chunk_size
        split = max(4, len(self.content) // 2)
        yield self.content[:split]
        self.barrier.wait(timeout=2)
        yield self.content[split:]


class _ConcurrentPdfSession:
    def __init__(self, content, barrier):
        self.content = content
        self.barrier = barrier

    def get(self, *args, **kwargs):
        del args, kwargs
        return _ConcurrentPdfResponse(self.content, self.barrier)


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "topSearch" in url:
            return FakeResponse([{"code": "600519", "orgId": "gssh0600519", "zwjc": "贵州茅台"}])
        page = int(kwargs["data"]["pageNum"])
        rows = [{
            "secCode": "600519", "secName": "贵州茅台", "orgId": "gssh0600519",
            "announcementId": "1222993920", "announcementTitle": "贵州茅台2024年年度报告",
            "announcementTime": 1743609600000,
            "adjunctUrl": "finalpage/2025-04-03/1222993920.PDF",
            "adjunctSize": 3542, "adjunctType": "PDF",
        }] if page == 1 else []
        return FakeResponse({"announcements": rows, "totalpages": 1})


def test_cninfo_resolves_org_id_and_normalizes_https_pdf():
    session = FakeSession()
    service = CninfoAnnouncementService(session=session, request_interval=0)
    rows = service.fetch(
        start_date=date(2025, 1, 1), end_date=date(2025, 12, 31), symbols=["600519.SH"],
        categories=["category_ndbg_szsh"],
    )
    assert len(rows) == 1
    assert rows[0]["category_names"] == ["年报"]
    assert rows[0]["pdf_url"] == "https://static.cninfo.com.cn/finalpage/2025-04-03/1222993920.PDF"
    query_payload = next(kwargs["data"] for url, kwargs in session.calls if "hisAnnouncement" in url)
    assert query_payload["stock"] == "600519,gssh0600519"


def test_cninfo_rejects_unknown_category():
    with pytest.raises(CninfoAnnouncementError, match="未知公告分类"):
        CninfoAnnouncementService(session=FakeSession()).fetch(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 2), categories=["unknown"],
        )


def test_cninfo_exposes_pagination_truncation_and_shared_request_budget():
    class _PagedSession(FakeSession):
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if "topSearch" in url:
                return FakeResponse([{"code": "600519", "orgId": "gssh0600519", "zwjc": "贵州茅台"}])
            page = int(kwargs["data"]["pageNum"])
            return FakeResponse({
                "announcements": [{
                    "secCode": "600519", "secName": "贵州茅台", "orgId": "gssh0600519",
                    "announcementId": f"A{page}", "announcementTitle": f"第{page}页公告",
                    "announcementTime": 1743609600000 + page,
                    "adjunctUrl": f"finalpage/{page}.PDF",
                }],
                "totalpages": 5,
            })

    diagnostics = {}
    budget = {"remaining": 10, "used": 0}
    service = CninfoAnnouncementService(session=_PagedSession(), request_interval=0)

    rows = service.fetch(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        symbols=["600519.SH"],
        categories=["category_ndbg_szsh"],
        max_pages=2,
        deadline_monotonic=time.monotonic() + 5,
        request_budget=budget,
        diagnostics=diagnostics,
    )

    assert len(rows) == 2
    assert diagnostics["truncated"] is True
    assert diagnostics["pages_fetched"] == 2
    assert diagnostics["total_pages"] == 5
    assert diagnostics["request_attempts"] == 3  # security lookup + two announcement pages
    assert budget == {"remaining": 7, "used": 3}


def test_concurrent_same_announcement_downloads_publish_atomically(tmp_path):
    content = b"%PDF-1.7\n" + (b"same-cninfo-object\n" * 128)
    barrier = threading.Barrier(2)
    event = {
        "external_id": "same-announcement",
        "symbols": ["603306.SH"],
        "url": "https://static.cninfo.com.cn/finalpage/same.PDF",
    }

    def download_once():
        service = AnnouncementArtifactService(
            session=_ConcurrentPdfSession(content, barrier),
        )
        service.root = tmp_path
        return service.download_pdf(event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: download_once(), range(2)))

    targets = {path for path, _cached in results}
    assert len(targets) == 1
    target = targets.pop()
    assert target.read_bytes() == content
    assert not list(target.parent.glob("*.part"))


class FakeCninfo:
    @staticmethod
    def categories():
        return [{"code": "category_ndbg_szsh", "name": "年报"}]

    @staticmethod
    def fetch(**kwargs):
        del kwargs
        return [{
            "announcement_id": "ann-1", "code": "600519", "name": "贵州茅台",
            "title": "贵州茅台年度报告", "announcement_at": datetime(2026, 8, 19, 0, 0),
            "pdf_url": "https://static.cninfo.com.cn/finalpage/a.PDF",
            "category_names": ["年报"], "category_codes": ["category_ndbg_szsh"],
            "size_kb": 123, "file_type": "PDF", "org_id": "org", "raw": {"announcementId": "ann-1"},
        }]

    @staticmethod
    def fetch_recent(symbols, days=2):
        del days
        return FakeCninfo.fetch() if symbols else []

    @staticmethod
    def fetch_recent_market(days=2, max_pages=30):
        del days, max_pages
        return FakeCninfo.fetch()


def test_manual_announcement_sync_is_idempotent_and_queryable(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "announcements.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    try:
        repo = InvestmentMonitorRepository(DatabaseManager.get_instance())
        service = InvestmentMonitorService(repository=repo, cninfo=FakeCninfo())
        payload = {"start_date": "2026-08-18", "end_date": "2026-08-19", "categories": []}
        assert service.sync_announcements(payload)["created"] == 1
        assert service.sync_announcements(payload)["created"] == 0
        result = service.list_announcements(days=30, symbol="600519")
        assert result["total"] == 1
        assert result["items"][0]["perspective"] == "company"
        assert result["items"][0]["url"].endswith("a.PDF")
        exact = service.list_announcements(
            start_date="2026-08-19", end_date="2026-08-19", category="category_ndbg_szsh",
        )
        assert exact["total"] == 1
        assert service.export_announcements(start_date="2026-08-19", end_date="2026-08-19")[:2] == b"PK"
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def test_automatic_announcement_source_queries_watchlist_explicitly(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "automatic-announcements.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    try:
        repo = InvestmentMonitorRepository(DatabaseManager.get_instance())
        service = InvestmentMonitorService(repository=repo, cninfo=FakeCninfo())
        service.equity_watchlist = lambda: ["600519.SH"]

        result = service.sync_source("cninfo.announcements")

        assert result["received"] == 1
        assert service.list_announcements(days=3650)["total"] == 1
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def test_generic_due_loop_leaves_cninfo_to_durable_watchlist_worker(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "cninfo-dedicated-worker.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    try:
        repo = InvestmentMonitorRepository(DatabaseManager.get_instance())
        service = InvestmentMonitorService(repository=repo, cninfo=FakeCninfo())
        cninfo_source = repo.get_source("cninfo.announcements")
        repo.due_sources = lambda: [cninfo_source]

        result = service.sync_due_sources()

        assert result["sources"] == []
        assert result["totals"].get("sources", 0) == 0
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


class WindowCninfo:
    def __init__(self):
        self.calls = []
        self.fail_once = False

    @staticmethod
    def categories():
        return []

    def fetch(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_once:
            self.fail_once = False
            raise CninfoAnnouncementError("temporary upstream failure")
        start = kwargs["start_date"]
        end = kwargs["end_date"]
        announcement_id = f"ann-{start.isoformat()}-{end.isoformat()}"
        return [{
            "announcement_id": announcement_id, "code": "600519", "name": "贵州茅台",
            "title": "贵州茅台公告", "announcement_at": datetime.combine(end, datetime.min.time()),
            "pdf_url": f"https://static.cninfo.com.cn/finalpage/{announcement_id}.PDF",
            "category_names": [], "category_codes": [], "size_kb": 1,
            "file_type": "PDF", "org_id": "org", "raw": {"announcementId": announcement_id},
        }]


def test_watchlist_announcement_worker_backfills_one_year_in_durable_windows(tmp_path, monkeypatch):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "watchlist-announcements.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    try:
        repo = InvestmentMonitorRepository(DatabaseManager.get_instance())
        cninfo = WindowCninfo()
        service = InvestmentMonitorService(repository=repo, cninfo=cninfo)
        worker = WatchlistAnnouncementSyncWorker(repository=repo, service=service)
        worker.max_windows_per_cycle = 24
        worker._watchlist_symbols = lambda: ["600519.SH"]

        result = worker.run_once()
        state = worker.status()["symbols"][0]

        assert result["status"] == "success"
        assert state["history_progress"] == 100.0
        assert state["status"] == "live"
        assert date.fromisoformat(state["target_end"]) - date.fromisoformat(state["target_start"]) == timedelta(days=365)
        assert len(result["backfill"]) >= 12
        assert all(call["symbols"] == ["600519.SH"] for call in cninfo.calls)
        assert service.list_announcements(days=3650, symbol="600519.SH")["total"] >= 12
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def test_watchlist_announcement_worker_keeps_failed_window_for_retry(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "watchlist-announcement-retry.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    try:
        repo = InvestmentMonitorRepository(DatabaseManager.get_instance())
        cninfo = WindowCninfo()
        service = InvestmentMonitorService(repository=repo, cninfo=cninfo)
        worker = WatchlistAnnouncementSyncWorker(repository=repo, service=service)
        worker.max_windows_per_cycle = 1
        worker._watchlist_symbols = lambda: ["600519.SH"]
        cninfo.fail_once = True

        first = worker.run_once()
        state = repo.list_announcement_sync_states(["600519.SH"])[0]

        assert first["incremental"][0]["status"] == "failed"
        assert state["status"] == "retry"
        assert state["completed_windows"] == []
        assert state["next_retry_at"] is not None

        repo.update_announcement_sync_state("600519.SH", next_retry_at=None)
        second = worker.run_once()
        assert second["incremental"][0]["status"] == "success"
        assert second["backfill"][0]["status"] == "success"
        assert len(repo.list_announcement_sync_states(["600519.SH"])[0]["completed_windows"]) == 1
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def test_package_uses_persisted_event_and_contains_pdf_txt_and_excel(tmp_path):
    from pypdf import PdfWriter

    buffer = BytesIO()
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.write(buffer)
    artifacts = AnnouncementArtifactService(session=FakePdfSession(buffer.getvalue()))
    artifacts.root = tmp_path / "cache"; artifacts.root.mkdir()
    event = {
        "id": 7, "external_id": "ann-7", "title": "年度报告", "event_at": "2026-08-19T00:00:00Z",
        "symbols": ["600519.SH"], "actors": ["贵州茅台"], "tags": ["公司公告", "年报"],
        "url": "https://static.cninfo.com.cn/finalpage/a.PDF", "metrics": {"size_kb": 1},
    }
    result = artifacts.package([event], include_text=True)
    try:
        with zipfile.ZipFile(result["path"]) as archive:
            names = archive.namelist()
            assert "上市公司公告索引.xlsx" in names
            assert any(name.startswith("PDF/") for name in names)
            assert any(name.startswith("TXT/") for name in names)
            assert "manifest.json" in names
        assert result["downloaded"] == 1
    finally:
        result["path"].unlink(missing_ok=True)


def test_announcement_text_cache_is_hash_bound_atomic_and_self_healing(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    def pdf_bytes(marker: str) -> bytes:
        buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_metadata({"/Subject": marker})
        writer.write(buffer)
        return buffer.getvalue()

    pdf_path = tmp_path / "announcement.pdf"
    pdf_path.write_bytes(pdf_bytes("first-version"))
    artifacts = AnnouncementArtifactService()
    original_extract = artifacts._extract_text_audited
    extract_calls = 0

    def counted_extract(path):
        nonlocal extract_calls
        extract_calls += 1
        return original_extract(path)

    monkeypatch.setattr(artifacts, "_extract_text_audited", counted_extract)

    first = artifacts.extract_text_cached(pdf_path)
    second = artifacts.extract_text_cached(pdf_path)

    assert first["cached"] is False
    assert second["cached"] is True
    assert extract_calls == 1
    assert first["document_hash"] == second["document_hash"]
    assert first["text_hash"] == second["text_hash"]
    assert first["extraction_complete"] is True
    assert first["pages_extracted"] == first["page_count"] == 1
    assert first["extraction_method"] in {"pdfium_text", "pypdf_text_fallback"}
    assert first["extraction_engine_version"]
    first_cache_path = Path(first["cache_path"])
    assert first_cache_path.is_file()

    pdf_path.write_bytes(pdf_bytes("second-version"))
    changed = artifacts.extract_text_cached(pdf_path)

    assert changed["cached"] is False
    assert changed["document_hash"] != first["document_hash"]
    assert extract_calls == 2
    assert first_cache_path.is_file()  # Historical cache data is not deleted.

    changed_cache_path = Path(changed["cache_path"])
    changed_cache_path.write_text("{broken", encoding="utf-8")
    repaired = artifacts.extract_text_cached(pdf_path)

    assert repaired["cached"] is False
    assert extract_calls == 3
    assert repaired["document_hash"] == changed["document_hash"]
    assert not list(tmp_path.glob("*.part"))


def test_announcement_text_cache_atomic_write_preserves_previous_file_on_failure(tmp_path, monkeypatch):
    cache_path = tmp_path / "announcement.pdf.hash.text.json"
    cache_path.write_text('{"previous":true}', encoding="utf-8")

    def fail_fsync(_file_descriptor):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("src.services.announcement_artifact_service.os.fsync", fail_fsync)
    with pytest.raises(AnnouncementArtifactError, match="缓存写入失败"):
        AnnouncementArtifactService._write_text_cache(cache_path, {"new": True})

    assert cache_path.read_text(encoding="utf-8") == '{"previous":true}'
    assert not list(tmp_path.glob("*.part"))


def test_pdf_extraction_fallback_is_bounded_and_auditable(tmp_path, monkeypatch):
    pdf_path = tmp_path / "announcement.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    calls = []

    def fake_run(cls, path, *, engine, timeout):
        calls.append((engine, timeout))
        if engine == "pdfium":
            raise _PdfExtractionFailure("parse_failed", "simulated PDFium failure")
        return {
            "text": "完整正文",
            "page_count": 2,
            "pages_extracted": 2,
            "extraction_complete": True,
            "extraction_status": "complete",
            "extraction_method": "pypdf_text_fallback",
            "extraction_engine_version": "pypdf=test",
            "duration_ms": 7,
        }

    monkeypatch.setattr(AnnouncementArtifactService, "_run_text_extractor", classmethod(fake_run))

    parsed = AnnouncementArtifactService._extract_text_audited(pdf_path)

    assert calls == [
        ("pdfium", AnnouncementArtifactService._PDFIUM_TIMEOUT_SECONDS),
        ("pypdf", AnnouncementArtifactService._PYPDF_FALLBACK_TIMEOUT_SECONDS),
    ]
    assert parsed["fallback_reason"] == "parse_failed"
    assert parsed["extraction_complete"] is True
    assert parsed["pages_extracted"] == parsed["page_count"]


def test_pdf_extraction_timeout_is_explicit_and_does_not_leave_partial_result(tmp_path, monkeypatch):
    import src.services.announcement_artifact_service as module

    pdf_path = tmp_path / "announcement.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    real_named_temporary_file = module.tempfile.NamedTemporaryFile

    def local_named_temporary_file(**kwargs):
        return real_named_temporary_file(dir=tmp_path, **kwargs)

    def timeout_run(*_args, **_kwargs):
        raise module.subprocess.TimeoutExpired(cmd="pdf-worker", timeout=1)

    monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", local_named_temporary_file)
    monkeypatch.setattr(module.subprocess, "run", timeout_run)

    with pytest.raises(_PdfExtractionFailure, match="超时") as failure:
        AnnouncementArtifactService._run_text_extractor(pdf_path, engine="pdfium", timeout=1)

    assert failure.value.code == "timeout"
    assert not list(tmp_path.glob("*.extract.json"))


def test_pdf_extraction_rejects_partial_page_result():
    payload = {
        "text": "只有第一页",
        "page_count": 2,
        "pages_extracted": 1,
        "extraction_complete": True,
        "extraction_status": "complete",
        "extraction_method": "pdfium_text",
        "extraction_engine_version": "pypdfium2=test",
    }

    with pytest.raises(_PdfExtractionFailure, match="完整性") as failure:
        AnnouncementArtifactService._validate_extraction_payload(payload, engine="pdfium")

    assert failure.value.code == "incomplete"


def test_pdfium_worker_extracts_every_page_when_dependency_is_available(tmp_path):
    pytest.importorskip("pypdfium2")
    from pypdf import PdfWriter

    pdf_path = tmp_path / "announcement.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with pdf_path.open("wb") as output:
        writer.write(output)

    parsed = AnnouncementArtifactService._extract_text_audited(pdf_path)

    assert parsed["extraction_method"] == "pdfium_text"
    assert parsed["page_count"] == parsed["pages_extracted"] == 1
    assert parsed["extraction_complete"] is True
    assert parsed["extraction_status"] == "complete_no_selectable_text"
