"""Upstream file resume/retry contract, using in-memory fake HTTP responses."""

from __future__ import annotations

import pytest
import requests

from src.services import data_acquisition_service as module
from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService


PDF = b"%PDF-1.7\nabcdefghij\n%%EOF"
URL = "https://files.example/report.pdf"


class FakeResponse:
    def __init__(self, chunks=(), *, status=200, headers=None):
        self.chunks = list(chunks)
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"fake HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    @property
    def content(self):
        raise AssertionError("Streaming downloads must not eagerly read response.content")

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.sleeps = []
        self.cookies = requests.cookies.RequestsCookieJar()
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs, "headers": dict(kwargs.get("headers") or {})})
        if not self.responses:
            raise AssertionError("Unexpected extra HTTP attempt")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


def install(monkeypatch, responses):
    session = FakeSession(responses)
    monkeypatch.setattr(module.requests, "Session", lambda: session)
    monkeypatch.setattr(module.time, "sleep", lambda delay: session.sleeps.append(delay))
    return session


def complete(data=PDF, **headers):
    return FakeResponse([data], headers={"Content-Length": str(len(data)), **headers})


def interrupted(*, headers=None):
    return FakeResponse([PDF[:10], requests.ConnectionError("fake stream disconnected")],
                        headers={"Content-Length": str(len(PDF)), **(headers or {})})


def resumed(*, headers=None, content_range=None):
    return FakeResponse([PDF[10:]], status=206, headers={
        "Content-Length": str(len(PDF) - 10),
        "Content-Range": content_range or f"bytes 10-{len(PDF) - 1}/{len(PDF)}",
        **(headers or {}),
    })


def assert_clean(target):
    assert not target.with_suffix(target.suffix + ".part").exists()


def test_strong_etag_resumes_only_missing_bytes_and_atomically_finishes(tmp_path, monkeypatch):
    first = interrupted(headers={"ETag": '"v1"'})
    second = resumed(headers={"ETag": '"v1"'})
    session = install(monkeypatch, [first, second])
    target = tmp_path / "report.pdf"

    assert DataAcquisitionService._download_one(URL, target, remaining=100) == len(PDF)
    assert target.read_bytes() == PDF
    assert "Range" not in session.calls[0]["headers"]
    assert session.calls[1]["headers"]["Range"] == "bytes=10-"
    assert session.calls[1]["headers"]["If-Range"] == '"v1"'
    assert session.calls[1]["headers"]["Accept-Encoding"] == "identity"
    assert session.sleeps == [.3]
    assert session.closed and first.closed and second.closed
    assert_clean(target)


@pytest.mark.parametrize("initial_headers", [{}, {"ETag": 'W/"weak"'}])
def test_without_trustworthy_validator_retries_from_zero(tmp_path, monkeypatch, initial_headers):
    session = install(monkeypatch, [interrupted(headers=initial_headers), complete()])
    target = tmp_path / "report.pdf"

    DataAcquisitionService._download_one(URL, target, remaining=100)

    assert target.read_bytes() == PDF
    assert all("Range" not in call["headers"] and "If-Range" not in call["headers"] for call in session.calls)
    assert_clean(target)


def test_last_modified_can_validate_range_when_etag_is_weak(tmp_path, monkeypatch):
    modified = "Sun, 18 Oct 2026 01:02:03 GMT"
    session = install(monkeypatch, [interrupted(headers={"ETag": 'W/"weak"', "Last-Modified": modified}),
                                    resumed(headers={"Last-Modified": modified})])
    target = tmp_path / "report.pdf"

    DataAcquisitionService._download_one(URL, target, remaining=100)

    assert target.read_bytes() == PDF
    assert session.calls[1]["headers"]["If-Range"] == modified


