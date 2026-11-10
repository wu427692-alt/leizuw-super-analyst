"""Real isolated entry references resolve to modules/CSS, never SPA HTML."""

import mimetypes
from pathlib import Path
import re
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def frontend(tmp_path, monkeypatch):
    from api.app import create_app
    from api.middlewares import auth

    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    accounts = Mock()
    accounts.return_value.account_for_session.return_value = None
    monkeypatch.setattr(auth, "UserAccountService", accounts)
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>shared SPA</html>", encoding="utf-8")
    entries = {}
    for workspace, page in (("account-access", "account-access"), ("ai-news", "ai-news")):
        folder = root / workspace
        folder.mkdir()
        js_url = f"/assets/{workspace}/{page}-CSZezQ2S.js"
        css_url = f"/assets/{workspace}/{page}-Mno0123.css"
        document = f'<script type="module" src="{js_url}"></script><link rel="stylesheet" href="{css_url}">'
        (folder / f"{page}.html").write_text(document, encoding="utf-8")
        (folder / Path(js_url).name).write_text(f'export const workspace = "{workspace}";', encoding="utf-8")
        (folder / Path(css_url).name).write_text(f'.{workspace} {{ display: grid; }}', encoding="utf-8")
        entries[workspace] = (document, js_url, css_url)
    return TestClient(create_app(static_dir=root), follow_redirects=False), root, entries


@pytest.mark.parametrize("workspace", ["account-access", "ai-news"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_entry_references_use_the_corresponding_isolated_build_root(frontend, monkeypatch, workspace, method):
    client, root, entries = frontend
    document, js_url, css_url = entries[workspace]
    monkeypatch.setitem(mimetypes.types_map, ".js", "text/html")
    monkeypatch.setitem(mimetypes.types_map, ".css", "text/plain")
    assert set(re.findall(r'(?:src|href)="([^"]+)"', document)) == {js_url, css_url}
    assert client.get("/access").text == entries["account-access"][0]
    for url, media_type in ((js_url, "text/javascript"), (css_url, "text/css")):
        result = client.request(method, url)
        assert result.status_code == 200
        assert result.headers["content-type"].startswith(media_type)
        assert result.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert result.headers["x-content-type-options"] == "nosniff"
        assert result.content == ((root / workspace / Path(url).name).read_bytes() if method == "GET" else b"")
        assert b"shared SPA" not in result.content


@pytest.mark.parametrize("prefix", ["/assets", "/assets/account-access", "/assets/ai-news"])
@pytest.mark.parametrize("extension,content_type", [("js", "text/javascript"), ("css", "text/css")])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_missing_module_or_stylesheet_returns_uncached_404(frontend, prefix, extension, content_type, method):
    client, _, _ = frontend
    result = client.request(method, f"{prefix}/no-such-hash.{extension}")
    assert result.status_code == 404
    assert result.headers["content-type"].startswith(content_type)
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.content == (b"asset not found" if method == "GET" else b"")


@pytest.mark.parametrize("workspace", ["account-access", "ai-news"])
def test_conditional_requests_and_isolated_namespace_precedence(frontend, workspace):
    client, root, entries = frontend
    _, js_url, _ = entries[workspace]
    # A legacy copy cannot shadow the intended isolated release root.
    legacy = root / "assets" / workspace
    legacy.mkdir()
    (legacy / Path(js_url).name).write_text("stale legacy copy", encoding="utf-8")
    result = client.get(js_url)
    assert "stale" not in result.text
    cached = client.get(js_url, headers={"If-None-Match": result.headers["etag"]})
    assert cached.status_code == 304 and cached.content == b""
    assert cached.headers["etag"] == result.headers["etag"]


@pytest.mark.parametrize("workspace", ["account-access", "ai-news"])
def test_legacy_namespaced_asset_layout_still_resolves_without_other_root_fallback(frontend, workspace):
    client, root, _ = frontend
    legacy = root / "assets" / workspace / "assets"
    legacy.mkdir(parents=True)
    (legacy / "legacy.js").write_text("export const legacy = true;", encoding="utf-8")
    result = client.get(f"/assets/{workspace}/assets/legacy.js")
    assert result.status_code == 200 and result.text == "export const legacy = true;"
    (root / "assets" / "unrelated.js").write_text("not this workspace", encoding="utf-8")
    assert client.get(f"/assets/{workspace}/unrelated.js").status_code == 404


@pytest.mark.parametrize("workspace", ["account-access", "ai-news"])
@pytest.mark.parametrize("path", ["%2e%2e/private.js", "..%5cprivate.js", "%2500.js", "%2fprivate.js", "escape.js"])
def test_asset_namespace_never_escapes_its_build_root(frontend, workspace, path):
    client, root, _ = frontend
    (root / "private.js").write_text("private bytes", encoding="utf-8")
    (root / workspace / "escape.js").symlink_to(root / "private.js")
    result = client.get(f"/assets/{workspace}/{path}")
    assert result.status_code == 404
    assert b"private bytes" not in result.content


@pytest.mark.parametrize("workspace", ["account-access", "ai-news"])
def test_asset_namespace_is_not_an_alternative_html_document_entry(frontend, workspace):
    client, _, _ = frontend
    assert client.get(f"/assets/{workspace}/{workspace}.html").status_code == 404
