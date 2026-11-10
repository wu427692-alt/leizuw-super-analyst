"""Deployment health aliases must stay JSON rather than SPA fallback HTML."""

from datetime import datetime
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest


@pytest.mark.parametrize("with_frontend", [True, False])
@pytest.mark.parametrize("auth_enabled", [True, False])
def test_health_aliases_reuse_canonical_json_contract(tmp_path, monkeypatch, with_frontend, auth_enabled):
    from api.app import create_app
    from api.middlewares import auth

    monkeypatch.setenv("USER_ACCESS_ENABLED", "true" if auth_enabled else "false")
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: auth_enabled)
    user_service = Mock(side_effect=AssertionError("health probes must not access the user database"))
    monkeypatch.setattr(auth, "UserAccountService", user_service)
    static_dir = tmp_path / "static"
    if with_frontend:
        static_dir.mkdir()
        (static_dir / "index.html").write_text("<html>private SPA</html>", encoding="utf-8")
    client = TestClient(create_app(static_dir=static_dir), follow_redirects=False)

    for path in ("/health", "/api/health", "/api/v1/health"):
        result = client.get(path)
        assert result.status_code == 200
        assert result.headers["content-type"].startswith("application/json")
        assert result.json()["status"] == "ok"
        assert datetime.fromisoformat(result.json()["timestamp"])
        assert "location" not in result.headers
        assert "private SPA" not in result.text
    for path in ("/health", "/api/health"):
        result = client.head(path)
        assert result.status_code == 200
        assert result.headers["content-type"].startswith("application/json")
        assert result.content == b""
    user_service.assert_not_called()