def test_server_200_fallback_restarts_file_instead_of_appending(tmp_path, monkeypatch):
    newer = b"%PDF-2.0\na-new-and-different-document\n%%EOF"
    session = install(monkeypatch, [interrupted(headers={"ETag": '"v1"'}), complete(newer, ETag='"v2"')])
    target = tmp_path / "report.pdf"

    assert DataAcquisitionService._download_one(URL, target, remaining=100) == len(newer)
    assert session.calls[1]["headers"]["Range"] == "bytes=10-"
    assert target.read_bytes() == newer
    assert_clean(target)


def test_changed_etag_on_206_is_rejected_not_spliced_or_retried(tmp_path, monkeypatch):
    session = install(monkeypatch, [interrupted(headers={"ETag": '"v1"'}), resumed(headers={"ETag": '"v2"'})])
    target = tmp_path / "report.pdf"

    with pytest.raises(DataAcquisitionError, match="标识已改变"):
        DataAcquisitionService._download_one(URL, target, remaining=100)

    assert len(session.calls) == 2
    assert not target.exists()
    assert_clean(target)


@pytest.mark.parametrize("range_header,extra", [
    (f"bytes 9-{len(PDF) - 1}/{len(PDF)}", {}),
    (f"bytes 10-{len(PDF) - 1}/{len(PDF) + 1}", {}),
    (f"bytes 10-{len(PDF) - 1}/*", {}),
    (f"bytes 10-{len(PDF) - 1}/{len(PDF)}", {"Content-Length": "1"}),
])
def test_malformed_or_inconsistent_content_range_is_not_accepted(tmp_path, monkeypatch, range_header, extra):
    session = install(monkeypatch, [interrupted(headers={"ETag": '"v1"'}),
                                    resumed(headers={"ETag": '"v1"', **extra}, content_range=range_header)])
    target = tmp_path / "report.pdf"

    with pytest.raises(DataAcquisitionError):
        DataAcquisitionService._download_one(URL, target, remaining=100)

    assert len(session.calls) == 2
    assert not target.exists()
    assert_clean(target)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 599])
def test_only_transient_http_statuses_retry(tmp_path, monkeypatch, status):
    session = install(monkeypatch, [FakeResponse(status=status), complete()])
    target = tmp_path / "report.pdf"

    DataAcquisitionService._download_one(URL, target, remaining=100)

    assert len(session.calls) == 2
    assert session.sleeps == [.3]
    assert target.read_bytes() == PDF


@pytest.mark.parametrize("status", [400, 401, 403, 404, 416])
def test_permanent_http_failures_do_not_retry(tmp_path, monkeypatch, status):
    session = install(monkeypatch, [FakeResponse(status=status)])
    target = tmp_path / "report.pdf"

    with pytest.raises(requests.HTTPError):
        DataAcquisitionService._download_one(URL, target, remaining=100)

    assert len(session.calls) == 1
    assert session.sleeps == []
    assert not target.exists()
    assert_clean(target)


def test_network_retries_are_bounded_and_partial_is_cleaned(tmp_path, monkeypatch):
    session = install(monkeypatch, [requests.Timeout("fake timeout") for _ in range(3)])
    target = tmp_path / "report.pdf"

    with pytest.raises(DataAcquisitionError, match="三次尝试"):
        DataAcquisitionService._download_one(URL, target, remaining=100)

    assert len(session.calls) == 3
    assert session.sleeps == [.3, .6]
    assert session.closed
    assert not target.exists()
    assert_clean(target)


def test_known_length_premature_eof_resumes_instead_of_counting_partial_success(tmp_path, monkeypatch):
    first = FakeResponse([PDF[:10]], headers={"Content-Length": str(len(PDF)), "ETag": '"v1"'})
    session = install(monkeypatch, [first, resumed(headers={"ETag": '"v1"'})])
    target = tmp_path / "report.pdf"

    DataAcquisitionService._download_one(URL, target, remaining=100)

    assert session.calls[1]["headers"]["Range"] == "bytes=10-"
    assert target.read_bytes() == PDF
    assert_clean(target)


