from src.services.startup_warmup_service import StartupWarmupService
import threading
from types import SimpleNamespace
import src.services.essay_analysis_service as essay_module
import src.services.home_dashboard_service as home_module
import src.services.industry_research_service as industry_module
import src.services.investment_monitor_service as monitor_module
import src.services.financial_data_service as financial_data_module


def test_startup_warmup_isolates_failed_views():
    calls = []

    def ready():
        calls.append("ready")

    def broken():
        calls.append("broken")
        raise RuntimeError("temporary")

    def still_ready():
        calls.append("still-ready")

    result = StartupWarmupService([
        ("ready", ready),
        ("broken", broken),
        ("still-ready", still_ready),
    ]).warm()

    assert calls == ["ready", "broken", "still-ready"]
    assert result["completed"] == ["ready", "still-ready"]
    assert result["failed"] == {"broken": "RuntimeError"}


class _StubService:
    @property
    def repo(self):
        return self

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _stub_default_services(monkeypatch):
    monkeypatch.setattr(essay_module, "EssayAnalysisService", lambda: _StubService())
    monkeypatch.setattr(financial_data_module, "ResearchNoteService", lambda: _StubService())
    monkeypatch.setattr(monitor_module, "InvestmentMonitorService", lambda: _StubService())
    monkeypatch.setattr(industry_module, "IndustryResearchService", lambda: _StubService())
    monkeypatch.setattr(home_module, "HomeDashboardService", lambda *args, **kwargs: _StubService())


def test_essential_warmup_keeps_heavy_analytics_lazy(monkeypatch):
    _stub_default_services(monkeypatch)
    monkeypatch.setenv("APP_STARTUP_WARMUP_PROFILE", "essential")

    names = [name for name, _ in StartupWarmupService()._default_tasks()]

    assert names == [
        "zsxq-topic-url-backfill",
        "stock-name-index",
        "essay-library-stats",
        "essay-status",
        "essay-feed-first-page",
        "research-note-audio-index",
        "investment-feed-first-page",
        "watchlist-stock-workspaces",
    ]


def test_full_warmup_retains_optional_analytics(monkeypatch):
    _stub_default_services(monkeypatch)
    monkeypatch.setenv("APP_STARTUP_WARMUP_PROFILE", "full")

    names = [name for name, _ in StartupWarmupService()._default_tasks()]

    assert "essay-deep-insights-short" in names
    assert "essay-deep-insights-medium" in names
    assert "essay-deep-insights-long" in names
    assert "industry-research-optical-module" in names
    assert "home-dashboard" in names


def test_watchlist_warmup_prepares_visible_overviews_before_full_context(monkeypatch):
    _stub_default_services(monkeypatch)
    monkeypatch.setenv("WATCHLIST_WORKSPACE_WARMUP_WORKERS", "3")
    calls = []
    monitor = _StubService()
    monitor.equity_watchlist = lambda: ["603306.SH", "300308.SZ", "300476.SZ"]
    monitor.stock_workspace = lambda symbol, **kwargs: calls.append((symbol, kwargs))
    monkeypatch.setattr(monitor_module, "InvestmentMonitorService", lambda: monitor)
    monkeypatch.setattr(StartupWarmupService, "_watchlist_symbols", staticmethod(lambda service: service.equity_watchlist()))
    tasks = dict(StartupWarmupService()._default_tasks())
    tasks["watchlist-stock-workspaces"]()
    assert len(calls) == 6
    assert all(options == {"days": 183, "refresh": True, "view": "overview"} for _, options in calls[:3])
    assert all(options == {"days": 183, "refresh": True, "view": "full"} for _, options in calls[3:])


def test_parallel_warmup_is_bounded_and_finishes_maintenance_before_reads(monkeypatch):
    monkeypatch.setenv("APP_STARTUP_WARMUP_WORKERS", "2")
    maintenance_done = threading.Event()
    pair = threading.Barrier(2)
    state = SimpleNamespace(active=0, maximum=0)
    lock = threading.Lock()

    def maintenance():
        maintenance_done.set()

    def read():
        assert maintenance_done.is_set()
        with lock:
            state.active += 1
            state.maximum = max(state.maximum, state.active)
        pair.wait(timeout=3)
        with lock:
            state.active -= 1

    result = StartupWarmupService([
        ("zsxq-topic-url-backfill", maintenance),
        ("overview-one", read), ("overview-two", read),
    ]).warm()
    assert not result["failed"]
    assert result["completed"] == ["zsxq-topic-url-backfill", "overview-one", "overview-two"]
    assert state.maximum == 2


def test_full_profile_write_maintenance_is_serial_before_parallel_views(monkeypatch):
    monkeypatch.setenv("APP_STARTUP_WARMUP_WORKERS", "2")
    calls = []

    def first_write():
        calls.append("url-backfill")
        raise RuntimeError("one maintenance task may fail")

    def second_write():
        assert calls == ["url-backfill"]
        calls.append("keywords")

    def read():
        assert calls == ["url-backfill", "keywords"]

    result = StartupWarmupService([
        ("zsxq-topic-url-backfill", first_write), ("view-one", read),
        ("watchlist-keyword-index", second_write), ("view-two", read),
    ]).warm()
    assert result["completed"] == ["watchlist-keyword-index", "view-one", "view-two"]
    assert result["failed"] == {"zsxq-topic-url-backfill": "RuntimeError"}


def test_explicit_empty_warmup_does_not_start_default_work(monkeypatch):
    monkeypatch.setattr(StartupWarmupService, "_default_tasks", lambda _self: (_ for _ in ()).throw(AssertionError()))
    assert StartupWarmupService([]).warm()["completed"] == []
