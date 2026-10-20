"""Local-only homepage cards and request-owned watchlist isolation."""

from copy import deepcopy
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event as sqlalchemy_event

from api.v1.endpoints import home_dashboard
from src.config import Config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.repositories.market_data_repo import MarketDataRepository
from src.services.home_dashboard_service import HomeDashboardService
from src.services.investment_monitor_service import InvestmentMonitorService
from src.services.market_data_service import MarketDataService
from src.services.startup_warmup_service import StartupWarmupService
from src.services.user_account_service import UserAccountService
from src.storage import DatabaseManager, StockDaily, UserAccount, UserWatchlistItem


class NoUpstream:
    def query(self, *args, **kwargs):
        raise AssertionError("Homepage personal cards must not request upstream data")


@pytest.fixture
def local_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "unused.env"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "home.db"))
    monkeypatch.setenv("STOCK_LIST", "600519")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager()
    repo = InvestmentMonitorRepository(db)
    # Bind only the existing read-only methods: constructing the production
    # monitor also registers sources and is intentionally outside this fixture.
    monitor = object.__new__(InvestmentMonitorService)
    monitor.repo = repo
    service = HomeDashboardService(tushare=NoUpstream(), monitor=monitor, background_refresh=False)
    market = MarketDataService(repository=MarketDataRepository(db), tushare=NoUpstream())
    monkeypatch.setattr("src.services.market_data_service.MarketDataService", lambda: market)
    monkeypatch.setattr("src.services.home_dashboard_service.get_index_stock_name", lambda code: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Personal cards must not refresh market data or the shared dashboard")

    monkeypatch.setattr(market, "refresh_ticks", forbidden)
    monkeypatch.setattr(service, "_build", forbidden)
    monkeypatch.setattr(service, "_schedule_refresh", forbidden)
    with db.session_scope() as session:
        for user_id in (1, 2, 3):
            session.add(UserAccount(id=user_id, display_name=f"测试用户{user_id}",
                                    normalized_name=f"test{user_id}", status="approved",
                                    password_salt="not-a-login", password_hash="not-a-login"))
        today = date.today()
        session.add_all([
            StockDaily(code="300476", date=today - timedelta(days=1), close=100),
            StockDaily(code="300476", date=today, open=101, high=106, low=100,
                       close=105, amount=1234, volume=100, data_source="local.test"),
        ])
    repo.upsert_events([
        {"source_key": "test.news", "source_name": "测试原文", "source_type": "test",
         "external_id": symbol, "event_type": "news", "perspective": "institution",
         "title": f"{symbol} 原文", "symbols": [symbol], "sentiment": "bullish",
         "importance_score": 80, "url": f"https://example.invalid/{symbol}",
         "summary": "本地测试证据", "raw_payload": {"not_for_home": "large-body"}}
        for symbol in ("300476.SZ", "600519.SH", "920748.BJ")
    ])
    accounts = UserAccountService(db)
    accounts.add_watchlist(1, "600519")
    accounts.add_watchlist(2, "300476")
    accounts.add_watchlist(2, "AAPL")
    try:
        yield service, accounts
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def test_personal_symbols_outside_global_stock_list_use_local_quotes_and_exact_evidence(local_home):
    service, _accounts = local_home
    cards = service.watchlist_for_symbols(["SZ300476", "300476.SZ", "AAPL", "hk700", "920748.BJ"])
    assert [card["symbol"] for card in cards] == ["300476.SZ", "AAPL", "HK00700", "920748.BJ"]
    new_stock = cards[0]
    assert new_stock["latest_quote"]["current_price"] == 105
    assert new_stock["latest_quote"]["source"] == "local.test"
    assert new_stock["event_count"] == 1
    assert new_stock["latest_catalyst"]["title"] == "300476.SZ 原文"
    assert new_stock["latest_institution"]["url"] == "https://example.invalid/300476.SZ"
    assert new_stock["latest_events"][0]["symbols"] == ["300476.SZ"]
    assert "raw_payload" not in new_stock["latest_events"][0]
    assert "600519" not in str(cards)
    # No local data does not silently remove a newly added or overseas stock.
    assert cards[1]["name"] == "AAPL"
    assert cards[1]["latest_quote"] is None
    assert cards[1]["event_count"] == 0
    assert cards[1]["latest_events"] == []
    assert cards[3]["latest_catalyst"]["symbols"] == ["920748.BJ"]


def test_only_same_symbol_public_history_can_be_reused_without_mutating_cache(local_home):
    service, _accounts = local_home
    public_cards = [
        {"symbol": "300476.SZ", "name": "本地名称", "history": [{"date": "20260901", "value": 99}]},
        {"symbol": "600519.SH", "name": "全局自选", "history": [{"date": "20260901", "value": 2000}]},
    ]
    before = deepcopy(public_cards)
    cards = service.watchlist_for_symbols(["300476"], cached_cards=public_cards)
    assert cards[0]["name"] == "本地名称"
    assert cards[0]["history"] == public_cards[0]["history"]
    cards[0]["history"].append({"value": 123})
    assert public_cards == before
    assert service.watchlist_for_symbols([], cached_cards=public_cards) == []


@pytest.mark.parametrize(("local_at", "cached_at", "local_stale", "expected_source"), [
    ("2026-09-11T14:00:00", "2026-09-11T15:00:00", False, "test.cached"),
    ("2026-09-11T15:00:00", "2026-09-11T14:00:00", True, "test.local"),
    ("2026-09-11T09:30:00", "2026-09-10T15:00:00", True, "test.local"),
    ("2026-09-11T07:01:00Z", "2026-09-11T15:00:00+08:00", True, "test.local"),
    (None, "2026-09-11T15:00:00", False, "test.cached"),
    ("2026-09-11T15:00:00", None, True, "test.local"),
    ("invalid", "2026-09-11T15:00:00", False, "test.cached"),
    ("20260911", "2026-09-11T14:53:00", True, "test.local"),
    ("2026-09-11T14:53:00", "2026-09-11", False, "test.cached"),
    ("2026-09-11T15:00:00", "2026-09-11T15:00:00", True, "test.cached"),
])
def test_personal_home_chooses_latest_dated_quote_without_mixing_sessions(
    local_home, monkeypatch, local_at, cached_at, local_stale, expected_source,
):
    service, _accounts = local_home
    local_quote = {
        "stock_code": "300476", "current_price": 100, "change_percent": -4.7619,
        "change": -5, "prev_close": 105, "high": 102, "low": 97,
        "update_time": local_at, "source": "test.local", "is_stale": local_stale,
    }
    cached_quote = {
        "current_price": 110, "change_percent": 10, "change": 10, "prev_close": 100,
        "high": 111, "low": 100, "update_time": cached_at,
        "source": "test.cached", "is_stale": False,
    }
    monkeypatch.setattr("src.services.market_data_service.MarketDataService",
                        lambda: SimpleNamespace(latest_quotes=lambda *_args, **_kwargs: [local_quote]))
    cards = [{"symbol": "300476.SZ", "name": "胜宏科技", "latest_quote": cached_quote}]
    before = deepcopy(cards)
    result = service.watchlist_for_symbols(["300476.SZ"], cached_cards=cards)
    expected = local_quote if expected_source == "test.local" else cached_quote
    assert result[0]["latest_quote"] == expected
    result[0]["latest_quote"]["current_price"] = 999
    assert cards == before
    assert local_quote["current_price"] == 100


@pytest.mark.parametrize("bad_price", [None, 0, -1, float("nan"), float("inf")])
def test_home_does_not_replace_valid_quote_with_invalid_newer_price(bad_price):
    previous = {"current_price": 105, "update_time": "2026-09-11T14:00:00"}
    bad = {"current_price": bad_price, "update_time": "2026-09-11T15:00:00"}
    assert HomeDashboardService._newest_watchlist_quote(bad, previous) == previous


def test_home_cached_symbol_facts_skip_event_queries_but_keep_quotes_live(local_home):
    service, _accounts = local_home
    statements = []
    with service.monitor.repo.db.get_session() as session:
        engine = session.get_bind()

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    sqlalchemy_event.listen(engine, "before_cursor_execute", observe)
    try:
        first = service.watchlist_for_symbols(["300476"])
        cold_event_reads = sum("monitoring_events" in query for query in statements)
        assert cold_event_reads > 0
        # A new market close is visible immediately; the facts cache has its
        # own 15-second lifecycle, independent of quotes and list membership.
        with service.monitor.repo.db.session_scope() as session:
            row = session.query(StockDaily).filter(StockDaily.code == "300476", StockDaily.date == date.today()).one()
            row.close = 106
        statements.clear()
        second = service.watchlist_for_symbols(["300476"])
        assert not any("monitoring_events" in query for query in statements)
        assert any("stock_daily" in query for query in statements)
        assert first[0]["latest_quote"]["current_price"] == 105
        assert second[0]["latest_quote"]["current_price"] == 106
        assert second[0]["latest_events"] == first[0]["latest_events"]
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe)