def test_repeated_early_eof_leaves_existing_final_target_untouched(tmp_path, monkeypatch):
    responses = [FakeResponse([PDF[:10]], headers={"Content-Length": str(len(PDF))}) for _ in range(3)]
    session = install(monkeypatch, responses)
    target = tmp_path / "report.pdf"
    target.write_bytes(b"existing complete file")

    with pytest.raises(DataAcquisitionError, match="三次尝试"):
        DataAcquisitionService._download_one(URL, target, remaining=100)

    assert len(session.calls) == 3
    assert target.read_bytes() == b"existing complete file"
    assert_clean(target)


def test_empty_file_is_not_success_and_does_not_retry(tmp_path, monkeypatch):
    session = install(monkeypatch, [complete(b"")])
    target = tmp_path / "report.pdf"

    with pytest.raises(DataAcquisitionError, match="空文件"):
        DataAcquisitionService._download_one(URL, target, remaining=100)

    assert len(session.calls) == 1
    assert not target.exists()
    assert_clean(target)


def test_valid_small_eastmoney_pdf_never_invokes_challenge_solver(tmp_path, monkeypatch):
    session = install(monkeypatch, [complete()])

    def forbidden_solver(script):
        raise AssertionError("A valid small PDF is not a challenge")

    monkeypatch.setattr(DataAcquisitionService, "_solve_eastmoney_pdf_challenge", forbidden_solver)
    target = tmp_path / "report.pdf"

    DataAcquisitionService._download_one("https://pdf.dfcfw.com/report.pdf", target, remaining=100)

    assert target.read_bytes() == PDF
    assert len(session.calls) == 1


def test_only_recognizable_eastmoney_script_uses_existing_solver_once(tmp_path, monkeypatch):
    script = b"<script>EO_Bot_Ssid __tst_status</script>"
    session = install(monkeypatch, [complete(script), complete()])
    solved = []

    def solver(text):
        solved.append(text)
        return {"EO_Bot_Ssid": "test", "__tst_status": "test"}

    monkeypatch.setattr(DataAcquisitionService, "_solve_eastmoney_pdf_challenge", solver)
    target = tmp_path / "report.pdf"

    DataAcquisitionService._download_one("https://pdf.dfcfw.com/report.pdf", target, remaining=100)

    assert len(solved) == 1
    assert len(session.calls) == 2
    assert target.read_bytes() == PDF


def test_unrelated_html_is_not_retried_or_passed_to_solver(tmp_path, monkeypatch):
    session = install(monkeypatch, [complete(b"<html>access denied</html>")])

    def forbidden_solver(script):
        raise AssertionError("Unrelated HTML is not a supported challenge")

    monkeypatch.setattr(DataAcquisitionService, "_solve_eastmoney_pdf_challenge", forbidden_solver)
    target = tmp_path / "report.pdf"

    with pytest.raises(DataAcquisitionError, match="有效 PDF"):
        DataAcquisitionService._download_one("https://pdf.dfcfw.com/report.pdf", target, remaining=100)

    assert len(session.calls) == 1
    assert not target.exists()
    assert_clean(target)


def test_pdf_header_can_span_stream_chunks(tmp_path, monkeypatch):
    session = install(monkeypatch, [FakeResponse([b"%P", b"DF", PDF[4:]], headers={"Content-Length": str(len(PDF))})])
    target = tmp_path / "report.pdf"

    DataAcquisitionService._download_one(URL, target, remaining=100)

    assert len(session.calls) == 1
    assert target.read_bytes() == PDF


@pytest.mark.parametrize("known_length", [True, False])
def test_streaming_size_never_exceeds_reserved_budget(tmp_path, monkeypatch, known_length):
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 10)
    session = install(monkeypatch, [complete() if known_length else FakeResponse([PDF])])
    target = tmp_path / "report.pdf"

    with pytest.raises(DataAcquisitionError, match="大小上限"):
        DataAcquisitionService._download_one(URL, target, remaining=9)

    assert len(session.calls) == 1
    assert not target.exists()
    assert_clean(target)
