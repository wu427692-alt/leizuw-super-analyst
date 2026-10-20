"""Bounded attachment concurrency and budget accounting without network access."""

from __future__ import annotations

from collections import Counter
import threading
import time
from urllib.parse import urlparse

from src.request_identity import current_owner_id, reset_current_user_id, set_current_user_id
from src.services import data_acquisition_service as module
from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService


def _service():
    # _download_files only uses pure helpers: no database, planner, or network
    # service should be constructed by these concurrency tests.
    return object.__new__(DataAcquisitionService)


def _reports(count, *, host_count=1):
    return [{"title": f"report-{index}", "url": f"https://h{index % host_count}.example/{index}.pdf"}
            for index in range(count)]


def _task(source="tushare", resource="research_report"):
    return {"source": source, "resource": resource}


def test_parallel_downloads_are_faster_and_bounded_globally_and_per_host(tmp_path, monkeypatch):
    lock = threading.Lock()
    active = 0
    peak = 0
    by_host = Counter()
    host_peak = Counter()

    def fake_download(url, target, *, remaining):
        nonlocal active, peak
        host = urlparse(url).hostname
        with lock:
            active += 1
            peak = max(peak, active)
            by_host[host] += 1
            host_peak[host] = max(host_peak[host], by_host[host])
        try:
            time.sleep(.06)
            target.write_bytes(b"%PDF-test")
            assert remaining >= target.stat().st_size
            return target.stat().st_size
        finally:
            with lock:
                active -= 1
                by_host[host] -= 1

    monkeypatch.setattr(DataAcquisitionService, "_download_one", staticmethod(fake_download))
    rows = _reports(8, host_count=2)
    monkeypatch.setattr(module, "MAX_DOWNLOAD_WORKERS", 1)
    started = time.perf_counter()
    serial = _service()._download_files(tmp_path / "serial", _task(), rows, "reports")
    serial_seconds = time.perf_counter() - started

    peak = 0
    host_peak.clear()
    monkeypatch.setattr(module, "MAX_DOWNLOAD_WORKERS", 8)
    started = time.perf_counter()
    parallel = _service()._download_files(tmp_path / "parallel", _task(), rows, "reports")
    parallel_seconds = time.perf_counter() - started

    assert all(row["status"] == "success" for row in [*serial, *parallel])
    assert parallel_seconds < serial_seconds * .75
    assert peak == 8
    assert max(host_peak.values()) <= module.MAX_DOWNLOAD_PER_HOST == 4
    assert [row["name"] for row in parallel] == [f"report-{index}.pdf" for index in range(8)]


def test_failures_and_invalid_urls_do_not_abort_or_reorder_other_files(tmp_path, monkeypatch):
    calls = []
    updates = []
    callback_threads = []
    main_thread = threading.get_ident()

    def fake_download(url, target, *, remaining):
        calls.append(url)
        if "broken" in url:
            raise TimeoutError("fake timeout")
        time.sleep(.01 if "fast" in url else .04)
        target.write_bytes(b"%PDF-test")
        return target.stat().st_size

    def progress(update):
        callback_threads.append(threading.get_ident())
        updates.append(update)

    monkeypatch.setattr(DataAcquisitionService, "_download_one", staticmethod(fake_download))
    rows = [
        {"title": "insecure", "pdf_url": "http://static.cninfo.com.cn/insecure.pdf"},
        {"title": "slow", "pdf_url": "https://static.cninfo.com.cn/slow.pdf"},
        {"title": "duplicate", "pdf_url": "https://static.cninfo.com.cn/slow.pdf"},
        {"title": "broken", "pdf_url": "https://static.cninfo.com.cn/broken.pdf"},
        {"title": "fast", "pdf_url": "https://static.cninfo.com.cn/fast.pdf"},
        {"title": "untrusted", "pdf_url": "https://untrusted.example/other.pdf"},
    ]

    results = _service()._download_files(
        tmp_path, _task("cninfo", "announcements"), rows, "announcements", progress_callback=progress,
    )

    assert [item["name"] for item in results] == ["insecure.pdf", "slow.pdf", "broken.pdf", "fast.pdf", "untrusted.pdf"]
    assert [item["status"] for item in results] == ["failed", "success", "failed", "success", "failed"]
    assert len(calls) == 3
    assert all(url.startswith("https://static.cninfo.com.cn/") for url in calls)
    assert [item["completed_files"] for item in updates] == list(range(1, 6))
    assert all(item["total_files"] == 5 for item in updates)
    assert updates[-1]["fraction"] == 1
    assert updates[-1]["downloaded_bytes"] == 18
    assert set(callback_threads) == {main_thread}


def test_unique_file_limit_is_shared_between_channels(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "MAX_DOWNLOAD_FILES", 3)
    calls = []

    def fake_download(url, target, *, remaining):
        calls.append(url)
        target.write_bytes(b"a")
        return 1

    monkeypatch.setattr(DataAcquisitionService, "_download_one", staticmethod(fake_download))
    budget = {}
    rows = [_reports(1)[0], _reports(1)[0], _reports(2)[1]]
    first = _service()._download_files(tmp_path, _task(), rows, "first", download_budget=budget)
    second = _service()._download_files(tmp_path, _task(), _reports(3, host_count=2), "second", download_budget=budget)

    assert len(first) == 2
    assert len(calls) == 3
    assert budget["file_count"] == 3
    assert second[-1]["name"] == "download_limit"
    assert second[-1]["status"] == "failed"