def test_home_symbol_facts_expire_refresh_and_do_not_share_response_mutations(local_home):
    service, _accounts = local_home
    first = service._watchlist_symbol_facts("300476.SZ")
    first["latest_events"][0]["title"] = "response-only mutation"
    assert service._watchlist_symbol_facts("300476.SZ")["latest_events"][0]["title"] != "response-only mutation"
    key = (service.monitor.repo.db._db_url, "300476.SZ")
    with service._watchlist_facts_lock:
        _created, payload = service._watchlist_facts[key]
        service._watchlist_facts[key] = (time.monotonic() - 16, payload)
    service.monitor.repo.upsert_events([{
        "source_key": "test.news", "source_name": "新来源", "source_type": "test",
        "external_id": "new-fact", "event_type": "news", "perspective": "company",
        "title": "刚入库新公告", "symbols": ["300476.SZ"], "sentiment": "neutral",
    }])
    refreshed = service._watchlist_symbol_facts("300476.SZ")
    assert refreshed["stats"]["event_count"] == 2
    assert any(row["title"] == "刚入库新公告" for row in refreshed["latest_events"])


@pytest.mark.parametrize("failure", [False, True])
def test_home_concurrent_symbol_reads_coalesce_and_recover_from_failure(local_home, monkeypatch, failure):
    service, _accounts = local_home
    waiters_ready = threading.Event()
    waiters_lock = threading.Lock()
    waiter_count = 0
    reads = 0

    class ObservedFuture(Future):
        def result(self, timeout=None):
            nonlocal waiter_count
            with waiters_lock:
                waiter_count += 1
                if waiter_count == 5:
                    waiters_ready.set()
            return super().result(timeout=timeout)

    original = service.monitor.repo.symbol_dashboard_stats

    def read_stats(**kwargs):
        nonlocal reads
        reads += 1
        assert waiters_ready.wait(5), "All concurrent misses should share one pending read"
        if failure:
            raise RuntimeError("temporary event read failure")
        return original(**kwargs)

    monkeypatch.setattr("src.services.home_dashboard_service.Future", ObservedFuture)
    monkeypatch.setattr(service.monitor.repo, "symbol_dashboard_stats", read_stats)
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(service._watchlist_symbol_facts, "300476.SZ") for _ in range(6)]
        if failure:
            for future in futures:
                with pytest.raises(RuntimeError, match="temporary event read failure"):
                    future.result(timeout=6)
        else:
            results = [future.result(timeout=6) for future in futures]
            assert all(result == results[0] for result in results)
    assert reads == 1
    assert (service.monitor.repo.db._db_url, "300476.SZ") not in service._watchlist_facts_pending
    if failure:
        monkeypatch.setattr(service.monitor.repo, "symbol_dashboard_stats", original)
        assert service._watchlist_symbol_facts("300476.SZ")["stats"]["event_count"] == 1


