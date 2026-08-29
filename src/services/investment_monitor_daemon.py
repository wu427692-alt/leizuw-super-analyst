"""Small process supervisor for the cloud investment-monitor worker."""

from __future__ import annotations

import logging
import signal
import threading
from typing import Optional

from src.logging_config import setup_logging
from src.services.investment_monitor_worker import InvestmentMonitorWorker


logger = logging.getLogger(__name__)


def run(worker: Optional[InvestmentMonitorWorker] = None) -> int:
    """Run the source poller outside the web process and fail fast if it dies."""
    setup_logging()
    monitor = worker or InvestmentMonitorWorker.get_instance()
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    state = monitor.start()
    if not state.get("running"):
        logger.error("Investment monitor daemon could not start: %s", state)
        return 1
    logger.info("Investment monitor daemon started")
    try:
        while not stop.wait(15):
            if not monitor.status().get("running"):
                logger.error("Investment monitor thread stopped unexpectedly")
                return 1
    finally:
        monitor.stop(timeout=20)
    logger.info("Investment monitor daemon stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