def test_shared_budget_reservations_refund_unused_capacity_and_never_overshoot(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 8)
    monkeypatch.setattr(module, "MAX_PACKAGE_FILE_BYTES", 10)
    allowances = []
    budget = {}

    def fake_download(url, target, *, remaining):
        allowances.append(remaining)
        time.sleep(.015)
        if remaining < 3:
            raise DataAcquisitionError("原始文件超过大小上限")
        target.write_bytes(b"abc")
        return 3

    monkeypatch.setattr(DataAcquisitionService, "_download_one", staticmethod(fake_download))
    results = _service()._download_files(tmp_path, _task(), _reports(4, host_count=4), "files", download_budget=budget)

    assert sum(item.get("size_bytes", 0) for item in results) == 9
    assert sum(item["status"] == "success" for item in results) == 3
    assert sum(item["status"] == "failed" for item in results) == 1
    assert all(0 < allowance <= 8 for allowance in allowances)
    assert budget["remaining"] == 1
    assert budget["reserved"] == 0
    assert sum(path.stat().st_size for path in (tmp_path / "attachments").rglob("*.pdf")) == 9


def test_failed_file_releases_reservation_and_next_channel_uses_actual_remainder(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 8)
    monkeypatch.setattr(module, "MAX_PACKAGE_FILE_BYTES", 10)
    budget = {}

    def fake_download(url, target, *, remaining):
        time.sleep(.01)
        if "fail" in url:
            raise TimeoutError("fake timeout")
        if remaining < 3:
            raise DataAcquisitionError("原始文件超过大小上限")
        target.write_bytes(b"abc")
        return 3

    monkeypatch.setattr(DataAcquisitionService, "_download_one", staticmethod(fake_download))
    first_rows = [{"title": "fail", "url": "https://h0.example/fail.pdf"}, *_reports(2, host_count=2)]
    first = _service()._download_files(tmp_path, _task(), first_rows, "first", download_budget=budget)
    second = _service()._download_files(tmp_path, _task(), _reports(2, host_count=2), "second", download_budget=budget)

    assert sum(item.get("size_bytes", 0) for item in [*first, *second]) == 9
    assert first[0]["status"] == "failed"
    assert sum(item["status"] == "success" for item in [*first, *second]) == 3
    assert budget["remaining"] == 1
    assert budget["reserved"] == 0


def test_real_streaming_download_still_enforces_single_file_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 4)

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, *args):
            yield b"%PDF-test"

        def close(self):
            pass

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(module.requests, "Session", FakeSession)
    results = _service()._download_files(tmp_path, _task(), _reports(2, host_count=2), "files")

    assert [item["status"] for item in results] == ["failed", "failed"]
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.pdf"))


def test_media_resolution_and_progress_keep_request_identity_on_caller_thread(tmp_path, monkeypatch):
    from src.services import zsxq_mcp_sync_service

    main_thread = threading.get_ident()
    resolved = []
    worker_threads = []
    callback_owners = []

    class Resolver:
        def resolve_media_url_sync(self, topic_id, kind, asset_id):
            assert threading.get_ident() == main_thread
            assert current_owner_id() == "user:42"
            resolved.append((topic_id, kind, asset_id))
            return f"https://files.example/{asset_id}.bin"

    def fake_download(url, target, *, remaining):
        worker_threads.append(threading.get_ident())
        assert len(resolved) == 2
        target.write_bytes(b"audio")
        return 5

    monkeypatch.setattr(zsxq_mcp_sync_service, "ZsxqMcpSyncService", Resolver)
    monkeypatch.setattr(DataAcquisitionService, "_download_one", staticmethod(fake_download))
    token = set_current_user_id(42)
    try:
        results = _service()._download_files(
            tmp_path, _task("zsxq", "research_notes"),
            [{"topic_id": "topic", "files": [{"file_id": "one", "name": "one.mp3"},
                                              {"file_id": "two", "name": "two.mp3"}]}],
            "media", progress_callback=lambda update: callback_owners.append(current_owner_id()),
        )
    finally:
        reset_current_user_id(token)

    assert all(result["status"] == "success" for result in results)
    assert worker_threads and main_thread not in worker_threads
    assert callback_owners == ["user:42", "user:42"]


def test_progress_callback_failure_does_not_lose_download_results(tmp_path, monkeypatch):
    def fake_download(url, target, *, remaining):
        target.write_bytes(b"abc")
        return 3

    def broken_progress(update):
        raise RuntimeError("UI disconnected")

    monkeypatch.setattr(DataAcquisitionService, "_download_one", staticmethod(fake_download))
    results = _service()._download_files(tmp_path, _task(), _reports(2), "files", progress_callback=broken_progress)
    assert [result["status"] for result in results] == ["success", "success"]
