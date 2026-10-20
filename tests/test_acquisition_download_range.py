"""Real FileResponse/GZip HTTP contract, with local ZIPs and real owner checks."""

import asyncio
from io import BytesIO
import json
from types import SimpleNamespace
import zipfile

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import httpx
import pytest
from starlette.middleware.gzip import GZipMiddleware

from api.v1.endpoints import data_acquisition as endpoint
from src.request_identity import reset_current_user_id, set_current_user_id
from src.services.data_acquisition_service import DataAcquisitionService


JOB = "20261018T000000Z-a1b2c3d4e5"
MISSING_JOB = "20261018T000000Z-f1b2c3d4e5"
URL = f"/acquisition/jobs/{JOB}/download"


@pytest.fixture
def package(tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.data_acquisition_service.get_config",
                        lambda: SimpleNamespace(database_path=str(tmp_path / "unused.db")))
    monkeypatch.setattr("requests.sessions.Session.request", lambda *args, **kwargs: pytest.fail("unexpected upstream"))
    service = DataAcquisitionService(planner=SimpleNamespace(model="offline"), financial=object(),
                                     monitor=object(), cninfo=object(), output_root=tmp_path / "packages")
    directory = service.output_root / JOB
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({"job_id": JOB, "owner_id": "user:11"}))
    memory = BytesIO()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("research.txt", bytes(range(256)) * 1025)
    content = memory.getvalue()
    path = directory / f"{JOB}.zip"
    path.write_bytes(content)
    monkeypatch.setattr(endpoint, "DataAcquisitionService", lambda: service)
    app = FastAPI()
    # Use the same compression layer as production. The test identity boundary
    # is explicit and offline; actual ownership uses the unmocked package service.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

    @app.middleware("http")
    async def test_identity(request: Request, call_next):
        supplied = request.headers.get("x-test-user")
        if supplied is None:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        token = set_current_user_id(int(supplied))
        try:
            return await call_next(request)
        finally:
            reset_current_user_id(token)

    app.include_router(endpoint.router, prefix="/acquisition")
    with TestClient(app) as client:
        yield client, content, path


def request_headers(**extra):
    return {"x-test-user": "11", "Accept-Encoding": "gzip", **extra}


def test_zip_full_response_is_identity_encoded_and_length_is_actual_bytes(package):
    client, content, _ = package
    response = client.get(URL, headers=request_headers())
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-encoding"] == "identity"
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"].startswith('"')
    assert response.headers["content-type"] == "application/zip"
    assert "no-transform" in response.headers["cache-control"]
    assert "private" in response.headers["cache-control"]
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.parametrize(("value", "start", "end"), [
    ("bytes=0-0", 0, 0), ("bytes=137-456", 137, 456),
    ("bytes=65530-65548", 65530, 65548), ("bytes=-73", -73, None),
    ("bytes=1234-", 1234, None),
])
def test_single_range_reuses_native_response_without_gzip(package, value, start, end):
    client, content, _ = package
    first = start if start >= 0 else len(content) + start
    last = len(content) - 1 if end is None else end
    response = client.get(URL, headers=request_headers(Range=value))
    assert response.status_code == 206
    assert response.content == content[first:last + 1]
    assert response.headers["content-range"] == f"bytes {first}-{last}/{len(content)}"
    assert response.headers["content-length"] == str(last - first + 1)
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"]
    assert response.headers["content-encoding"] == "identity"


def test_etag_if_range_continues_only_the_same_archive(package):
    client, content, path = package
    original = client.head(URL, headers=request_headers())
    resumed = client.get(URL, headers=request_headers(Range="bytes=65536-", **{"If-Range": original.headers["etag"]}))
    assert resumed.status_code == 206
    assert resumed.content == content[65536:]
    # Replacement is not a production operation; this fixture simulates a changed
    # artifact to prove stale If-Range never appends bytes from a new object.
    changed = content + b"new-version"
    path.write_bytes(changed)
    restarted = client.get(URL, headers=request_headers(Range="bytes=65536-", **{"If-Range": original.headers["etag"]}))
    assert restarted.status_code == 200
    assert restarted.content == changed
    assert "content-range" not in restarted.headers
    assert restarted.headers["etag"] != original.headers["etag"]


def test_head_supports_safe_resume_probe_without_body(package):
    client, content, _ = package
    response = client.head(URL, headers=request_headers())
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"]
    ranged = client.head(URL, headers=request_headers(Range="bytes=7-13"))
    assert ranged.status_code == 206
    assert ranged.content == b""
    assert ranged.headers["content-range"] == f"bytes 7-13/{len(content)}"
    assert ranged.headers["content-length"] == "7"


def test_unsatisfiable_and_malformed_range_use_native_errors(package):
    client, content, _ = package
    response = client.get(URL, headers=request_headers(Range=f"bytes={len(content)}-"))
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(content)}"
    assert response.headers["content-length"] == "0"
    assert client.get(URL, headers=request_headers(Range="bytes=invalid")).status_code == 400


@pytest.mark.parametrize("range_value", ["bytes=0-4", "bytes=999999999-", "bytes=invalid"])
def test_auth_and_owner_check_precede_range_and_do_not_leak_target(package, monkeypatch, range_value):
    client, _, _ = package
    monkeypatch.setattr(endpoint.FileResponse, "_parse_range_header",
                        lambda *args, **kwargs: pytest.fail("Range parsed before owner check"))
    anonymous = client.get(URL, headers={"Range": range_value})
    assert anonymous.status_code == 401
    denied = client.get(URL, headers={"x-test-user": "22", "Range": range_value})
    missing = client.get(URL.replace(JOB, MISSING_JOB), headers={"x-test-user": "22", "Range": range_value})
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
    for response in (anonymous, denied, missing):
        assert "content-range" not in response.headers
        assert "etag" not in response.headers
        assert "accept-ranges" not in response.headers


def test_two_http_ranges_reconstruct_a_crc_valid_zip(package):
    client, content, _ = package
    first = client.get(URL, headers=request_headers(Range="bytes=0-65535"))
    rest = client.get(URL, headers=request_headers(Range="bytes=65536-", **{"If-Range": first.headers["etag"]}))
    assert first.status_code == rest.status_code == 206
    combined = first.content + rest.content
    assert combined == content
    with zipfile.ZipFile(BytesIO(combined)) as archive:
        assert archive.testzip() is None


def test_server_file_reads_are_bounded_not_whole_archive(package, monkeypatch):
    client, content, _ = package
    sizes = []
    original = anyio.AsyncFile.read

    async def bounded_read(handle, size=-1):
        sizes.append(size)
        assert 0 < size <= endpoint.FileResponse.chunk_size
        return await original(handle, size)

    monkeypatch.setattr(anyio.AsyncFile, "read", bounded_read)
    assert client.get(URL, headers=request_headers()).content == content
    assert client.get(URL, headers=request_headers(Range="bytes=65536-131100")).content == content[65536:131101]
    assert len(sizes) > 2


def test_concurrent_range_readers_have_independent_offsets(package):
    client, content, _ = package

    async def concurrent_reads():
        # The repository's threadless TestClient intentionally owns a single
        # event loop; use ASGITransport directly for actual concurrent requests.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://testserver") as readers:
            async def read_range(index):
                start, end = index * 101, index * 101 + 100
                response = await readers.get(URL, headers=request_headers(Range=f"bytes={start}-{end}"))
                return response.status_code, response.content, content[start:end + 1]
            return await asyncio.gather(*(read_range(index) for index in range(12)))

    results = asyncio.run(concurrent_reads())
    assert all(status == 206 and actual == expected for status, actual, expected in results)
