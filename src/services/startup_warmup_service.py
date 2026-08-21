# -*- coding: utf-8 -*-
"""Best-effort local cache warmup for the desktop Web application."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

WarmupTask = Tuple[str, Callable[[], Any]]


class StartupWarmupService:
    """Read common local views once so first navigation does not pay cold-I/O cost."""

    def __init__(self, tasks: Optional[Iterable[WarmupTask]] = None):
        self._tasks = tuple(tasks) if tasks is not None else None

    def _default_tasks(self) -> tuple[WarmupTask, ...]:
        # Import and construct lazily: merely importing api.app must remain fast.
        from src.services.essay_analysis_service import EssayAnalysisService
        from src.services.home_dashboard_service import HomeDashboardService
        from src.services.investment_monitor_service import InvestmentMonitorService

        essays = EssayAnalysisService()
        monitor = InvestmentMonitorService()
        home = HomeDashboardService(monitor=monitor, background_refresh=True)
        return (
            ("home-dashboard", lambda: home.dashboard(force=False)),
            ("essay-library-stats", essays.historical_backlog),
            (
                "essay-feed-first-page",
                lambda: essays.list_feed(days=0, page=1, page_size=20),
            ),
            ("investment-dashboard", lambda: monitor.dashboard(days=7)),
            (
                "investment-feed-first-page",
                lambda: monitor.list_events(days=7, page=1, page_size=100),
            ),
            ("watchlist-keyword-index", monitor.reindex_watchlist_keywords),
        )

    def warm(self) -> Dict[str, Any]:
        started_at = time.monotonic()
        completed = []
        failed: Dict[str, str] = {}
        for name, task in self._tasks or self._default_tasks():
            try:
                task()
                completed.append(name)
            except Exception as exc:  # noqa: BLE001 - one cold view must not affect another.
                failed[name] = type(exc).__name__
                logger.warning("Startup warmup skipped %s: %s", name, type(exc).__name__)
        result = {
            "completed": completed,
            "failed": failed,
            "elapsed_ms": round((time.monotonic() - started_at) * 1000),
        }
        logger.info(
            "Startup warmup finished: %d ready, %d skipped, %dms",
            len(completed),
            len(failed),
            result["elapsed_ms"],
        )
        return result
