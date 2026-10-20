from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest

from src.services.cninfo_announcement_service import CninfoAnnouncementError
from src.services.sync_watchdog_worker import SyncWatchdogWorker
from src.services.watchlist_announcement_sync_worker import WatchlistAnnouncementSyncWorker
from src.storage import utc_naive_now


_SHANGHAI = timezone(timedelta(hours=8))


class _FakeCninfo:
    def __init__(self, outcomes):
        self.outcomes = {symbol: list(values) for symbol, values in outcomes.items()}
        self.calls = []

    def fetch(self, **kwargs):
        self.calls.append(dict(kwargs))
        symbol = kwargs["symbols"][0]
        outcome = self.outcomes[symbol].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeService:
    def __init__(self, symbols, outcomes):
        self.symbols = list(symbols)
        self.cninfo = _FakeCninfo(outcomes)

    def equity_watchlist(self):
        return list(self.symbols)

    @staticmethod
    def _stock_name(symbol):
        return symbol

    @staticmethod
    def _announcement_events(_source, rows):
        return list(rows)


class _MemoryRepository:
    def __init__(self, states):
        self.states = {state["symbol"]: deepcopy(state) for state in states}
        self.updates = []

    def ensure_announcement_sync_state(self, symbol, **_kwargs):
        return deepcopy(self.states[symbol])

    def list_announcement_sync_states(self, symbols=None):
        selected = list(symbols) if symbols else sorted(self.states)
        return [deepcopy(self.states[symbol]) for symbol in selected]

    def update_announcement_sync_state(self, symbol, **fields):
        self.states[symbol].update(deepcopy(fields))
        self.updates.append((symbol, deepcopy(fields)))
        return deepcopy(self.states[symbol])

    @staticmethod
    def get_source(_source_key):
        return {"source_key": "cninfo.announcements"}

    @staticmethod
    def upsert_events(rows):
        return {"created": len(rows), "updated": 0}

    @staticmethod
    def update_source_status(*_args, **_kwargs):
        return None


def _state(symbol, *, history_complete=True, backfill_completed=True, last_success=None):
    today = datetime.now(_SHANGHAI).date()
    window_key = f"{today.isoformat()}~{today.isoformat()}"
    return {
        "symbol": symbol,
        "stock_name": symbol,
        "target_start": today.isoformat(),
        "target_end": today.isoformat(),
        "completed_windows": [window_key] if history_complete else [],
        "status": "live" if backfill_completed else "pending",
        "consecutive_failures": 0,
        "next_retry_at": None,
        "last_error": None,
        "last_incremental_started_at": None,
        "last_incremental_success_at": last_success,
        "last_backfill_started_at": None,
        "last_backfill_success_at": None,
        "backfill_completed_at": utc_naive_now().isoformat() + "Z" if backfill_completed else None,
        "total_fetched": 0,
        "total_created": 0,
        "total_updated": 0,
        "updated_at": utc_naive_now().isoformat() + "Z",
    }


def _worker(states, outcomes):
    symbols = [state["symbol"] for state in states]
    repository = _MemoryRepository(states)
    service = _FakeService(symbols, outcomes)
    worker = WatchlistAnnouncementSyncWorker(repository=repository, service=service)
    worker._watchlist_symbols = lambda: list(symbols)
    worker.max_windows_per_cycle = 24
    return worker, repository, service


def test_completed_history_does_not_erase_incremental_failure_or_backoff():
    state = _state("603306.SH", history_complete=True, backfill_completed=False)
    worker, repository, _service = _worker(
        [state], {"603306.SH": [RuntimeError("upstream timeout")]},
    )

    result = worker.run_once()
    persisted = repository.states["603306.SH"]

    assert result["status"] == "failed"
    assert result["failed"] == 1
    assert persisted["backfill_completed_at"] is not None
    assert persisted["status"] == "retry"
    assert persisted["consecutive_failures"] == 1
    assert persisted["next_retry_at"] is not None
    assert "upstream timeout" in persisted["last_error"]
    assert worker.status()["status"] == "degraded"


def test_incomplete_history_keeps_incremental_failure_for_retry():
    state = _state("603306.SH", history_complete=False, backfill_completed=False)
    worker, repository, _service = _worker(
        [state], {"603306.SH": [RuntimeError("upstream timeout")]},
    )

    result = worker.run_once()
    persisted = repository.states["603306.SH"]

    assert result["status"] == "failed"
    assert persisted["status"] == "retry"
    assert persisted["consecutive_failures"] == 1
    assert persisted["next_retry_at"] is not None
    assert persisted["backfill_completed_at"] is None
    assert persisted["completed_windows"] == []


def test_successful_retry_clears_failure_and_restores_healthy_status():
    state = _state("603306.SH")
    worker, repository, _service = _worker(
        [state], {"603306.SH": [RuntimeError("upstream timeout"), []]},
    )

    first = worker.run_once()
    assert first["status"] == "failed"
    assert worker._last_success_at is None

    repository.update_announcement_sync_state("603306.SH", next_retry_at=None)
    second = worker.run_once()
    persisted = repository.states["603306.SH"]

    assert second["status"] == "success"
    assert persisted["status"] == "live"
    assert persisted["consecutive_failures"] == 0
    assert persisted["next_retry_at"] is None
    assert persisted["last_error"] is None
    assert persisted["last_incremental_success_at"] is not None
    assert worker._last_success_at is not None
    assert worker.status()["status"] == "healthy"


def test_all_failures_are_reported_and_do_not_advance_last_success():
    states = [_state("300308.SZ"), _state("603306.SH")]
    worker, _repository, _service = _worker(states, {
        "300308.SZ": [RuntimeError("timeout a")],
        "603306.SH": [RuntimeError("timeout b")],
    })
    worker._last_success_at = 123.0

    result = worker.run_once()

    assert result["status"] == "failed"
    assert result["succeeded"] == 0
    assert result["failed"] == 2
    assert worker._last_success_at == 123.0
    assert worker.status()["status"] == "degraded"


