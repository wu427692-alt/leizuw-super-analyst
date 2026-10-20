from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import threading
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from src.services.sms_auth_service import (
    AliyunSmsProvider, SmsAuthError, SmsAuthService, SMS_BROWSER_COOKIE,
    sign_rpc_request, sms_login_enabled,
)
from src.services.user_account_service import (
    DISABLED_PASSWORD, UserAccountError, UserAccountService, USER_COOKIE_NAME,
    _verify_password,
)
from src.storage import DatabaseManager, SmsAuthChallenge, UserAccount, UserPhoneIdentity

PHONE = "13000000001"
BROWSER = "test-browser-token-012345678901234567890"
NOW = 1800000000.0


class FakeProvider:
    scheme = "test-scheme"

    def __init__(self):
        self.sent = []
        self.checked = []
        self.send_error = False
        self.verify_error = False

    def send(self, phone, challenge_id, scheme, purpose="login"):
        self.sent.append((phone, challenge_id, scheme, purpose))
        if self.send_error:
            raise RuntimeError("private provider text should not escape")

    def verify(self, phone, code, challenge_id, scheme):
        self.checked.append((phone, code, challenge_id, scheme))
        if self.verify_error:
            raise RuntimeError("private verification text should not escape")
        return code == "654321"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "sms.db"))
    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")
    monkeypatch.setenv("USER_REGISTRATION_MODE", "self_service")
    monkeypatch.setenv("SMS_AUTH_ENABLED", "true")
    for key in ["ACCESS_KEY_ID", "ACCESS_KEY_SECRET", "SIGN_NAME", "TEMPLATE_CODE"]:
        monkeypatch.setenv("ALIYUN_SMS_" + key, "unit-test-not-real")
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'sms.db'}")
    provider, clock = FakeProvider(), [NOW]
    service = SmsAuthService(db, provider=provider, clock=lambda: clock[0])
    try:
        yield service, provider, clock, monkeypatch
    finally:
        UserAccountService._session_cache.clear()
        DatabaseManager.reset_instance()


def issue(service, phone=PHONE, purpose="login", browser=BROWSER, user_id=None, ip="203.0.113.10"):
    return service.send(phone, purpose, browser, ip, user_id)["challengeId"]


def verify(service, challenge, *, phone=PHONE, code="654321", purpose="login", browser=BROWSER, user_id=None, name=None):
    return service.verify(phone, code, challenge, browser, "203.0.113.10", purpose=purpose, user_id=user_id, name=name)


def old_user(service, monkeypatch, name="原用户", state="approved"):
    monkeypatch.setenv("USER_REGISTRATION_MODE", "approval")
    accounts = UserAccountService(service.db)
    created = accounts.register(name, "secret-old", "203.0.113.10")
    if state != "pending":
        accounts.set_status(created["id"], state)
    monkeypatch.setenv("USER_REGISTRATION_MODE", "self_service")
    return created["id"]


def _independent_process_send(db_url, ready, start, output):
    """A fresh interpreter/connection, not another thread sharing a Session."""
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=db_url)
    service = SmsAuthService(db, provider=FakeProvider(), clock=lambda: NOW)
    ready.put(True)
    try:
        # Readiness is bounded by the parent, which releases/terminates children
        # in its finally block. A separate shorter child timer races that setup.
        start.wait()
        issue(service)
        output.put("sent")
    except SmsAuthError as exc:
        output.put(exc.code)
    finally:
        DatabaseManager.reset_instance()


def test_official_acs3_signing_vector():
    query, headers = sign_rpc_request(
        "ecs.cn-shanghai.aliyuncs.com", "RunInstances", "2014-05-26",
        {"ImageId": "win2019_1809_x64_dtc_zh-cn_40G_alibase_20230811.vhd", "RegionId": "cn-shanghai"},
        "YourAccessKeyId", "YourAccessKeySecret", "2023-10-26T10:22:32Z", "3156853299f313e23d1673dc12e1703d",
    )
    assert query == "ImageId=win2019_1809_x64_dtc_zh-cn_40G_alibase_20230811.vhd&RegionId=cn-shanghai"
    assert headers["Authorization"].endswith("Signature=06563a9e1b43f5dfe96b81484da74bceab24a1d853912eee15083a6f0f3283c0")


