"""A withdrawn Jev bundle never bypasses shared login or serves old code."""

from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    from api.app import create_app
    from api.middlewares import auth

    monkeypatch.delenv("JEV_FRONTEND_DIR", raising=False)
    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    accounts = Mock()
    accounts.account_for_session.return_value = None
    monkeypatch.setattr(auth, "UserAccountService", lambda: accounts)
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>legacy main SPA</html>")
    (static / "jev-backtest" / "assets").mkdir(parents=True)
    (static / "jev-backtest" / "jev-backtest.html").write_text(
        '<html>Jev isolated login<script src="/jev-backtest-assets/assets/entry.js"></script></html>'
    )
    (static / "jev-backtest" / "assets" / "entry.js").write_text("// public login code")
    (static / "jev-backtest" / "assets" / "entry.css").write_text("/* public styles */")
    return TestClient(create_app(static_dir=static), follow_redirects=False), accounts, static


@pytest.mark.parametrize("target", [
    "/jev-backtest", "/jev-backtest/",
    "/jev-backtest?lab=convertible-t&round=sample&task=private&symbol=A%26B",
])
def test_anonymous_entry_keeps_shared_login_with_original_target(isolated_client, target):
    client, accounts, _ = isolated_client
    response = client.get(target)
    assert response.status_code == 307
    assert response.headers["location"] == "/access?redirect=" + quote(target, safe="")
    login = client.get(response.headers["location"])
    assert login.status_code == 200
    assert "Jev isolated login" not in login.text
    assert "no-store" in login.headers["cache-control"]
    assert "legacy main SPA" in login.text
    accounts.account_for_session.assert_called_once_with("")


@pytest.mark.parametrize("target", [
    "", "/app", "/jev-backtest-private", "/jev-backtest/task",
    "https://example.com/jev-backtest", "//example.com/jev-backtest",
    "///jev-backtest", "/\\example.com/jev-backtest", "/jev-backtest\n",
    "/jev-backtest%2f..%2fapp",
])
def test_other_access_targets_keep_existing_login_bundle(isolated_client, target):
    client, _, _ = isolated_client
    response = client.get("/access", params={"redirect": target})
    assert response.status_code == 200
    assert "legacy main SPA" in response.text
    assert "Jev isolated login" not in response.text


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_old_jev_assets_remain_auth_gated_instead_of_public(isolated_client, method):
    client, _, _ = isolated_client
    for name, mime in [("entry.js", "javascript"), ("entry.css", "text/css")]:
        response = client.request(method, "/jev-backtest-assets/assets/" + name)
        assert response.status_code == 307
    assert client.request(method, "/jev-backtest-assets/assets/missing.js").status_code == 307
    assert client.request(method, "/jev-backtest-assets/jev-backtest.html").status_code == 307
    assert client.request(method, "/app").status_code == 307
    for prefix in ["jev-backtest", "jev-convertible-t"]:
        assert client.request(method, f"/api/v1/{prefix}/capabilities").status_code == 401


@pytest.mark.parametrize("path", ["/jev-backtest", "/jev-backtest/"])
def test_approved_session_sees_withdrawn_notice_not_standalone(isolated_client, path):
    client, accounts, _ = isolated_client
    accounts.account_for_session.return_value = SimpleNamespace(id=123, display_name="Synthetic approved user")
    response = client.get(path + "?lab=convertible-t&round=sample")
    assert response.status_code == 410
    assert "Jev isolated login" not in response.text
    assert "Jev 功能暂时下线" in response.text
    assert "no-store" in response.headers["cache-control"]


def test_without_standalone_existing_access_still_works(isolated_client):
    from api.app import create_app
    _, _, static = isolated_client
    (static / "jev-backtest" / "jev-backtest.html").unlink()
    client = TestClient(create_app(static_dir=static), follow_redirects=False)
    response = client.get("/access?redirect=%2Fjev-backtest")
    assert response.status_code == 200
    assert "legacy main SPA" in response.text
