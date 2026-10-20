"""The optional shared account bundle serves every login destination."""
from pathlib import Path

from fastapi.testclient import TestClient


def test_shared_account_entry_and_backward_compatible_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "empty.env"))
    from api.app import create_app
    from api.middlewares import auth
    from src.config import Config
    from src.storage import DatabaseManager

    Config.reset_instance()
    DatabaseManager.reset_instance()
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    static: Path = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>original workspace</html>")
    (static / "account-access").mkdir()
    (static / "account-access" / "account-access.html").write_text("<html>shared account form</html>")
    try:
        client = TestClient(create_app(static_dir=static), follow_redirects=False)
        for query in ("", "?redirect=%2Fapp", "?redirect=%2Fai-news", "?bind=phone", "?redirect=%2F%2Fevil.invalid"):
            response = client.get("/access" + query)
            assert response.status_code == 200
            assert "shared account form" in response.text
            assert "no-store" in response.headers["cache-control"]
        assert client.head("/access").status_code == 200
        assert "shared account form" in client.get("/access/").text
        assert client.get("/app").status_code == 307
        assert client.get("/api/v1/user-auth/admin/users").status_code == 401
        # A deployment without the optional bundle still has the old SPA entry.
        other = tmp_path / "original-static"
        other.mkdir()
        (other / "index.html").write_text("<html>original workspace</html>")
        original = TestClient(create_app(static_dir=other), follow_redirects=False)
        assert "original workspace" in original.get("/access").text
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
