"""Small process supervisor for the cloud investment-monitor worker."""

from __future__ import annotations

import logging
import os
import signal
import threading
from typing import Any, Optional

from src.logging_config import setup_logging
from src.services.investment_monitor_worker import InvestmentMonitorWorker


logger = logging.getLogger(__name__)


def run(worker: Optional[InvestmentMonitorWorker] = None) -> int:
    """Run low-priority collectors outside the latency-sensitive web process."""
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
    concept_worker: Optional[Any] = None
    if os.getenv("CONCEPT_THEME_AUTO_START", "false").strip().lower() in {"1", "true", "yes", "on"}:
        from src.services.concept_theme_worker import ConceptThemeWorker

        concept_worker = ConceptThemeWorker.get_instance()
        concept_state = concept_worker.start()
        if not concept_state.get("running"):
            logger.error("Concept theme worker could not start: %s", concept_state)
            monitor.stop(timeout=20)
            return 1
    logger.info("Background collector daemon started")
    try:
        while not stop.wait(15):
            if not monitor.status().get("running"):
                logger.error("Investment monitor thread stopped unexpectedly")
                return 1
            if concept_worker is not None and not concept_worker.status().get("running"):
                logger.error("Concept theme thread stopped unexpectedly")
                return 1
    finally:
        if concept_worker is not None:
            concept_worker.stop(timeout=20)
        monitor.stop(timeout=20)
    logger.info("Background collector daemon stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