def test_provider_contract_requires_pass_and_matching_challenge(setup, monkeypatch):
    provider = AliyunSmsProvider()
    call = Mock(return_value={"Code": "OK", "Success": True, "Model": {"VerifyResult": "UNKNOWN", "OutId": "challenge"}})
    monkeypatch.setattr(provider, "_call", call)
    provider.send(PHONE, "challenge", "same-scheme")
    action, params = call.call_args.args
    assert action == "SendSmsVerifyCode"
    assert params["ReturnVerifyCode"] == "false"
    assert params["AutoRetry"] == "0"
    assert params["CodeLength"] == "6"
    assert params["ValidTime"] == "300"
    assert json.loads(params["TemplateParam"])["code"] == "##code##"
    assert provider.verify(PHONE, "654321", "challenge", "same-scheme") is False
    call.return_value["Model"] = {"VerifyResult": "PASS", "OutId": "another-challenge"}
    assert provider.verify(PHONE, "654321", "challenge", "same-scheme") is False
    call.return_value["Model"]["OutId"] = "challenge"
    assert provider.verify(PHONE, "654321", "challenge", "same-scheme") is True
    assert call.call_args.args[1]["SchemeName"] == "same-scheme"


@pytest.mark.parametrize("binding_template,expected", [
    ("100004", "100004"), ("", "100001"), ("   ", "100001"), (None, "100001"),
])
def test_provider_uses_optional_binding_template(setup, monkeypatch, binding_template, expected):
    monkeypatch.setenv("ALIYUN_SMS_TEMPLATE_CODE", "100001")
    if binding_template is None:
        monkeypatch.delenv("ALIYUN_SMS_BIND_TEMPLATE_CODE", raising=False)
    else:
        monkeypatch.setenv("ALIYUN_SMS_BIND_TEMPLATE_CODE", binding_template)
    provider = AliyunSmsProvider()
    call = Mock(return_value={"Code": "OK", "Success": True})
    monkeypatch.setattr(provider, "_call", call)
    provider.send(PHONE, "login-challenge", "", "login")
    assert call.call_args.args[1]["TemplateCode"] == "100001"
    provider.send(PHONE, "bind-challenge", "", "bind")
    assert call.call_args.args[1]["TemplateCode"] == expected
    assert json.loads(call.call_args.args[1]["TemplateParam"]) == {"code": "##code##", "min": "5"}
    with pytest.raises(SmsAuthError):
        provider.send(PHONE, "invalid-challenge", "", "reset")
    assert call.call_count == 2


def test_service_passes_verified_purpose_to_provider(setup):
    service, provider, _, monkeypatch = setup
    old_id = old_user(service, monkeypatch)
    issue(service)
    issue(service, phone="13000000002", purpose="bind", user_id=old_id)
    assert [sent[3] for sent in provider.sent] == ["login", "bind"]


def test_provider_transport_no_retry_or_raw_error_exposure(setup, monkeypatch):
    import src.services.sms_auth_service as module
    conn = Mock()
    conn.request.side_effect = TimeoutError("private phone code and signed URL")
    factory = Mock(return_value=conn)
    monkeypatch.setattr(module.http.client, "HTTPSConnection", factory)
    with pytest.raises(SmsAuthError) as error:
        AliyunSmsProvider().send(PHONE, "opaque", "")
    assert error.value.code == "sms_provider_unavailable"
    assert "private" not in str(error.value)
    assert conn.request.call_count == 1
    conn.close.assert_called_once()
    assert factory.call_args.args == ("dypnsapi.aliyuncs.com",)
    assert factory.call_args.kwargs["context"].verify_mode != 0