def test_home_symbol_cache_is_database_scoped_and_bounded(local_home, monkeypatch):
    service, _accounts = local_home
    monkeypatch.setattr(HomeDashboardService, "_watchlist_facts", {})
    monkeypatch.setattr(HomeDashboardService, "_watchlist_facts_limit", 2)
    for symbol in ("300476.SZ", "600519.SH", "920748.BJ"):
        service._watchlist_symbol_facts(symbol)
    assert len(service._watchlist_facts) == 2
    assert (service.monitor.repo.db._db_url, "300476.SZ") not in service._watchlist_facts
    monkeypatch.setattr(service.monitor.repo.db, "_db_url", "sqlite:///isolated-environment.db")
    service._watchlist_symbol_facts("920748.BJ")
    assert ("sqlite:///isolated-environment.db", "920748.BJ") in service._watchlist_facts


def test_workspace_warmup_includes_approved_personal_symbols_with_private_membership_excluded(local_home, monkeypatch):
    service, _accounts = local_home
    monitor = service.monitor
    monkeypatch.setattr(monitor, "equity_watchlist", lambda: ["600519.SH", "600519.SH"])
    with monitor.repo.db.session_scope() as session:
        session.get(UserAccount, 3).status = "pending"
        session.add(UserWatchlistItem(user_id=3, symbol="688001.SH", created_at=datetime(2026, 9, 14)))
        # Both approved users following the same equity still share only one
        # fact target, and canonical names cannot consume separate cache slots.
        session.add(UserWatchlistItem(user_id=1, symbol="SZ300476", created_at=datetime(2026, 9, 14)))
        for index in range(80):
            session.add(UserWatchlistItem(user_id=2, symbol=f"600{index:03d}.SH", created_at=datetime(2025, 1, 1)))
    symbols = StartupWarmupService._watchlist_symbols(monitor)
    assert len(symbols) == len(set(symbols)) == 64
    assert symbols[0] == "600519.SH"
    assert "300476.SZ" in symbols
    assert "AAPL" in symbols
    assert "688001.SH" not in symbols
    assert all(isinstance(symbol, str) for symbol in symbols)


