from src.services.startup_warmup_service import StartupWarmupService
import src.services.essay_analysis_service as essay_module
import src.services.home_dashboard_service as home_module
import src.services.industry_research_service as industry_module
import src.services.investment_monitor_service as monitor_module


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
