# -*- coding: utf-8 -*-
"""Isolated process entry point for CPU-heavy essay quant precomputation."""

from __future__ import annotations

import argparse
import json

from src.services.essay_quant_service import EssayQuantService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-prices", action="store_true")
    args = parser.parse_args()
    result = EssayQuantService().run({
        "name": "后台全量机构预计算",
        "signal_direction": "bullish",
        "lookback_days": 730,
        "holding_periods": [5, 10, 20],
        "first_mention_window_days": 180,
        "min_importance": 0,
        "min_confidence": 0,
        "benchmark_code": "000300.SH",
        "portfolio_size": 10,
    }, refresh_prices=args.refresh_prices, max_symbols=60, persist=True, apply_adjustment=False)
    summary = result.get("summary") or {}
    quality = result.get("data_quality") or {}
    print(json.dumps({
        "run_id": result.get("run_id"),
        "event_count": summary.get("event_count", 0),
        "mature_event_count": summary.get("mature_event_count", 0),
        "ranked_group_count": len(result.get("research_group_rankings") or []),
        "generated_at": result.get("generated_at"),
        "resolved_symbol_count": quality.get("resolved_symbol_count", 0),
        "priced_symbol_count": quality.get("priced_symbol_count", 0),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
