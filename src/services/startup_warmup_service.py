# -*- coding: utf-8 -*-
"""Best-effort local cache warmup for the desktop Web application."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

WarmupTask = Tuple[str, Callable[[], Any]]
# Every symbol occupies both an overview and a full-context entry in the
# existing 128-entry workspace cache. Larger startup batches evict their own
# just-warmed landing views before the first visitor arrives.
_WATCHLIST_WARMUP_SYMBOL_LIMIT = 64
_MAINTENANCE_TASKS = frozenset({"zsxq-topic-url-backfill", "watchlist-keyword-index"})


def _warmup_workers(name: str, default: int) -> int:
    try:
        return max(1, min(int(os.getenv(name, str(default))), 8))
    except (TypeError, ValueError):
        return default


class StartupWarmupService:
    """Read common local views once so first navigation does not pay cold-I/O cost."""

    def __init__(self, tasks: Optional[Iterable[WarmupTask]] = None):
        self._tasks = tuple(tasks) if tasks is not None else None

    @staticmethod
    def _watchlist_symbols(monitor: Any) -> list[str]:
        """Read shared warmup targets without exposing private list membership."""
        from sqlalchemy import func, select
        from src.storage import UserAccount, UserWatchlistItem

        symbols = list(dict.fromkeys(monitor.equity_watchlist()))[:_WATCHLIST_WARMUP_SYMBOL_LIMIT]
        if len(symbols) >= _WATCHLIST_WARMUP_SYMBOL_LIMIT:
            return symbols
        # Reuse the already initialized database; no UserAccountService or
        # implicit account/source creation is needed for this read-only query.
        with monitor.repo.db.get_session() as session:
            personal_symbols = session.execute(
                select(UserWatchlistItem.symbol)
                .join(UserAccount, UserWatchlistItem.user_id == UserAccount.id)
                .where(UserAccount.status == "approved")
                .group_by(UserWatchlistItem.symbol)
                .order_by(func.max(UserWatchlistItem.created_at).desc(), UserWatchlistItem.symbol)
                .limit(_WATCHLIST_WARMUP_SYMBOL_LIMIT)
            ).scalars().all()
        for raw in personal_symbols:
            symbol = monitor._canonical_ts_code(raw)
            if symbol and symbol not in symbols:
                symbols.append(symbol)
            if len(symbols) >= _WATCHLIST_WARMUP_SYMBOL_LIMIT:
                break
        return symbols

    def _default_tasks(self) -> tuple[WarmupTask, ...]:
        # Import and construct lazily: merely importing api.app must remain fast.
        from src.data.stock_index_loader import get_stock_name_index_map
        from src.services.essay_analysis_service import EssayAnalysisService
        from src.services.financial_data_service import ResearchNoteService
        from src.services.investment_monitor_service import InvestmentMonitorService

        essays = EssayAnalysisService()
        research_notes = ResearchNoteService()
        monitor = InvestmentMonitorService()

        def warm_watchlist_workspaces() -> None:
            symbols = self._watchlist_symbols(monitor)
            if not symbols:
                return
            workers = _warmup_workers("WATCHLIST_WORKSPACE_WARMUP_WORKERS", 2)
            with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as executor:
                # The browser opens overview first. Warming only full leaves
                # that first screen cold despite a successful startup warmup.
                # Finish previews before full reads to preserve cache tokens.
                for view in ("overview", "full"):
                    list(executor.map(
                        lambda symbol: monitor.stock_workspace(symbol, days=183, refresh=True, view=view),
                        symbols,
                    ))

        essential: tuple[WarmupTask, ...] = (
            # This is maintenance, not request-path work.  It used to run every
            # time InvestmentMonitorService was constructed, forcing unrelated
            # page requests to scan the full essay-event table.
            ("zsxq-topic-url-backfill", monitor.repo.backfill_zsxq_topic_urls),
            ("stock-name-index", get_stock_name_index_map),
            ("essay-library-stats", essays.historical_backlog),
            ("essay-status", lambda: essays.progress(days=30)),
            (
                "essay-feed-first-page",
                lambda: essays.list_feed(days=0, page=1, page_size=20),
            ),
            (
                "research-note-audio-index",
                lambda: research_notes.list_audio_files(page=1, page_size=20),
            ),
            (
                "investment-feed-first-page",
                lambda: monitor.list_events(days=7, page=1, page_size=30),
            ),
            ("watchlist-stock-workspaces", warm_watchlist_workspaces),
        )
        if os.getenv("APP_STARTUP_WARMUP_PROFILE", "essential").strip().lower() != "full":
            return essential
        from src.services.home_dashboard_service import HomeDashboardService
        from src.services.industry_research_service import IndustryResearchService

        industry_research = IndustryResearchService()
        home = HomeDashboardService(monitor=monitor, background_refresh=True)
        return essential + (
            ("essay-deep-insights-short", lambda: essays.deep_insights(horizon="short")),
            ("essay-deep-insights-medium", lambda: essays.deep_insights(horizon="medium")),
            ("essay-deep-insights-long", lambda: essays.deep_insights(horizon="long")),
            (
                "industry-research-optical-module",
                lambda: industry_research.blueprint("光模块", lookback_days=730),
            ),
            ("home-dashboard", lambda: home.dashboard(force=False)),
            ("essay-dashboard", lambda: essays.dashboard(days=30)),
            ("investment-dashboard", lambda: monitor.dashboard(days=7)),
            ("super-watchlist", lambda: monitor.super_watchlist(days=183)),
            ("watchlist-keyword-index", monitor.reindex_watchlist_keywords),
        )

    def page_critical_tasks(self) -> tuple[WarmupTask, ...]:
        """**轻量**页面关键缓存：每轮保温都跑，成本约 5–10 秒。

        只放「重建便宜、但冷掉会让用户干等」的读路径。
        """
        from src.services.concept_theme_service import ConceptThemeService
        from src.services.essay_analysis_service import EssayAnalysisService
        from src.services.home_dashboard_service import HomeDashboardService
        from src.services.investment_monitor_service import InvestmentMonitorService

        monitor = InvestmentMonitorService()
        essays = EssayAnalysisService()
        home = HomeDashboardService(monitor=monitor, background_refresh=True)
        concepts = ConceptThemeService()

        # 保温只负责「让缓存不过期」，不能走强制重建：
        # 实测 force=True 一轮要 223 秒（会同步去上游拉数据）。
        return (
            ("home-dashboard", lambda: home.dashboard(force=False)),
            ("essay-deep-insights-short", lambda: essays.deep_insights(horizon="short")),
            ("concept-themes-overview", lambda: concepts.overview()),
        )

    def heavyweight_warm_tasks(self) -> tuple[WarmupTask, ...]:
        """**重量**页面缓存：单个就要 45–65 秒，不能每轮都跑。

        这两个的重建路径会去上游取行情，实测自选工作台 46s、超级自选 63s。
        策略是每轮保温只挑一个跑（轮转），把间隔摊到 2 轮以上：
        它们的 stale 窗口有 900 秒，只要在窗口内被刷新过，用户请求拿到的就是
        「旧数据 + 后台刷新」（毫秒级），而不是同步重建（几十秒）。

        更彻底的解法是把上游拉取从重建路径里拆出去交给后台 worker，
        那属于架构改造，不在保温机制的职责内。
        """
        from src.services.investment_monitor_service import InvestmentMonitorService

        monitor = InvestmentMonitorService()

        def warm_watchlist_workspaces() -> None:
            symbols = self._watchlist_symbols(monitor)
            if not symbols:
                return
            workers = _warmup_workers("WATCHLIST_WORKSPACE_WARMUP_WORKERS", 2)
            with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as executor:
                for view in ("overview", "full"):
                    list(executor.map(
                        lambda symbol: monitor.stock_workspace(symbol, days=183, refresh=False, view=view),
                        symbols,
                    ))

        return (
            ("watchlist-stock-workspaces", warm_watchlist_workspaces),
            ("super-watchlist", lambda: monitor.super_watchlist(days=183)),
        )

    def keep_warm(self, round_index: int = 0) -> Dict[str, Any]:
        """跑一轮「保温」。

        - 轻任务每轮都跑（5–10s）
        - 重任务按 round_index 轮转，每轮只跑一个（45–65s）

        任何一步失败都只记录，绝不影响线上请求。
        """
        warmed: list[str] = []
        failed: list[str] = []

        def run(name: str, fn: Any) -> None:
            try:
                fn()
                warmed.append(name)
            except Exception as exc:  # noqa: BLE001 - 保温失败不能影响服务
                failed.append(f"{name}:{type(exc).__name__}")
                logger.debug("keep-warm task %s failed: %s", name, exc)

        for name, fn in self.page_critical_tasks():
            run(name, fn)

        heavy = self.heavyweight_warm_tasks()
        if heavy:
            name, fn = heavy[round_index % len(heavy)]
            run(name, fn)

        return {"warmed": warmed, "failed": failed, "round": round_index}

    def warm(self) -> Dict[str, Any]:
        started_at = time.monotonic()
        tasks = list(self._tasks if self._tasks is not None else self._default_tasks())

        def run_task(item: WarmupTask) -> tuple[str, Optional[str], int]:
            name, task = item
            task_started_at = time.monotonic()
            failure = None
            try:
                task()
            except Exception as exc:  # noqa: BLE001 - one cold view must not affect another.
                failure = type(exc).__name__
                logger.warning("Startup warmup skipped %s: %s", name, type(exc).__name__)
            return name, failure, round((time.monotonic() - task_started_at) * 1000)

        # This task may write the event store. Complete it before concurrent
        # readers, so increasing Mac mini warmup throughput cannot create a
        # write/read maintenance stampede at startup.
        maintenance = [task for task in tasks if task[0] in _MAINTENANCE_TASKS]
        reads = [task for task in tasks if task[0] not in _MAINTENANCE_TASKS]
        outcomes = [run_task(task) for task in maintenance]
        workers = _warmup_workers("APP_STARTUP_WARMUP_WORKERS", 1)
        if workers == 1:
            outcomes.extend(run_task(task) for task in reads)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="startup-view-warmup") as executor:
                outcomes.extend(executor.map(run_task, reads))
        completed = [name for name, failure, _ in outcomes if failure is None]
        failed = {name: failure for name, failure, _ in outcomes if failure is not None}
        timings_ms = {name: elapsed for name, _, elapsed in outcomes}
        result = {
            "completed": completed,
            "failed": failed,
            "timings_ms": timings_ms,
            "elapsed_ms": round((time.monotonic() - started_at) * 1000),
        }
        logger.info(
            "Startup warmup finished: %d ready, %d skipped, %dms",
            len(completed),
            len(failed),
            result["elapsed_ms"],
        )
        return result