def test_mixed_results_are_partial_and_keep_failed_symbol_visible():
    states = [_state("300308.SZ"), _state("603306.SH")]
    worker, _repository, _service = _worker(states, {
        "300308.SZ": [[]],
        "603306.SH": [RuntimeError("timeout")],
    })

    result = worker.run_once()
    status = worker.status()

    assert result["status"] == "partial"
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert status["status"] == "degraded"
    assert status["degraded_symbols"] == ["603306.SH"]


def test_no_due_work_is_idle_and_does_not_advance_last_success():
    future_success = (utc_naive_now() + timedelta(hours=1)).isoformat() + "Z"
    state = _state("603306.SH", last_success=future_success)
    worker, _repository, service = _worker([state], {"603306.SH": []})
    worker._last_success_at = 123.0

    result = worker.run_once()

    assert result["status"] == "idle"
    assert result["succeeded"] == 0
    assert result["failed"] == 0
    assert worker._last_success_at == 123.0
    assert service.cninfo.calls == []
    assert worker.status()["status"] == "idle"


class _WatchdogWorker:
    def __init__(self, state):
        self.state = dict(state)
        self.starts = 0
        self.triggers = 0

    def status(self):
        return deepcopy(self.state)

    def start(self):
        self.starts += 1
        self.state["running"] = True
        return self.status()

    def trigger(self):
        self.triggers += 1
        return {**self.status(), "refresh_requested": True}


def test_watchdog_reports_upstream_degradation_without_restarting_worker(monkeypatch):
    monkeypatch.setenv("INVESTMENT_MONITOR_AUTO_START", "false")
    monkeypatch.setenv("ZSXQ_MCP_AUTO_START", "false")
    monkeypatch.setenv("MARKET_DATA_AUTO_START", "false")
    monkeypatch.setenv("CNINFO_WATCHLIST_AUTO_START", "true")
    announcement = _WatchdogWorker({
        "running": True,
        "status": "degraded",
        "poll_seconds": 60,
        "last_run_age_seconds": 1,
        "degraded_symbols": ["603306.SH"],
    })
    watchdog = SyncWatchdogWorker(
        announcement_worker=announcement,
        clock=lambda: 1_700_000_000.0,
    )

    result = watchdog.audit_once()

    assert result["status"] == "degraded"
    assert result["repairs"] == []
    assert result["degraded_workers"] == [{
        "worker": "cninfo_watchlist",
        "reason": "upstream_sync_failed",
        "failed_symbols": 1,
    }]
    assert announcement.starts == 0
    assert announcement.triggers == 0
    assert watchdog.status()["last_healthy_at"] is None


def test_watchdog_wakes_stale_heartbeat_but_does_not_restart_or_clear_backoff(monkeypatch):
    monkeypatch.setenv("INVESTMENT_MONITOR_AUTO_START", "false")
    monkeypatch.setenv("ZSXQ_MCP_AUTO_START", "false")
    monkeypatch.setenv("MARKET_DATA_AUTO_START", "false")
    monkeypatch.setenv("CNINFO_WATCHLIST_AUTO_START", "true")
    announcement = _WatchdogWorker({
        "running": True,
        "status": "degraded",
        "poll_seconds": 60,
        "last_run_age_seconds": 600,
        "degraded_symbols": ["603306.SH"],
        "symbols": [{
            "symbol": "603306.SH",
            "status": "retry",
            "consecutive_failures": 3,
            "next_retry_at": (utc_naive_now() + timedelta(minutes=5)).isoformat() + "Z",
        }],
    })
    watchdog = SyncWatchdogWorker(
        announcement_worker=announcement,
        clock=lambda: 1_700_000_000.0,
    )

    result = watchdog.audit_once()

    assert result["status"] == "degraded"
    assert result["repairs"] == [{"worker": "cninfo_watchlist", "action": "wake"}]
    assert announcement.starts == 0
    assert announcement.triggers == 1
    assert announcement.state["symbols"][0]["consecutive_failures"] == 3


def test_idle_during_backoff_preserves_error_and_does_not_request_again():
    worker, repo, service = _worker([_state('603306.SH')], {'603306.SH': [RuntimeError('timeout')]})
    worker.run_once()
    before = deepcopy(repo.states['603306.SH'])
    assert worker.run_once()['status'] == 'idle'
    assert len(service.cninfo.calls) == 1
    assert repo.states['603306.SH']['next_retry_at'] == before['next_retry_at']
    assert repo.states['603306.SH']['last_error'] == before['last_error']
    assert worker.status()['status'] == 'degraded'
    assert worker._last_success_at is None


@pytest.mark.parametrize(
    "retry_after, expected",
    [(120, 120), (600, 600), (10**9, 10**9), (2, 30), (float("inf"), 30), ("bad", 30)],
)
def test_worker_persists_safe_provider_retry_after(retry_after, expected):
    error = CninfoAnnouncementError('HTTP 429', status_code=429, retry_after_seconds=retry_after)
    worker, repo, _ = _worker([_state('603306.SH')], {'603306.SH': [error]})
    result = worker.run_once()
    assert result['incremental'][0]['retry_seconds'] == expected
    assert repo.states['603306.SH']['status'] == 'retry'


def test_forbidden_response_uses_long_backoff_without_restarting():
    error = CninfoAnnouncementError('HTTP 403', status_code=403)
    worker, _, _ = _worker([_state('603306.SH')], {'603306.SH': [error]})
    assert worker.run_once()['incremental'][0]['retry_seconds'] == 300
