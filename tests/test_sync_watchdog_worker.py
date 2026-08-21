from __future__ import annotations

from datetime import datetime, timedelta

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.sync_watchdog_worker import SyncWatchdogWorker


class _FakeWorker:
    def __init__(self, state):
        self.state = dict(state)
        self.starts = 0
        self.triggers = 0

    def status(self):
        return dict(self.state)

    def start(self):
        self.starts += 1
        self.state["running"] = True
        return self.status()

    def trigger(self):
        self.triggers += 1
        return {**self.status(), "refresh_requested": True}


class _FakeRepository:
    def __init__(self, due=()):
        self._due = list(due)

    def due_sources(self):
        return list(self._due)


def _watchdog(monkeypatch, *, investment, zsxq, market, due=()):
    monkeypatch.setenv("INVESTMENT_MONITOR_AUTO_START", "true")
    monkeypatch.setenv("ZSXQ_MCP_AUTO_START", "true")
    monkeypatch.setenv("MARKET_DATA_AUTO_START", "true")
    monkeypatch.setenv("CNINFO_WATCHLIST_AUTO_START", "false")
    return SyncWatchdogWorker(
        investment_worker=investment,
        zsxq_worker=zsxq,
        market_worker=market,
        repository_factory=lambda: _FakeRepository(due),
        clock=lambda: 1_700_000_000.0,
    )


def test_watchdog_restarts_configured_workers_that_are_not_running(monkeypatch):
    investment = _FakeWorker({"running": False, "poll_seconds": 10, "last_sync_age_seconds": None})
    zsxq = _FakeWorker({
        "running": False, "available": True, "syncing": False,
        "poll_seconds": 30, "last_sync_age_seconds": None,
    })
    market = _FakeWorker({
        "running": False, "collecting_window": False,
        "poll_seconds": 1, "last_run_age_seconds": None,
    })

    result = _watchdog(monkeypatch, investment=investment, zsxq=zsxq, market=market).audit_once()

    assert result["status"] == "repaired"
    assert investment.starts == 1
    assert zsxq.starts == 1
    assert market.starts == 1
    assert {item["worker"] for item in result["repairs"]} == {
        "investment_monitor", "zsxq_mcp", "market_data",
    }


def test_watchdog_wakes_overdue_sources_and_stale_heartbeats(monkeypatch):
    investment = _FakeWorker({"running": True, "poll_seconds": 10, "last_sync_age_seconds": 5})
    zsxq = _FakeWorker({
        "running": True, "available": True, "syncing": False,
        "poll_seconds": 30, "last_sync_age_seconds": 121,
    })
    market = _FakeWorker({
        "running": True, "collecting_window": True,
        "poll_seconds": 1, "last_run_age_seconds": 6,
    })

    result = _watchdog(
        monkeypatch,
        investment=investment,
        zsxq=zsxq,
        market=market,
        due=({"source_key": "tushare.news.cls"},),
    ).audit_once()

    assert result["status"] == "repaired"
    assert investment.triggers == 1
    assert zsxq.triggers == 1
    assert market.triggers == 1
    investment_repair = next(item for item in result["repairs"] if item["worker"] == "investment_monitor")
    assert investment_repair["overdue_sources"] == ["tushare.news.cls"]


def test_watchdog_leaves_healthy_workers_alone(monkeypatch):
    investment = _FakeWorker({"running": True, "poll_seconds": 10, "last_sync_age_seconds": 1})
    zsxq = _FakeWorker({
        "running": True, "available": True, "syncing": False,
        "poll_seconds": 30, "last_sync_age_seconds": 1,
    })
    market = _FakeWorker({
        "running": True, "collecting_window": False,
        "poll_seconds": 1, "last_run_age_seconds": None,
    })

    result = _watchdog(monkeypatch, investment=investment, zsxq=zsxq, market=market).audit_once()

    assert result["status"] == "healthy"
    assert result["repairs"] == []
    assert investment.triggers == zsxq.triggers == market.triggers == 0


def test_failed_and_unconfigured_sources_use_retry_backoff():
    now = datetime(2026, 8, 21, 10, 0, 0)
    recent_failure = {
        "source_key": "failed.recent", "enabled": True, "poll_interval_seconds": 30,
        "last_success_at": None, "last_started_at": (now - timedelta(seconds=30)).isoformat() + "Z",
        "last_status": "failed",
    }
    old_failure = {
        **recent_failure,
        "source_key": "failed.old",
        "last_started_at": (now - timedelta(seconds=61)).isoformat() + "Z",
    }
    unconfigured = {
        **recent_failure,
        "source_key": "source.unconfigured",
        "last_started_at": (now - timedelta(minutes=10)).isoformat() + "Z",
        "last_status": "not_configured",
    }
    repository = object.__new__(InvestmentMonitorRepository)
    repository.list_sources = lambda: [recent_failure, old_failure, unconfigured]

    assert [item["source_key"] for item in repository.due_sources(now)] == ["failed.old"]
