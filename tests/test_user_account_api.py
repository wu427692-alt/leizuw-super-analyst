# -*- coding: utf-8 -*-
"""End-to-end coverage for approval, session login and page/API boundaries."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api.middlewares.auth import AuthMiddleware
from api.v1.endpoints.user_auth import router as user_auth_router
from src.services.user_account_service import UserAccountService
from src.config import Config
from src.storage import DatabaseManager


def test_approval_still_requires_password_login_and_session(tmp_path, monkeypatch):
    db_path = tmp_path / "user-api.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")
    monkeypatch.setenv("TRUST_X_FORWARDED_FOR", "true")
    Config.reset_instance()
    DatabaseManager.reset_instance()

    app = FastAPI()
    app.include_router(user_auth_router, prefix="/api/v1/user-auth")

    @app.get("/api/v1/private")
    def private(request: Request):
        return {"userId": request.state.user_id, "authMethod": request.state.auth_method}

    @app.get("/app")
    def protected_page():
        return {"page": "app"}

    @app.get("/access")
    def access_page():
        return {"page": "access"}

    app.add_middleware(AuthMiddleware)
    first_ip = {"CF-Connecting-IP": "203.0.113.20"}

    try:
        applicant = TestClient(app)
        registered = applicant.post(
            "/api/v1/user-auth/register",
            json={"name": "用户甲", "password": "secret1"},
            headers=first_ip,
        )
        assert registered.status_code == 202
        first_id = registered.json()["user"]["id"]
        assert applicant.get("/api/v1/private", headers=first_ip).status_code == 401

        service = UserAccountService()
        service.set_status(first_id, "approved")
        status = applicant.get("/api/v1/user-auth/status", headers=first_ip)
        assert status.json()["loggedIn"] is False
        assert status.json()["authMethod"] is None
        assert "dsa_user_session" not in applicant.cookies
        assert applicant.get("/api/v1/private", headers=first_ip).status_code == 401
        page_redirect = applicant.get("/app?tab=market", headers=first_ip, follow_redirects=False)
        assert page_redirect.status_code == 307
        assert page_redirect.headers["location"] == "/access?redirect=%2Fapp%3Ftab%3Dmarket"
        assert applicant.get("/access", headers=first_ip).status_code == 200

        logged_in = applicant.post(
            "/api/v1/user-auth/login",
            json={"name": "用户甲", "password": "secret1"},
            headers=first_ip,
        )
        assert logged_in.status_code == 200
        assert applicant.get("/api/v1/private", headers=first_ip).json()["userId"] == first_id
        assert applicant.get("/app", headers=first_ip).status_code == 200

        second = service.register("用户乙", "secret2", "203.0.113.20")
        service.set_status(second["id"], "approved")

        # The first browser owns a signed session, so a later account on the
        # same NAT cannot change its identity.
        assert applicant.get("/api/v1/private", headers=first_ip).json()["userId"] == first_id

        fresh_browser = TestClient(app)
        assert fresh_browser.get("/api/v1/private", headers=first_ip).status_code == 401
        assert fresh_browser.get("/app", headers=first_ip, follow_redirects=False).status_code == 307
        logged_in = fresh_browser.post(
            "/api/v1/user-auth/login",
            json={"name": "用户乙", "password": "secret2"},
            headers=first_ip,
        )
        assert logged_in.status_code == 200
        assert fresh_browser.get("/api/v1/private", headers=first_ip).json()["userId"] == second["id"]
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
