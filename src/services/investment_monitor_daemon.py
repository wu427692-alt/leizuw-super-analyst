"""Small process supervisor for the cloud investment-monitor worker."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
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

    # 每 10 分钟核对一次「每个源最后一条数据是什么时候」。
    # 调度成功不代表有数据：上游延迟、分页截断、配置失效都会让数据静默停住。
    freshness_every = max(60, int(os.getenv("INVESTMENT_MONITOR_FRESHNESS_AUDIT_SEC", "600")))
    freshness_after = max(15, int(os.getenv("INVESTMENT_MONITOR_FRESHNESS_STALE_MIN", "90")))
    freshness_due = time.monotonic() + 60

    def audit_freshness() -> None:
        # worker 的每轮循环里临时构造服务，这里同样现取一个：只读巡检，不共享状态。
        from src.services.investment_monitor_service import InvestmentMonitorService

        try:
            report = InvestmentMonitorService().audit_source_freshness(stale_after_minutes=freshness_after)
            stalled = report.get("stalled") or []
            if stalled:
                logger.warning(
                    "[investment-monitor] freshness audit: %s of %s sources stalled: %s",
                    len(stalled), report.get("checked"),
                    ", ".join(f"{item['name']}({item.get('stalled_minutes')}min)" for item in stalled[:6]),
                )
        except Exception as exc:  # noqa: BLE001 - 巡检不能拖垮采集
            logger.warning("freshness audit failed: %s: %s", type(exc).__name__, str(exc)[:200])

    # ---------------------------------------------------------------- 全市场行情批量同步
    # 原有链路按股票拉，每天只能覆盖约 2100 只，而 A 股实际有 5500+ 只在交易。
    # 这里按交易日拉全市场（一次请求 0.4~1s 覆盖 5500+ 行），把覆盖补满。
    bulk_enabled = os.getenv("BULK_MARKET_SYNC_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    bulk_boot_days = max(1, int(os.getenv("BULK_MARKET_SYNC_BOOTSTRAP_DAYS", "30")))
    bulk_every = max(1800, int(os.getenv("BULK_MARKET_SYNC_INTERVAL_SEC", "3600")))
    bulk_after_close = os.getenv("BULK_MARKET_SYNC_AFTER_CLOSE", "16:10").strip()
    bulk_next = time.monotonic() + 120   # 启动后 2 分钟做一次补缺
    bulk_bootstrapped = not bulk_enabled

    def sync_bulk_market() -> None:
        """补全覆盖不足的交易日；失败只记录，不影响采集主循环。"""
        from datetime import datetime as _dt
        from src.services.bulk_market_data_sync import BulkMarketDataSyncService

        try:
            service = BulkMarketDataSyncService()
            end = _dt.now().strftime("%Y%m%d")
            # 启动时回溯一段，之后每轮只补最近的缺口
            lookback = max(bulk_boot_days, 20) if bulk_bootstrapped else 20
            probe = _dt.now().replace(year=_dt.now().year - 1).strftime("%Y%m%d")
            days = service.trade_days(probe, end)
            start = days[-lookback] if len(days) > lookback else days[0]
            report = service.sync(start=start, end=end, max_days=lookback)
            if report.get("synced_days"):
                logger.info(
                    "[bulk-sync] 补齐 %s 个交易日, 写入 %s 行",
                    report.get("synced_days"), report.get("rows_written"),
                )
            if report.get("failed_days"):
                logger.warning("[bulk-sync] 失败 %s 天: %s",
                               len(report["failed_days"]), report["failed_days"][:5])
        except Exception as exc:  # noqa: BLE001 - 批量同步不能拖垮采集
            logger.warning("bulk market sync failed: %s: %s", type(exc).__name__, str(exc)[:200])

    try:
        while not stop.wait(15):
            if not monitor.status().get("running"):
                logger.error("Investment monitor thread stopped unexpectedly")
                return 1
            if time.monotonic() >= freshness_due:
                freshness_due = time.monotonic() + freshness_every
                audit_freshness()
            if bulk_enabled and time.monotonic() >= bulk_next:
                bulk_next = time.monotonic() + bulk_every
                bulk_bootstrapped = True
                sync_bulk_market()
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
