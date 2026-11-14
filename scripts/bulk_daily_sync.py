#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场日线批量同步 CLI。

实现本体在 `src/services/bulk_market_data_sync.py`，这里只是命令行入口，
避免脚本和服务两份平行实现各自漂移。

用法：
    python scripts/bulk_daily_sync.py --dry-run           # 只看缺口，不写库
    python scripts/bulk_daily_sync.py                     # 补最近 60 个交易日
    python scripts/bulk_daily_sync.py --days 490          # 补近 2 年
    python scripts/bulk_daily_sync.py --full              # 全量历史
    python scripts/bulk_daily_sync.py --start 20240101    # 指定起点
    python scripts/bulk_daily_sync.py --no-adaptive       # 用固定阈值判断缺口
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.services.bulk_market_data_sync import BulkMarketDataSyncService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bulk_daily_sync")


def main() -> int:
    parser = argparse.ArgumentParser(description="全市场日线批量同步")
    parser.add_argument("--days", type=int, default=60,
                        help="向前补多少个交易日 (默认 60)")
    parser.add_argument("--start", default=None,
                        help="起始日期 YYYYMMDD (优先于 --days)")
    parser.add_argument("--end", default=None,
                        help="结束日期 YYYYMMDD (默认今天)")
    parser.add_argument("--full", action="store_true",
                        help="全量历史 (从 20120104 开始)")
    parser.add_argument("--max-days", type=int, default=None,
                        help="本次最多处理多少个交易日 (分片限流用)")
    parser.add_argument("--no-adaptive", action="store_true",
                        help="关闭自适应阈值，改回固定 5400 只判断缺口")
    parser.add_argument("--dry-run", action="store_true",
                        help="只分析缺口，不写入")
    args = parser.parse_args()

    end = args.end or datetime.now().strftime("%Y%m%d")
    service = BulkMarketDataSyncService()

    if args.full:
        start = "20120104"
    elif args.start:
        start = args.start
    else:
        # 交易日历向前推 N 个交易日
        probe = datetime.now().replace(year=datetime.now().year - 2).strftime("%Y%m%d")
        days = service.trade_days(probe, end)
        start = days[-args.days] if len(days) > args.days else days[0]

    logger.info("同步范围: %s ~ %s", start, end)
    report = service.sync(
        start=start,
        end=end,
        only_sparse=True,
        max_days=args.max_days,
        dry_run=args.dry_run,
        adaptive=not args.no_adaptive,
    )
    logger.info("结果: %s", report)
    return 0 if not report.get("failed_days") else 1


if __name__ == "__main__":
    raise SystemExit(main())
