"""Deterministic unattended report-library wakeups, without upstream calls."""

from types import SimpleNamespace
import time
from unittest.mock import Mock

import pytest

import src.services.investment_monitor_worker as monitor_module
from src.services.research_report_library_service import ResearchReportLibraryService
from src.services import stock_index_remote_service


@pytest.fixture(autouse=True)
def stock_directory_wake(monkeypatch):
    wake = Mock(return_value=False)
    monkeypatch.setattr(stock_index_remote_service, "ensure_stock_directory_refresh", wake)
    return wake


def _controlled_worker(monkeypatch, ticks):
    monkeypatch.setenv("INVESTMENT_MONITOR_RUN_MODE", "embedded")
    clock = SimpleNamespace(monotonic=ticks[0], wall=1_700_000_000.0)
    monkeypatch.setattr(monitor_module, "time", SimpleNamespace(
        monotonic=lambda: clock.monotonic,
        time=lambda: clock.wall,
        strftime=time.strftime,
        gmtime=time.gmtime,
    ))
    worker = monitor_module.InvestmentMonitorWorker()
    monitor = SimpleNamespace(sync_due_sources=Mock(return_value={"totals": {"created": 0}}))
    library = SimpleNamespace(ensure_background_sync=Mock(return_value={"status": "idle"}))
    factory = Mock(return_value=library)
    monkeypatch.setattr(monitor_module, "InvestmentMonitorService", lambda: monitor)
    monkeypatch.setattr(ResearchReportLibraryService, "get_instance", factory)

    class CycleDriver:
        index = 0

        def wait(self, _seconds):
            self.index += 1
            # Deliberately reverse the wall clock: it must not determine the
            # maintenance cadence or prevent the next monotonic-time check.
            clock.wall -= 3600
            if self.index == len(ticks):
                worker._stop_event.set()
            else:
                clock.monotonic = ticks[self.index]

        def clear(self):
            pass

    worker._wake_event = CycleDriver()
    return worker, monitor, library, factory, clock


def test_first_collector_cycle_wakes_report_library_without_any_web_request(monkeypatch):
    worker, monitor, library, factory, _ = _controlled_worker(monkeypatch, [0.0])

    worker._run()

    factory.assert_called_once_with()
    library.ensure_background_sync.assert_called_once_with()
    monitor.sync_due_sources.assert_called_once_with()
    assert worker._last_error is None


def test_report_wake_is_limited_to_five_monotonic_minutes(monkeypatch):
    ticks = [0.0, 15.0, 299.999, 300.0, 301.0, 600.0]
    worker, monitor, library, factory, clock = _controlled_worker(monkeypatch, ticks)
    checked_at = []
    library.ensure_background_sync.side_effect = lambda: checked_at.append(clock.monotonic)

    worker._run()

    assert checked_at == [0.0, 300.0, 600.0]
    assert factory.call_count == 3
    assert monitor.sync_due_sources.call_count == len(ticks)


def test_report_wake_failure_does_not_stop_monitoring_or_retry_each_source_poll(monkeypatch):
    worker, monitor, library, _, _ = _controlled_worker(monkeypatch, [0.0, 15.0, 300.0])
    library.ensure_background_sync.side_effect = [RuntimeError("temporary report-library failure"), {"status": "queued"}]

    worker._run()

    assert library.ensure_background_sync.call_count == 2
    assert monitor.sync_due_sources.call_count == 3
    assert worker._last_error is None
    assert worker._consecutive_failures == 0


def test_report_library_construction_failure_is_isolated_from_monitoring(monkeypatch):
    worker, monitor, library, factory, _ = _controlled_worker(monkeypatch, [0.0, 300.0])
    factory.side_effect = [RuntimeError("temporary database contention"), library]

    worker._run()

    assert factory.call_count == 2
    library.ensure_background_sync.assert_called_once_with()
    assert monitor.sync_due_sources.call_count == 2
    assert worker._last_error is None


def test_stock_directory_is_woken_without_visitors_on_existing_maintenance_cadence(monkeypatch, stock_directory_wake):
    worker, monitor, _, _, _ = _controlled_worker(monkeypatch, [0.0, 15.0, 300.0, 301.0])
    worker._run()
    assert stock_directory_wake.call_count == 2
    assert monitor.sync_due_sources.call_count == 4


def test_stock_directory_wake_failure_cannot_stop_sources(monkeypatch, stock_directory_wake):
    worker, monitor, _, _, _ = _controlled_worker(monkeypatch, [0.0, 300.0])
    stock_directory_wake.side_effect = RuntimeError("directory temporarily unavailable")
    worker._run()
    assert monitor.sync_due_sources.call_count == 2


def test_monitoring_failure_does_not_prevent_next_report_library_wake(monkeypatch):
    worker, monitor, library, _, _ = _controlled_worker(monkeypatch, [0.0, 300.0])
    monitor.sync_due_sources.side_effect = RuntimeError("temporary news source failure")

    worker._run()

    assert library.ensure_background_sync.call_count == 2
    assert monitor.sync_due_sources.call_count == 2
    assert worker._consecutive_failures == 2


def test_external_web_worker_never_starts_report_library_or_collector(monkeypatch):
    library_factory = Mock()
    monitor_factory = Mock()
    monkeypatch.setattr(ResearchReportLibraryService, "get_instance", library_factory)
    monkeypatch.setattr(monitor_module, "InvestmentMonitorService", monitor_factory)
    monkeypatch.setenv("INVESTMENT_MONITOR_RUN_MODE", "external")
    worker = monitor_module.InvestmentMonitorWorker()

    started = worker.start()
    triggered = worker.trigger()

    assert started["running"] is False and triggered["running"] is False
    assert worker._thread is None
    library_factory.assert_not_called()
    monitor_factory.assert_not_called()
