"""Anonymous marketing images must not fall through to login or the SPA."""

from __future__ import annotations

import mimetypes
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def landing_client(tmp_path, monkeypatch):
    from api.app import create_app
    from api.middlewares import auth

    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    # Exercise the actual middleware and routing, without opening a user DB.
    user_service = Mock()
    user_service.return_value.account_for_session.return_value = None
    monkeypatch.setattr(auth, "UserAccountService", user_service)

    static_dir = tmp_path / "static"
    (static_dir / "landing" / "screens").mkdir(parents=True)
    (static_dir / "assets").mkdir()
    (static_dir / "assets" / "main.js").write_text("// app", encoding="utf-8")
    (static_dir / "index.html").write_text(
        '<html><script src="/assets/main.js"></script>private SPA</html>',
        encoding="utf-8",
    )
    client = TestClient(create_app(static_dir=static_dir), follow_redirects=False)
    return client, static_dir, user_service


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    ("relative_path", "media_type", "payload"),
    [
        ("screens/market-overview.jpg", "image/jpeg", b"\xff\xd8\xffmarket"),
        ("screens/concept-themes.jpg", "image/jpeg", b"\xff\xd8\xffconcept"),
        ("screens/super-watchlist-preview.png", "image/png", b"\x89PNG\r\n\x1a\npreview"),
        ("screens/industry-research.jpg", "image/jpeg", b"\xff\xd8\xffindustry"),
        ("screens/quant-workbench.jpg", "image/jpeg", b"\xff\xd8\xffquant"),
        ("screens/example.jpeg", "image/jpeg", b"\xff\xd8\xffjpeg"),
        ("intelligence-flow-v2.webp", "image/webp", b"RIFF\x00\x00\x00\x00WEBP"),
    ],
)
def test_landing_images_are_public_with_correct_mime_and_bounded_cache(
    landing_client, monkeypatch, method, relative_path, media_type, payload,
):
    client, static_dir, user_service = landing_client
    image_path = static_dir / "landing" / relative_path
    image_path.write_bytes(payload)
    # Image types must not depend on the host's MIME registry either.
    monkeypatch.setitem(mimetypes.types_map, image_path.suffix, "text/plain")

    response = client.request(method, f"/landing/{relative_path}?asset_audit=uncached")

    assert response.status_code == 200
    assert "location" not in response.headers
    assert response.headers["content-type"] == media_type
    assert response.headers["content-length"] == str(len(payload))
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == (payload if method == "GET" else b"")
    user_service.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    "relative_path",
    ["screens/missing.png", "screens/page.html", "screens/data.json", "screens/code.js", "screens/icon.svg"],
)
def test_missing_and_non_image_landing_assets_return_404_not_spa(landing_client, method, relative_path):
    client, static_dir, user_service = landing_client
    if not relative_path.endswith("missing.png"):
        (static_dir / "landing" / relative_path).write_text("not public", encoding="utf-8")

    response = client.request(method, f"/landing/{relative_path}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    assert b"private SPA" not in response.content
    assert b"not public" not in response.content
    if method == "HEAD":
        assert response.content == b""
    user_service.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    "request_path",
    [
        "/landing/%2e%2e/private.png",
        "/landing/%252e%252e/private.png",
        "/landing/%2e%2e/%2e%2e/private.png",
        "/landing/..%5Cprivate.png",
        "/landing/%2Fprivate.png",
        "/landing/C:%5Cprivate.png",
        "/landing/%2500.png",
        "/landing/screens/escape.png",
    ],
)
def test_landing_images_cannot_escape_marketing_directory(landing_client, method, request_path):
    client, static_dir, user_service = landing_client
    outside = static_dir.parent / "private.png"
    outside.write_bytes(b"private image outside static")
    (static_dir / "private.png").write_bytes(b"private image outside landing")
    (static_dir / "landing" / "screens" / "escape.png").symlink_to(outside)

    response = client.request(method, request_path)

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    assert b"private" not in response.content
    user_service.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("request_path", ["/app", "/guide", "/guide/", "/landing-private/example.png"])
def test_landing_exemption_does_not_open_business_pages(landing_client, method, request_path):
    client, _, user_service = landing_client

    response = client.request(method, request_path)

    assert response.status_code == 307
    assert response.headers["location"].startswith("/access?redirect=")
    assert response.headers["cache-control"] == "no-store"
    user_service.return_value.account_for_session.assert_called_once_with("")


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("request_path", ["/api/v1/stocks", "/api/v1/system/config"])
def test_landing_exemption_does_not_open_business_apis(landing_client, method, request_path):
    client, _, _ = landing_client

    response = client.request(method, request_path)

    assert response.status_code == 401
    assert "location" not in response.headers


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_landing_images_are_read_only(landing_client, method):
    client, static_dir, _ = landing_client
    image = static_dir / "landing" / "screens" / "example.png"
    image.write_bytes(b"unchanged image")

    response = client.request(method, "/landing/screens/example.png", content=b"replacement")

    assert response.status_code == 405
    assert image.read_bytes() == b"unchanged image"