def test_new_sms_account_is_approved_password_disabled_and_one_use(setup):
    service, provider, clock, _ = setup
    challenge = issue(service)
    user = verify(service, challenge)
    assert user.status == "approved" and user.display_name == "用户0001"
    assert user.password_hash == user.password_salt == DISABLED_PASSWORD
    assert not _verify_password("anything", user.password_salt, user.password_hash)
    with pytest.raises(SmsAuthError, match="失效"):
        verify(service, challenge)
    clock[0] += 61
    next_challenge = issue(SmsAuthService(service.db, provider=provider, clock=lambda: clock[0]))
    assert verify(service, next_challenge, name="不能改掉原名字").id == user.id
    with service.db.get_session() as db:
        assert len(db.scalars(select(UserAccount)).all()) == 1
        assert len(db.scalars(select(UserPhoneIdentity)).all()) == 1
        reserved = db.get(SmsAuthChallenge, challenge)
        assert reserved.state == "consumed"
        assert PHONE not in reserved.phone_hash
    assert UserAccountService(service.db).phone_status(user.id) == {"phoneBound": True, "phoneMasked": "130****0001"}


@pytest.mark.parametrize("change", ["phone", "browser", "purpose", "user", "expired"])
def test_challenge_context_cannot_be_swapped(setup, change):
    service, provider, clock, _ = setup
    challenge = issue(service)
    kwargs = {}
    if change == "phone": kwargs["phone"] = "13000000002"
    if change == "browser": kwargs["browser"] = "another-browser-012345678901234567890"
    if change == "purpose": kwargs["purpose"] = "bind"
    if change == "user": kwargs.update(purpose="bind", user_id=1)
    if change == "expired": clock[0] += 301
    with pytest.raises(SmsAuthError):
        verify(service, challenge, **kwargs)
    assert not provider.checked


def test_five_attempts_and_provider_timeout_fail_closed(setup):
    service, provider, clock, _ = setup
    challenge = issue(service)
    for _ in range(5):
        with pytest.raises(SmsAuthError) as error:
            verify(service, challenge, code="000000")
        assert error.value.code == "invalid_code"
    with pytest.raises(SmsAuthError):
        verify(service, challenge)
    assert len(provider.checked) == 5
    clock[0] += 61
    challenge = issue(service)
    provider.verify_error = True
    with pytest.raises(SmsAuthError) as error:
        verify(service, challenge)
    assert error.value.code == "sms_provider_unavailable"
    provider.verify_error = False
    with pytest.raises(SmsAuthError):
        verify(service, challenge)


def test_send_failure_counts_quota_and_is_not_retried(setup):
    service, provider, _, _ = setup
    provider.send_error = True
    with pytest.raises(SmsAuthError):
        issue(service)
    with pytest.raises(SmsAuthError) as error:
        issue(service)
    assert error.value.status == 429 and error.value.retry_after == 60
    assert len(provider.sent) == 1


@pytest.mark.parametrize("scope", ["phone", "ip", "global"])
def test_persisted_rolling_daily_send_limits(setup, scope):
    service, provider, clock, monkeypatch = setup
    monkeypatch.setenv(f"SMS_AUTH_{scope.upper()}_DAILY_LIMIT", "1")
    issue(service)
    clock[0] += 61
    restarted = SmsAuthService(service.db, provider=provider, clock=lambda: clock[0])
    with pytest.raises(SmsAuthError) as error:
        issue(restarted, phone=PHONE if scope == "phone" else "13000000002", ip="203.0.113.11" if scope == "global" else "203.0.113.10")
    assert error.value.status == 429
    assert len(provider.sent) == 1


