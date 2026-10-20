#!/usr/bin/env python3
"""Bounded, predeclared Jev ablation and chronological holdout campaign.

This is an explicit paid command, never launched by a GET, scheduler or import.
It stores all attempts, including losses. Public sample data only enter Jev.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-cost-usd", type=float, default=.5)
    args = parser.parse_args()
    if not 0 < args.max_cost_usd <= 1:
        raise SystemExit("Campaign budget must be between zero and $1")
    os.environ["JEV_CB_DATA_DIR"] = str(Path(args.data_dir).resolve())
    os.environ["DATABASE_PATH"] = str(Path(args.database).resolve())
    from api.v1.schemas.jev_convertible_t import JevEventRequest
    from src.services.jev_event_service import JevEventService
    from src.services.jev_cb_data import load_cb_dataset
    from src.services.jev_cb_decisions_client import JevCBDecisionsClient

    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    report_path = Path(args.data_dir) / "research.json"
    owner, campaign = "event:integration", str(uuid4())
    service = JevEventService()
    service.pool.shutdown(wait=True)
    base = dict(symbol="NVDA", dataset_id="nvda-xnas-20250916-1s",
        start_date="2025-09-16", end_date="2025-09-16", event_granularity="1s",
        base_position_value=200000, initial_cash=200000, order_quantity=100,
        lookback_events=61, decision_interval_seconds=30, confidence_threshold=.55,
        commission_bps=1, other_fee_bps=.1, min_commission=0, slippage_bps=2,
        latency_ms=1000, use_model_latency=True, participation_rate=.1,
        restore_base_at_close=True, account_mode="unlevered_margin")
    splits = {"train": ("09:40:00", "10:10:00"), "validation": ("11:00:00", "11:30:00"),
              "test": ("14:00:00", "15:55:00")}
    _, _, data = load_cb_dataset({**base, "factors": ["ohlcv"]})
    manifest = {"campaign_id": campaign, "created_at": datetime.now(timezone.utc).isoformat(),
        "base": base, "splits": splits, "threshold_grid": [.45, .55, .65], "quantity_grid": [25, 100],
        "selection_rule": "Choose training-only maximum incremental T P&L; if all non-positive, report no demonstrated edge. Validation/test never reselect.",
        "data_hash": data.get("hash"), "source_sha256": data.get("source_sha256"),
        "question_sha256": hashlib.sha256(json.dumps(JevCBDecisionsClient(api_key="")._build_questions(), sort_keys=True).encode()).hexdigest(),
        "cache_version": JevCBDecisionsClient.cache_version, "max_cost_usd": args.max_cost_usd}
    (out / f"{campaign}-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    report = {"status": "running", "title": "NVDA 秒级 Jev：因子、时段与成本对照",
        "description": "预先锁定时间划分；仅训练段选择因子和参数，后两段不再调参。美元无杠杆保证金式仿真账户。",
        "rounds": [], "conclusion": "研究进行中。", "campaign_id": campaign,
        "limitations": ["只有一个标的、一个历史交易日，不能证明跨日稳健收益。",
            "单一纳斯达克场所报价与成交，并非全市场 NBBO；不重建真实排队、隐藏流动性或冲击。",
            "板块、指数同期数据缺失，本轮仅比较量价与最优盘口；没有用代理数据冒充。",
            "初始底仓20万美元、现金20万美元；并非人民币换汇，也不模拟美股现金账户结算。",
            "Jev 分类置信度并非盈利概率；模型预训练和单日重复研究偏差仍存在。",
            "所有盈亏扣除设定的佣金、额外费用及滑点；费用是研究假设，不是具体券商账单。"]}
    spent = 0.

    def save():
        report["updated_at"] = datetime.now(timezone.utc).isoformat()
        report["total_model_cost_usd"] = spent
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        temporary.replace(report_path)

    def run(name, split, factors, **conditions):
        nonlocal spent
        remaining = min(.5, args.max_cost_usd - spent)
        if remaining <= .002:
            raise RuntimeError("Campaign budget reached")
        start, end = splits[split]
        request = JevEventRequest(request_id=uuid4(), **(base | conditions), factors=factors,
            evaluation_start=f"2025-09-16T{start}-04:00", evaluation_end=f"2025-09-16T{end}-04:00",
            max_cost_usd=remaining).model_dump(mode="json")
        # Synchronous worker uses the same durable job, cache and cost gate as UI.
        request.update(currency="USD", quantity_unit="shares", market="XNAS")
        task, _ = service.repo.create(owner, request)
        service.repo.update(owner, task["task_id"], {"research_campaign": campaign, "research_round": name})
        print(json.dumps({"round": name, "status": "running", "task_id": task["task_id"]}), flush=True)
        service._run(owner, task["task_id"])
        task = service.repo.get(owner, task["task_id"])
        spent += task["usage"]["cost_usd"]
        path = out / f"{task['task_id']}.json"
        path.write_text(json.dumps(task, ensure_ascii=False))
        if task["status"] != "completed":
            report["rounds"].append({"id": task["task_id"], "name": name, "split": split,
                "status": task["status"], "notes": task.get("error", ""), "model_cost_usd": task["usage"]["cost_usd"]})
            save()
            raise RuntimeError(task.get("error", "Incomplete research run"))
        summary = task["result"]["summary"]
        latencies = [s["latency_ms"] for s in task["result"]["signals"] if s.get("latency_ms") is not None]
        row = {"id": task["task_id"], "task_id": task["task_id"], "name": name, "split": split,
            "period": f"2025-09-16 {start}–{end} EDT", "symbol": "NVDA", "currency": "USD", "status": "completed",
            "factors": factors, "incremental_t_pnl": summary["incremental_t_pnl"], "total_pnl": summary["total_pnl"],
            "max_drawdown_pct": summary["max_drawdown_pct"], "trade_count": summary["trade_count"],
            "model_requests": task["usage"]["requests"], "model_cached": task["usage"]["cached"],
            "model_cost_usd": task["usage"]["cost_usd"], "median_latency_ms": statistics.median(latencies) if latencies else None,
            "p95_latency_ms": sorted(latencies)[int(.95 * (len(latencies)-1))] if latencies else None,
            "conditions": {k: request[k] for k in ("order_quantity", "confidence_threshold", "commission_bps", "slippage_bps", "latency_ms", "participation_rate")},
            "inventory_difference": summary["inventory_difference"], "restore_gaps": summary["restore_gaps"],
            "notes": "增量收益含相对底仓的期末市值差，不等同已实现利润。"}
        report["rounds"].append(row)
        save()
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row

    save()
    try:
        candidates = []
        for factors, label in ((["ohlcv"], "量价"), (["ohlcv", "orderbook"], "量价＋盘口")):
            for confidence in manifest["threshold_grid"]:
                for quantity in manifest["quantity_grid"]:
                    candidates.append(run(f"训练 · {label} · 阈值{confidence} · {quantity}股", "train", factors,
                                          confidence_threshold=confidence, order_quantity=quantity))
        winner = max(candidates, key=lambda row: (row["incremental_t_pnl"], -row["max_drawdown_pct"]))
        selection = {"order_quantity": winner["conditions"]["order_quantity"],
                     "confidence_threshold": winner["conditions"]["confidence_threshold"]}
        report["locked_selection"] = {"based_on": "train_only", "round_id": winner["id"], "factors": winner["factors"], **selection}
        save()
        validation = run("验证 · 锁定方案", "validation", winner["factors"], **selection)
        test = run("最终测试 · 锁定方案", "test", winner["factors"], **selection)
        stress = run("最终测试 · 成本与延迟压力", "test", winner["factors"], **selection,
                     commission_bps=2, slippage_bps=5, latency_ms=5000, participation_rate=.05)
        report["status"] = "completed"
        survives = all(row["incremental_t_pnl"] > 0 and row["trade_count"] > 0 for row in (winner, validation, test, stress))
        report["conclusion"] = ("本单日样本的锁定方案在验证、最终测试及压力条件下均有正增量收益；仍需跨日与其他标的验证。"
            if survives else "尚未验证稳定的净增量收益：训练、验证、最终测试或压力条件至少一项未通过。保留全部结果，不据此推荐实盘。")
        report["validated_positive_edge"] = survives
    except Exception as exc:
        report["status"] = "failed"
        report["conclusion"] = str(exc) if isinstance(exc, RuntimeError) else "研究运行中止，已完成轮次与费用保留。"
        save()
        raise
    save()
    print(json.dumps({"campaign": campaign, "status": report["status"], "cost_usd": spent,
                      "conclusion": report["conclusion"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
