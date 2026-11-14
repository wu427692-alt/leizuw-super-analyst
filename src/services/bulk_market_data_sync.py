# -*- coding: utf-8 -*-
"""全市场行情批量同步服务。

为什么需要这个服务
------------------
原有采集链路是「按股票拉」：自选股逐只请求，每天只能覆盖约 2100 只，
而 A 股实际有 5500+ 只在交易。缺口不是抓取失败，是覆盖策略本身就有上限。

Tushare 的 `daily` / `daily_basic` 支持按交易日一次返回全市场，
一次请求 0.4~1 秒拿到 5500+ 行。按日期维度批量拉取，同样的配额
能覆盖 2.5 倍以上的标的，且天然对齐交易日历，不会漏掉任何一天。

设计要点
--------
1. **按交易日推进，不按股票推进**：一次调用覆盖全市场，规避逐股请求的配额消耗。
2. **缺口自愈**：不存同步状态表，每次都用「某日股票数是否低于阈值」判断缺口，
   幂等且能在任何中断后续跑。
3. **写入走 SQLite UPSERT**：`(code, date)` 唯一约束，重复执行不会产生脏数据，
   也不会覆盖掉其它来源写入的技术指标列。
4. **限速保护**：Tushare 免费额度约 500 次/分钟，单次请求间隔不足时主动 sleep。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.storage import DatabaseManager, StockDaily

logger = logging.getLogger(__name__)

# 单批 UPSERT 行数。SQLite 对绑定参数数量有上限，
# 500 行 × 21 列 = 10500 个参数，仍在安全范围内且吞吐较好。
_CHUNK_ROWS = 500

# A 股全市场约 5570 只，低于这个数视为该交易日数据不完整。
_DEFAULT_COVERAGE_THRESHOLD = 5400

# 单日请求最快间隔，保护 Tushare 免费额度（约 500 次/分钟）。
_MIN_REQUEST_INTERVAL = 0.35


class BulkMarketDataSyncService:
    """按交易日批量同步全市场日线与估值指标。"""

    _instance: Optional["BulkMarketDataSyncService"] = None
    _instance_lock = threading.Lock()

    def __init__(self, *, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or DatabaseManager.get_instance()
        self._pro = None
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "BulkMarketDataSyncService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ---------------------------------------------------------------- utils
    @property
    def pro(self):
        if self._pro is None:
            import tushare as ts

            token = os.getenv("TUSHARE_TOKEN", "").strip()
            if not token:
                raise RuntimeError("TUSHARE_TOKEN 未配置，无法批量同步行情")
            self._pro = ts.pro_api(token)
        return self._pro

    @staticmethod
    def _to_db_date(trade_date: str) -> date:
        return date(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:8]))

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return None if f != f else f  # NaN → None

    # ------------------------------------------------------------ 交易日历
    def trade_days(self, start: str, end: str) -> List[str]:
        """返回 [start, end] 区间内的交易日（YYYYMMDD 字符串，升序）。"""
        cal = self.pro.query(
            "trade_cal", exchange="SSE", start_date=start, end_date=end,
            fields="cal_date,is_open",
        )
        return sorted(
            str(r["cal_date"]) for _, r in cal.iterrows() if int(r["is_open"]) == 1
        )

    def expected_counts(self, trade_days: Sequence[str]) -> Dict[str, int]:
        """估算每个交易日「应该有多少只股票在交易」。

        不能用固定的 5400 当阈值：2015 年 A 股只有约 2800 只上市，
        拿今天的数量去衡量历史日期，会把所有历史日期都误判成数据不全，
        白白重拉几千天。这里用 `stock_basic` 的上市/退市日期推算每天的在市数量。
        """
        if not trade_days:
            return {}
        basics = self.pro.query(
            "stock_basic", exchange="", list_status="L",
            fields="ts_code,list_date",
        )
        listed = self.pro.query(
            "stock_basic", exchange="", list_status="D",
            fields="ts_code,list_date,delist_date",
        )
        spans: List[Tuple[str, Optional[str]]] = []
        for rec in basics.to_dict("records"):
            spans.append((str(rec.get("list_date") or ""), None))
        for rec in listed.to_dict("records"):
            spans.append((
                str(rec.get("list_date") or ""),
                str(rec.get("delist_date") or "") or None,
            ))

        expected: Dict[str, int] = {}
        for day in trade_days:
            count = 0
            for list_date, delist_date in spans:
                if not list_date or list_date > day:
                    continue
                if delist_date and delist_date < day:
                    continue
                count += 1
            expected[day] = count
        return expected

    def sparse_days(
        self,
        trade_days: Sequence[str],
        threshold: int = _DEFAULT_COVERAGE_THRESHOLD,
        *,
        adaptive: bool = True,
    ) -> List[str]:
        """挑出 DB 中覆盖股票数明显不足的交易日。

        这是本服务唯一的「状态」来源：数据库自己就是进度条，
        中断后重跑只会补真正缺的日期，不会重复劳动。

        Args:
            adaptive: 按当日实际在市股票数判断（覆盖 < 90% 视为缺口）。
                关闭后回退到固定阈值，仅适用于近月数据。
        """
        if not trade_days:
            return []
        days_as_date = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in trade_days]
        placeholders = ",".join(f":d{i}" for i in range(len(days_as_date)))
        params = {f"d{i}": v for i, v in enumerate(days_as_date)}
        with self.db.get_session() as session:
            rows = session.execute(
                text(
                    f"SELECT date(date) AS d, COUNT(DISTINCT code) AS n "
                    f"FROM stock_daily WHERE date(date) IN ({placeholders}) GROUP BY d"
                ),
                params,
            ).fetchall()
        counts = {str(r[0]).replace("-", ""): int(r[1]) for r in rows}

        if not adaptive:
            return [d for d in trade_days if counts.get(d, 0) < threshold]

        expected = self.expected_counts(trade_days)
        sparse: List[str] = []
        for day in trade_days:
            have = counts.get(day, 0)
            want = expected.get(day) or 0
            # 在市数量未知时退回固定阈值；否则要求覆盖 ≥ 90%。
            if want >= 100:
                if have < want * 0.9:
                    sparse.append(day)
            elif have < threshold:
                sparse.append(day)
        return sparse

    # -------------------------------------------------------------- 抓取
    def _fetch_day(self, trade_date: str) -> Tuple[Optional[Any], Optional[Any]]:
        """拉取一天的日线与估值指标；失败重试 3 次后返回 (None, None)。"""
        daily_df = None
        for attempt in range(3):
            try:
                daily_df = self.pro.query("daily", trade_date=trade_date)
                break
            except Exception as exc:  # noqa: BLE001 - 上游异常按重试处理
                if attempt == 2:
                    logger.warning("[bulk-sync] daily %s 拉取失败: %s", trade_date, exc)
                    return None, None
                time.sleep(1 + attempt)

        basic_df = None
        for attempt in range(2):
            try:
                basic_df = self.pro.query(
                    "daily_basic", trade_date=trade_date,
                    fields="ts_code,trade_date,turnover_rate,pe,pb,total_mv,circ_mv",
                )
                break
            except Exception as exc:  # noqa: BLE001 - 估值缺失不应阻断日线落库
                if attempt == 1:
                    logger.debug("[bulk-sync] daily_basic %s 缺失: %s", trade_date, exc)
                time.sleep(0.5)
        return daily_df, basic_df

    def _build_rows(self, daily_df: Any, basic_df: Any, trade_date: str) -> List[Dict[str, Any]]:
        """把 daily 与 daily_basic 合并成 stock_daily 行。"""
        now = datetime.now()
        db_date = self._to_db_date(trade_date)

        basic_map: Dict[str, Dict[str, Any]] = {}
        if basic_df is not None and not basic_df.empty:
            basic_map = {
                str(rec["ts_code"]): rec for rec in basic_df.to_dict("records")
            }

        rows: List[Dict[str, Any]] = []
        # 用 to_dict 而不是 iterrows：iterrows 每行构造 Series，5500 行会慢一个数量级。
        for row in daily_df.to_dict("records"):
            ts_code = str(row["ts_code"])
            basic = basic_map.get(ts_code, {})
            rows.append({
                "code": ts_code.split(".")[0],
                "date": db_date,
                "open": self._safe_float(row.get("open")),
                "high": self._safe_float(row.get("high")),
                "low": self._safe_float(row.get("low")),
                "close": self._safe_float(row.get("close")),
                "volume": self._safe_float(row.get("vol")),
                "amount": self._safe_float(row.get("amount")),
                "pct_chg": self._safe_float(row.get("pct_chg")),
                "pe": self._safe_float(basic.get("pe")),
                "pb": self._safe_float(basic.get("pb")),
                "total_mv": self._safe_float(basic.get("total_mv")),
                "circ_mv": self._safe_float(basic.get("circ_mv")),
                "turnover_rate": self._safe_float(basic.get("turnover_rate")),
                "data_source": "tushare_bulk",
                "created_at": now,
                "updated_at": now,
            })
        return rows

    # -------------------------------------------------------------- 写入
    def _upsert(self, rows: Sequence[Dict[str, Any]]) -> int:
        """批量 UPSERT 到 stock_daily。

        只更新本服务负责的列，避免覆盖其它采集链路算好的技术指标。

        写入是批量回填的主要瓶颈（5000+ 行/天，且大部分是 UPDATE，需要维护
        唯一索引）。这里做两处 SQLite 调优：
        - `synchronous=NORMAL`：WAL 模式下断电最多丢最后一个事务，但不再每
          次提交都 fsync，实测写入耗时降低约一半；
        - `wal_autocheckpoint=0`：批量期间不做自动检查点，避免写放大，
          由 `checkpoint()` 在整批结束后统一落盘。
        """
        if not rows:
            return 0
        update_cols = (
            "open", "high", "low", "close", "volume", "amount", "pct_chg",
            "pe", "pb", "total_mv", "circ_mv", "turnover_rate",
            "data_source", "updated_at",
        )
        written = 0
        with self.db.get_session() as session:
            try:
                session.execute(text("PRAGMA synchronous=NORMAL"))
                session.execute(text("PRAGMA wal_autocheckpoint=0"))
            except Exception:  # noqa: BLE001 - 调优失败不影响正确性
                logger.debug("[bulk-sync] PRAGMA 调优被跳过")
            for start in range(0, len(rows), _CHUNK_ROWS):
                chunk = list(rows[start:start + _CHUNK_ROWS])
                stmt = sqlite_insert(StockDaily).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["code", "date"],
                    set_={col: getattr(stmt.excluded, col) for col in update_cols},
                )
                session.execute(stmt)
                written += len(chunk)
            session.commit()
        return written

    def checkpoint(self) -> None:
        """把 WAL 落回主库。批量回填结束时调用，避免 WAL 无限增长。"""
        try:
            with self.db.get_session() as session:
                session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                session.commit()
        except Exception as exc:  # noqa: BLE001 - 检查点失败不是数据错误
            logger.debug("[bulk-sync] checkpoint 跳过: %s", exc)

    # -------------------------------------------------------------- 主流程
    def sync(
        self,
        *,
        start: str,
        end: Optional[str] = None,
        threshold: int = _DEFAULT_COVERAGE_THRESHOLD,
        only_sparse: bool = True,
        adaptive: bool = True,
        max_days: Optional[int] = None,
        dry_run: bool = False,
        progress_every: int = 10,
    ) -> Dict[str, Any]:
        """同步 [start, end] 区间的全市场日线。

        Args:
            only_sparse: 只补覆盖不足的交易日（增量模式）；False 则全部重拉。
            max_days: 单次最多处理多少个交易日，用于限流分片。
        """
        end = end or datetime.now().strftime("%Y%m%d")
        with self._lock:
            days = self.trade_days(start, end)
            target = (
                self.sparse_days(days, threshold, adaptive=adaptive)
                if only_sparse else list(days)
            )
            if max_days:
                target = target[:max_days]

            report: Dict[str, Any] = {
                "requested_start": start,
                "requested_end": end,
                "trade_days": len(days),
                "pending_days": len(target),
                "synced_days": 0,
                "rows_written": 0,
                "failed_days": [],
                "dry_run": dry_run,
            }
            if dry_run or not target:
                return report

            started = time.time()
            for index, trade_date in enumerate(target, start=1):
                t0 = time.time()
                daily_df, basic_df = self._fetch_day(trade_date)
                if daily_df is None or daily_df.empty:
                    report["failed_days"].append(trade_date)
                    continue
                rows = self._build_rows(daily_df, basic_df, trade_date)
                report["rows_written"] += self._upsert(rows)
                report["synced_days"] += 1

                elapsed = time.time() - t0
                if elapsed < _MIN_REQUEST_INTERVAL:
                    time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

                if index % progress_every == 0 or index == len(target):
                    done = index
                    avg = (time.time() - started) / done
                    logger.info(
                        "[bulk-sync] %d/%d %s: %d 行 | 累计 %d 行 | ETA %.0fs",
                        done, len(target), trade_date, len(rows),
                        report["rows_written"], avg * (len(target) - done),
                    )

            report["elapsed_s"] = round(time.time() - started, 1)
            self.checkpoint()
            if report["failed_days"]:
                logger.warning("[bulk-sync] %d 个交易日拉取失败", len(report["failed_days"]))
            return report

    def coverage_report(self, *, days: int = 30) -> Dict[str, Any]:
        """返回最近若干交易日的覆盖情况，用于巡检和看板。"""
        end = datetime.now().strftime("%Y%m%d")
        start_dt = datetime.now()
        days_list = self.trade_days((start_dt).strftime("%Y%m%d"), end)
        # 向前取 days 个交易日
        probe_start = (start_dt.replace(year=start_dt.year - 1)).strftime("%Y%m%d")
        all_days = self.trade_days(probe_start, end)
        window = all_days[-days:] if len(all_days) > days else all_days
        sparse = set(self.sparse_days(window, adaptive=True))
        return {
            "window": f"{window[0]}~{window[-1]}" if window else None,
            "checked": len(window),
            "complete": len(window) - len(sparse),
            "sparse": sorted(sparse),
        }
