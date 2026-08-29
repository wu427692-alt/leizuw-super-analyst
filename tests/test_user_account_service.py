# -*- coding: utf-8 -*-
"""Regression coverage for the approved-user access and isolation model."""

from __future__ import annotations

import pytest

from src.services.user_account_service import (
    UserAccountError,
    UserAccountService,
    create_user_session,
    parse_user_session,
)
from src.repositories.alert_repo import AlertRepository
from src.repositories.backtest_repo import BacktestRepository
from src.repositories.decision_signal_repo import DecisionSignalRepository
from src.repositories.portfolio_repo import PortfolioRepository
from src.request_identity import reset_current_user_id, set_current_user_id
from src.services.essay_quant_service import EssayQuantError, EssayQuantService
from src.storage import AnalysisHistory, BacktestResult, DatabaseManager


@pytest.fixture()
def user_service(tmp_path, monkeypatch):
    db_path = tmp_path / "users.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{db_path}")
    try:
        yield UserAccountService(db)
    finally:
        DatabaseManager.reset_instance()


def test_registration_requires_approval_then_password_login(user_service):
    created = user_service.register("测试用户", "secret1", "203.0.113.7")
    assert created["status"] == "pending"

    with pytest.raises(UserAccountError) as pending:
        user_service.login("测试用户", "secret1", "203.0.113.7")
    assert pending.value.code == "pending_approval"

    user_service.set_status(created["id"], "approved")
    logged_in = user_service.login("测试用户", "secret1", "203.0.113.7")
    assert logged_in.display_name == "测试用户"


def test_shared_ip_requires_each_approved_user_to_log_in(user_service):
    first = user_service.register("用户甲", "secret1", "198.51.100.4")
    second = user_service.register("用户乙", "secret2", "198.51.100.4")
    user_service.set_status(first["id"], "approved")
    user_service.set_status(second["id"], "approved")

    assert user_service.login("用户甲", "secret1", "198.51.100.4").id == first["id"]
    assert user_service.login("用户乙", "secret2", "198.51.100.4").id == second["id"]


def test_session_cache_is_invalidated_immediately_when_account_is_disabled(user_service):
    created = user_service.register("缓存用户", "secret1", "203.0.113.9")
    user_service.set_status(created["id"], "approved")
    cookie = create_user_session(created["id"])

    assert user_service.account_for_session(cookie) is not None
    # The second read is served by the short in-memory cache.
    assert user_service.account_for_session(cookie) is not None

    user_service.set_status(created["id"], "disabled")
    assert user_service.account_for_session(cookie) is None


def test_legacy_ip_autologin_sessions_are_invalidated(user_service):
    created = user_service.register("历史会话用户", "secret1", "203.0.113.11")
    user_service.set_status(created["id"], "approved")

    assert parse_user_session(f"v1.{created['id']}.1.legacy.invalid") is None
    assert create_user_session(created["id"]).startswith("v2.")


def test_watchlists_are_isolated_while_symbols_can_share_market_storage(user_service):
    first = user_service.register("用户甲", "secret1", "203.0.113.1")
    second = user_service.register("用户乙", "secret2", "203.0.113.2")

    user_service.add_watchlist(first["id"], "603306", "603306.SH")
    user_service.add_watchlist(second["id"], "300476", "300476.SZ")

    assert user_service.list_watchlist(first["id"]) == ["603306.SH"]
    assert user_service.list_watchlist(second["id"]) == ["300476.SZ"]


def test_portfolio_accounts_and_alert_rules_follow_request_owner_scope(user_service):
    db = user_service.db
    first_token = set_current_user_id(11)
    try:
        PortfolioRepository(db).create_account(
            name="账户甲", broker=None, market="cn", base_currency="CNY",
        )
        AlertRepository(db).create_rule({
            "name": "华懋价格提醒", "target_scope": "single_symbol", "target": "603306",
            "alert_type": "price_cross", "parameters": '{"direction":"above","price":80}',
            "severity": "warning", "enabled": True, "source": "api",
        })
    finally:
        reset_current_user_id(first_token)

    second_token = set_current_user_id(12)
    try:
        second_portfolios = PortfolioRepository(db).list_accounts()
        second_alerts, second_total = AlertRepository(db).list_rules()
    finally:
        reset_current_user_id(second_token)

    assert second_portfolios == []
    assert second_alerts == []
    assert second_total == 0


def test_quant_rules_runs_and_traditional_backtests_follow_owner_scope(user_service):
    db = user_service.db
    first_token = set_current_user_id(21)
    try:
        first_quant = EssayQuantService(db=db)
        first_rule = first_quant.save_rule({"name": "用户甲策略"})
        first_signal = DecisionSignalRepository(db).create({
            "stock_code": "603306", "market": "cn", "source_type": "analysis",
            "trigger_source": "web", "action": "watch",
        })
    finally:
        reset_current_user_id(first_token)

    second_token = set_current_user_id(22)
    try:
        second_quant = EssayQuantService(db=db)
        assert second_quant.list_rules() == {"items": [], "total": 0}
        with pytest.raises(EssayQuantError, match="量化规则不存在"):
            second_quant.save_rule({"name": "越权修改"}, first_rule["id"])
        second_signals, second_signal_total = DecisionSignalRepository(db).list()
        assert DecisionSignalRepository(db).get(first_signal.id) is None
        assert second_signals == []
        assert second_signal_total == 0
    finally:
        reset_current_user_id(second_token)

    with db.get_session() as session:
        first_history = AnalysisHistory(query_id="u21_a", code="603306")
        second_history = AnalysisHistory(query_id="u22_b", code="300476")
        session.add_all([first_history, second_history])
        session.flush()
        session.add_all([
            BacktestResult(analysis_history_id=first_history.id, code="603306", eval_window_days=10),
            BacktestResult(analysis_history_id=second_history.id, code="300476", eval_window_days=10),
        ])
        session.commit()

    first_token = set_current_user_id(21)
    try:
        rows = BacktestRepository(db).list_results(code=None, limit=20)
    finally:
        reset_current_user_id(first_token)
    assert [row.code for row in rows] == ["603306"]
