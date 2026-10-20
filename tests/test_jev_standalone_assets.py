"""The isolated Jev release must preserve the existing SPA and authentication."""
from unittest.mock import Mock

from fastapi.testclient import TestClient


def test_independent_bundle_keeps_original_index_and_requires_auth(tmp_path, monkeypatch):
    from api.app import create_app
    from api.middlewares import auth
    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")
    monkeypatch.delenv("JEV_FRONTEND_DIR", raising=False)
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_session", lambda value: value == "test-admin")
    users = Mock()
    users.return_value.account_for_session.return_value = None
    monkeypatch.setattr(auth, "UserAccountService", users)
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("original site")
    jev = root / "jev-backtest"
    (jev / "assets").mkdir(parents=True)
    (jev / "jev-backtest.html").write_text("independent Jev page")
    (jev / "assets" / "main.js").write_text("// independent")
    client = TestClient(create_app(static_dir=root), follow_redirects=False)
    assert client.get("/").text == "original site"
    assert client.get("/jev-backtest").status_code == 307
    client.cookies.set(auth.COOKIE_NAME, "test-admin")
    page = client.get("/jev-backtest")
    assert page.status_code == 410 and "independent Jev page" not in page.text
    assert "no-store" in page.headers["cache-control"]
    asset = client.get("/jev-backtest-assets/assets/main.js")
    assert asset.status_code == 410 and "// independent" not in asset.text
    assert client.get("/jev-backtest-assets/assets/missing.js").status_code == 410
    # Canonical traversal to the ordinary shared index is not a Jev identity.
    ordinary_index = client.get("/jev-backtest-assets/%2e%2e/index.html")
    assert ordinary_index.status_code == 200 and ordinary_index.text == "original site"
    assert (root / "index.html").read_text() == "original site"