def test_real_default_home_monitor_constructor_does_not_register_sources_or_write(local_home, monkeypatch):
    existing_service, _accounts = local_home
    statements = []

    def forbidden(*_args, **_kwargs):
        pytest.fail("Homepage construction must not initialize sources or fetch upstream")

    monkeypatch.setattr(InvestmentMonitorRepository, "ensure_sources", forbidden)
    monkeypatch.setattr("src.services.financial_data_service.TushareGatewayService.query", forbidden)
    with existing_service.monitor.repo.db.get_session() as session:
        engine = session.get_bind()

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower().lstrip())

    sqlalchemy_event.listen(engine, "before_cursor_execute", observe)
    try:
        # No injected monitor: execute the actual default construction path.
        service = HomeDashboardService(tushare=NoUpstream(), background_refresh=False)
        assert isinstance(service.monitor, InvestmentMonitorService)
        assert service.monitor._announcement_artifacts is None
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe)
    assert not any(sql.startswith(("insert", "update", "delete")) for sql in statements)


def test_home_api_switches_owner_and_reads_new_membership_without_poisoning_public_cache(local_home, monkeypatch):
    service, accounts = local_home
    shared_payload = {"cn_indices": [{"code": "000001.SH", "close": 3000}],
                      "watchlist": [{"symbol": "600519.SH", "name": "全局自选", "history": []}]}
    before = deepcopy(shared_payload)
    calls = []

    def public_dashboard(**options):
        calls.append(options)
        return shared_payload

    monkeypatch.setattr(service, "dashboard", public_dashboard)
    monkeypatch.setattr(home_dashboard, "HomeDashboardService", lambda: service)
    app = FastAPI()

    @app.middleware("http")
    async def test_identity(request: Request, call_next):
        # Test harness only: production identity continues to come exclusively
        # from the unchanged authentication middleware, never a query argument.
        request.state.user_id = int(request.headers.get("X-Test-User", "0"))
        return await call_next(request)

    app.include_router(home_dashboard.router, prefix="/home")
    with TestClient(app) as client:
        def read(owner, path="/home"):
            response = client.get(path, headers={"X-Test-User": str(owner)})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store, private"
            assert response.headers["vary"] == "Cookie, Authorization"
            return response.json()

        assert [card["symbol"] for card in read(1)["watchlist"]] == ["600519.SH"]
        b = read(2, "/home?user_id=1&force=false&refresh=true")
        assert [card["symbol"] for card in b["watchlist"]] == ["300476.SZ", "AAPL"]
        assert b["cn_indices"] == shared_payload["cn_indices"]
        assert "600519" not in str(b["watchlist"])
        accounts.add_watchlist(2, "HK00700")
        assert [card["symbol"] for card in read(2)["watchlist"]] == ["300476.SZ", "AAPL", "HK00700"]
        assert read(3)["watchlist"] == []
        assert read(0) == shared_payload  # Legacy/admin behavior remains supported.
    assert calls[1] == {"force": False, "refresh": True}
    assert shared_payload == before