def test_concurrent_send_reserves_before_network(setup):
    service, provider, clock, _ = setup
    entered, release = threading.Event(), threading.Event()
    send = provider.send
    def blocked_send(*args):
        entered.set()
        assert release.wait(5)
        return send(*args)
    provider.send = blocked_send
    other = SmsAuthService(service.db, provider=provider, clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(issue, service)
        assert entered.wait(5)
        try:
            with pytest.raises(SmsAuthError) as error:
                issue(other)
            assert error.value.status == 429
        finally:
            release.set()
        assert first.result(timeout=5)
    assert len(provider.sent) == 1


def test_concurrent_verify_consumes_once_without_network_db_lock(setup):
    service, provider, clock, _ = setup
    challenge = issue(service)
    entered, release = threading.Event(), threading.Event()
    check = provider.verify
    def blocked_verify(*args):
        entered.set()
        assert release.wait(5)
        return check(*args)
    provider.verify = blocked_verify
    other = SmsAuthService(service.db, provider=provider, clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(verify, service, challenge)
        assert entered.wait(5)
        try:
            with pytest.raises(SmsAuthError):
                verify(other, challenge)
            # An unrelated send can transact while verification is in flight.
            assert issue(other, phone="13000000002")
        finally:
            release.set()
        user = first.result(timeout=5)
    assert user.id and len(provider.checked) == 1


def test_bind_preserves_old_id_data_and_never_merges_by_name_or_ip(setup):
    service, _, clock, monkeypatch = setup
    old_id = old_user(service, monkeypatch)
    accounts = UserAccountService(service.db)
    accounts.add_watchlist(old_id, "603306.SH")
    challenge = issue(service, purpose="bind", user_id=old_id)
    assert verify(service, challenge, purpose="bind", user_id=old_id).id == old_id
    clock[0] += 61
    assert verify(service, issue(service)).id == old_id
    another = verify(service, issue(service, phone="13000000002"), phone="13000000002", name="原用户")
    assert another.id != old_id and another.display_name == "原用户"
    assert accounts.list_watchlist(another.id) == []
    assert accounts.list_watchlist(old_id) == ["603306.SH"]
    clock[0] += 61
    conflict = issue(service, purpose="bind", user_id=another.id)
    with pytest.raises(SmsAuthError) as error:
        verify(service, conflict, purpose="bind", user_id=another.id)
    assert error.value.code == "phone_in_use"
    with service.db.get_session() as db:
        assert db.get(SmsAuthChallenge, conflict).state == "consumed"


@pytest.mark.parametrize("state", ["pending", "rejected", "disabled"])
def test_legacy_unapproved_accounts_never_auto_approve(setup, state):
    service, _, _, monkeypatch = setup
    user_id = old_user(service, monkeypatch, state=state)
    with pytest.raises(UserAccountError):
        UserAccountService(service.db).login("原用户", "secret-old", "203.0.113.10")
    with service.db.get_session() as db:
        assert db.get(UserAccount, user_id).status == state
    with pytest.raises(SmsAuthError):
        issue(service, purpose="bind", user_id=user_id)


def test_disabled_phone_identity_does_not_create_replacement(setup):
    service, _, clock, _ = setup
    user = verify(service, issue(service))
    UserAccountService(service.db).set_status(user.id, "disabled")
    clock[0] += 61
    with pytest.raises(SmsAuthError) as error:
        verify(service, issue(service))
    assert error.value.code == "account_unavailable"
    with service.db.get_session() as db:
        assert len(db.scalars(select(UserAccount)).all()) == 1


def test_api_auth_cookie_csrf_status_and_binding(setup):
    # Keep spawned service workers free of the endpoints package: its __init__
    # imports the complete app, including unrelated external SDKs/data services.
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from api.middlewares.auth import AuthMiddleware
    from api.v1.endpoints import user_auth

    service, provider, clock, monkeypatch = setup
    old_id = old_user(service, monkeypatch)
    monkeypatch.setattr(user_auth, "SmsAuthService", lambda: service)
    app = FastAPI()
    app.include_router(user_auth.router, prefix="/api/v1/user-auth")
    @app.get("/api/v1/private")
    def private(request: Request):
        return {"id": request.state.user_id}
    app.add_middleware(AuthMiddleware)
    client = TestClient(app, base_url="https://testserver")
    origin = {"Origin": "https://testserver"}
    assert client.get("/api/v1/private").status_code == 401
    status = client.get("/api/v1/user-auth/status").json()
    assert status["registrationMode"] == "self_service" and status["smsLoginEnabled"] is True
    register = client.post("/api/v1/user-auth/register", json={"name": "绕过短信", "password": "secret-pass"})
    assert register.status_code == 403 and register.json()["error"] == "sms_required"
    assert client.post("/api/v1/user-auth/sms/send", json={"phone": PHONE}).status_code == 403
    assert client.post("/api/v1/user-auth/sms/send", json={"phone": PHONE}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/v1/user-auth/sms/send", json={"phone": PHONE, "purpose": "bind"}, headers=origin).status_code == 401
    assert client.post("/api/v1/user-auth/sms/bind", json={"phone": PHONE, "code": "654321", "challengeId": "x" * 43}, headers=origin).status_code == 401
    logged_in = client.post("/api/v1/user-auth/login", json={"name": "原用户", "password": "secret-old"})
    assert logged_in.json()["user"]["id"] == old_id
    sent = client.post("/api/v1/user-auth/sms/send", json={"phone": PHONE, "purpose": "bind"}, headers=origin)
    assert sent.status_code == 200
    assert "Secure" in sent.headers["set-cookie"] and "HttpOnly" in sent.headers["set-cookie"]
    assert SMS_BROWSER_COOKIE in client.cookies
    body = {"phone": PHONE, "code": "654321", "challengeId": sent.json()["challengeId"]}
    assert client.post("/api/v1/user-auth/sms/bind", json=body, headers=origin).status_code == 422
    body["currentPassword"] = "wrong-old-password"
    assert client.post("/api/v1/user-auth/sms/bind", json=body, headers=origin).status_code == 401
    assert not provider.checked
    with service.db.get_session() as db:
        pending = db.get(SmsAuthChallenge, body["challengeId"])
        assert pending.state == "sent" and pending.attempts == 0
    with monkeypatch.context() as limited:
        limited.setattr(user_auth, "check_rate_limit", lambda key: False)
        assert client.post("/api/v1/user-auth/sms/bind", json=body, headers=origin).status_code == 429
        assert not provider.checked
    body["currentPassword"] = "secret-old"
    bound = client.post("/api/v1/user-auth/sms/bind", json=body, headers=origin)
    assert bound.status_code == 200 and bound.json()["user"]["id"] == old_id
    assert bound.json()["user"]["phoneMasked"] == "130****0001"
    assert client.get("/api/v1/user-auth/status").json()["user"]["phoneBound"] is True
    client.post("/api/v1/user-auth/logout")
    clock[0] += 61
    sent = client.post("/api/v1/user-auth/sms/send", json={"phone": PHONE}, headers=origin)
    body["challengeId"] = sent.json()["challengeId"]
    stranger = TestClient(app, base_url="https://testserver")
    assert stranger.post("/api/v1/user-auth/sms/login", json=body, headers=origin).status_code == 400
    result = client.post("/api/v1/user-auth/sms/login", json=body, headers=origin)
    assert result.status_code == 200 and USER_COOKIE_NAME in client.cookies
    assert client.get("/api/v1/private").json()["id"] == old_id
    assert client.post("/api/v1/user-auth/sms/login", json=body, headers=origin).status_code == 400


def test_new_send_supersedes_verification_in_flight(setup, monkeypatch):
    service, provider, clock, _ = setup
    challenge = issue(service)
    entered, release = threading.Event(), threading.Event()
    original_verify = provider.verify
    def wait_for_new_send(*args):
        entered.set()
        assert release.wait(5)
        return original_verify(*args)
    monkeypatch.setattr(provider, "verify", wait_for_new_send)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(verify, service, challenge)
        assert entered.wait(5)
        clock[0] += 61
        latest = issue(service)
        release.set()
        with pytest.raises(SmsAuthError):
            first.result(timeout=5)
    assert verify(service, latest).status == "approved"
    with service.db.get_session() as db:
        assert db.get(SmsAuthChallenge, challenge).state == "superseded"
        assert len(db.scalars(select(UserAccount)).all()) == 1


def test_disabled_while_provider_verifies_never_logs_in(setup, monkeypatch):
    service, provider, clock, _ = setup
    user = verify(service, issue(service))
    clock[0] += 61
    challenge = issue(service)
    original_verify = provider.verify
    def disable_then_verify(*args):
        UserAccountService(service.db).set_status(user.id, "disabled")
        return original_verify(*args)
    monkeypatch.setattr(provider, "verify", disable_then_verify)
    with pytest.raises(SmsAuthError) as error:
        verify(service, challenge)
    assert error.value.code == "account_unavailable"
    with service.db.get_session() as db:
        assert db.get(SmsAuthChallenge, challenge).state == "consumed"
        assert db.get(UserAccount, user.id).status == "disabled"


def test_sms_only_account_cannot_pass_password_reauthentication(setup):
    service, _, _, _ = setup
    user = verify(service, issue(service))
    with pytest.raises(UserAccountError) as error:
        UserAccountService(service.db).verify_current_password(user.id, DISABLED_PASSWORD)
    assert error.value.code == "invalid_credentials"


def test_sms_accounts_cannot_gain_admin_scope_and_admin_cookie_cannot_bind(setup, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from api.middlewares.auth import AuthMiddleware
    from api.v1.endpoints import user_auth
    import api.middlewares.auth as auth_middleware
    service, provider, _, _ = setup
    monkeypatch.setattr(user_auth, "SmsAuthService", lambda: service)
    monkeypatch.setattr(auth_middleware, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth_middleware, "verify_session", lambda cookie: cookie == "test-admin-cookie")
    app = FastAPI()
    app.include_router(user_auth.router, prefix="/api/v1/user-auth")
    @app.get("/api/v1/private")
    def private(request: Request):
        return {"id": request.state.user_id}
    app.add_middleware(AuthMiddleware)
    origin = {"Origin": "https://testserver"}
    client = TestClient(app, base_url="https://testserver")
    sent = client.post("/api/v1/user-auth/sms/send", json={"phone": PHONE}, headers=origin)
    body = {"phone": PHONE, "code": "654321", "challengeId": sent.json()["challengeId"]}
    result = client.post("/api/v1/user-auth/sms/login", json=body, headers=origin)
    assert result.status_code == 200
    assert result.json()["user"]["id"] > 0
    assert client.get("/api/v1/private").json()["id"] == result.json()["user"]["id"]
    assert client.get("/api/v1/user-auth/admin/users").status_code == 401
    admin = TestClient(app, base_url="https://testserver")
    admin.cookies.set(auth_middleware.COOKIE_NAME, "test-admin-cookie")
    assert admin.get("/api/v1/user-auth/admin/users").status_code == 200
    assert admin.post("/api/v1/user-auth/sms/send", json={"phone": PHONE, "purpose": "bind"}, headers=origin).status_code == 401
    body["currentPassword"] = "unrelated-password"
    assert admin.post("/api/v1/user-auth/sms/bind", json=body, headers=origin).status_code == 401
    assert len(provider.sent) == len(provider.checked) == 1


def test_send_limits_are_atomic_across_independent_processes(setup):
    service, _, _, _ = setup
    # Establish the shared HMAC secret before spawning fresh interpreters.
    issue(service, phone="13000000999")
    ctx = multiprocessing.get_context("spawn")
    ready, output, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    children = [ctx.Process(target=_independent_process_send,
                args=(str(service.db._engine.url), ready, start, output)) for _ in range(2)]
    try:
        for child in children:
            child.start()
        assert ready.get(timeout=20) is True
        assert ready.get(timeout=20) is True
        start.set()
        outcomes = sorted([output.get(timeout=15), output.get(timeout=15)])
        assert outcomes == ["sent", "sms_rate_limited"]
        for child in children:
            child.join(timeout=10)
            assert child.exitcode == 0
    finally:
        start.set()
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=5)
        ready.close()
        output.close()


def test_feature_and_credentials_fail_closed(monkeypatch):
    monkeypatch.delenv("SMS_AUTH_ENABLED", raising=False)
    assert not sms_login_enabled()
    monkeypatch.setenv("SMS_AUTH_ENABLED", "true")
    for key in ["ACCESS_KEY_ID", "ACCESS_KEY_SECRET", "SIGN_NAME", "TEMPLATE_CODE"]:
        monkeypatch.delenv("ALIYUN_SMS_" + key, raising=False)
    assert not sms_login_enabled()
    with pytest.raises(SmsAuthError) as error:
        AliyunSmsProvider()
    assert error.value.status == 503
