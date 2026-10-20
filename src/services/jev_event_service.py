"""Owner-private event-time experiments, using the existing durable job store."""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import threading
import time
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from uuid import UUID

from src.repositories.jev_backtest_repo import encode, timestamp
from src.services.jev_backtest_service import (
    JevBacktestService, ReplayStopped, credential_status, load_credential,
)
from src.services.jev_decisions_client import JevClientError


def create_event_client():
    if os.getenv("JEV_RELAY_URL"):
        from src.services.jev_relay_client import JevEventRelayClient
        return JevEventRelayClient()
    from src.services.jev_cb_decisions_client import JevCBDecisionsClient
    return JevCBDecisionsClient(api_key=load_credential())


def event_capabilities():
    from src.services.jev_cb_data import availability, instruments
    from src.services.jev_decisions_client import JevDecisionsClient
    catalog = instruments()
    selected = next((i for i in catalog if i.get("status") == "available"), catalog[0] if catalog else {})
    config = {"symbol": selected.get("symbol", "113677"), "dataset_id": selected.get("dataset_id"),
              "start_date": selected.get("default_start_date", "2026-09-18"),
              "end_date": selected.get("default_end_date", "2026-09-18"),
              "event_granularity": selected.get("granularity", "1s")}
    quality = availability(config)
    model = JevDecisionsClient(api_key="")
    enabled = ["ohlcv"] + (["orderbook"] if quality.get("factor_availability", {}).get("orderbook", {}).get("available") else [])
    return {"configured": credential_status(), "model": model.model, "provider": "WorldSeek",
            "instrument": selected, "instruments": catalog, "data_availability": quality,
            "limits": {"max_cost_usd": .5, "max_events": 200000},
            "defaults": {**config, "base_position_value": 200000, "initial_cash": 200000,
                         "order_quantity": 100, "decision_interval_seconds": 60, "lookback_events": 60,
                         "confidence_threshold": .65, "latency_ms": 1000, "use_model_latency": True,
                         "order_ttl_ms": 10000, "participation_rate": .1, "commission_bps": 1,
                         "min_commission": 0, "slippage_bps": 2, "other_fee_bps": 0,
                         "restore_base_at_close": True, "factors": enabled, "max_cost_usd": .2,
                         "account_mode": selected.get("simulation_account", "cash_t0")}}


_RESEARCH_RESULT_MAX_BYTES = 32 * 1024 * 1024


def _read_research_json(filename, *, results=False, max_bytes=1000000):
    """Read bounded server artifacts without following directory/file symlinks."""
    root = os.getenv("JEV_CB_DATA_DIR")
    if not root:
        return None
    opened = []
    try:
        base = Path(root).resolve(strict=True)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(base, directory_flags)
        opened.append(directory)
        if results:
            directory = os.open("research-results", directory_flags, dir_fd=directory)
            opened.append(directory)
        descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                return None
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, AttributeError, NotImplementedError):
        return None
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def research_report():
    """Read a server-owned public research artifact; never trigger paid work."""
    fallback = {"status": "not_started", "title": "Jev 多轮对照研究", "rounds": [],
                "description": "先锁定时间划分与成本假设，再比较因子；测试区间不参与调参。",
                "conclusion": "尚无已完成的研究结果。", "limitations": []}
    value = _read_research_json("research.json")
    return value if value and isinstance(value.get("rounds"), list) else fallback


def _public_fields(value, names):
    def scalar(item):
        return item is None or isinstance(item, (str, bool, int)) or (isinstance(item, float) and math.isfinite(item))
    return {name: value[name] for name in names if name in value and (
        scalar(value[name]) or isinstance(value[name], list) and all(scalar(item) for item in value[name]))} if isinstance(value, dict) else {}


def research_round(task_id):
    """Expose an explicitly published sample, never read owner-private jobs."""
    try:
        canonical = str(UUID(str(task_id)))
    except (ValueError, TypeError, AttributeError):
        return None
    report = research_report()
    if not any(isinstance(row, dict) and row.get("task_id") == canonical for row in report["rounds"]):
        return None
    value = _read_research_json(f"{canonical}.json", results=True, max_bytes=_RESEARCH_RESULT_MAX_BYTES)
    if not value or value.get("task_id") != canonical or value.get("visibility") != "public_sample" or value.get("status") != "completed":
        return None
    result = value.get("result")
    if not isinstance(value.get("config"), dict) or not isinstance(value.get("usage"), dict) or not isinstance(result, dict):
        return None
    if not all(isinstance(result.get(key), list) for key in ("equity", "events", "signals", "trades")):
        return None
    # Explicit projections keep owners, request identifiers, source file paths,
    # prompts and internal provider metadata out of the shared sample endpoint.
    config_names = (
        "symbol", "dataset_id", "account_mode", "currency", "quantity_unit", "market", "start_date", "end_date",
        "base_position_value", "initial_cash", "event_granularity", "decision_interval_seconds", "lookback_events",
        "factors", "order_quantity", "latency_ms", "use_model_latency", "order_ttl_ms", "participation_rate",
        "commission_bps", "min_commission", "slippage_bps", "other_fee_bps", "restore_base_at_close",
        "confidence_threshold", "max_cost_usd", "evaluation_start", "evaluation_end")
    summary_names = (
        "initial_base_value", "initial_cash", "initial_total_equity", "initial_equity", "initial_base_quantity",
        "initial_base_price", "initial_cash_after_rounding", "final_equity", "hold_baseline_equity", "baseline_final_equity",
        "total_pnl", "incremental_t_pnl", "realized_pnl", "unrealized_pnl", "total_return_pct", "incremental_t_return_pct",
        "max_drawdown_pct", "fees", "trade_count", "cash", "inventory", "inventory_difference", "inventory_deviation_value",
        "cash_difference", "commission", "other_fees", "sell_tax", "slippage_cost", "filled_order_count", "fill_count",
        "model_calls", "unavailable_count", "uncertain_count", "model_call_limit_reached", "currency", "instrument_type", "simulation_account")
    traces = {
        "equity": ("timestamp", "event_index", "equity", "hold_baseline_equity", "cash", "position_quantity", "price",
                   "realized_pnl", "unrealized_pnl", "total_pnl", "incremental_t_pnl", "drawdown_pct", "inventory_difference", "inventory_deviation_value"),
        "trades": ("timestamp", "event_index", "side", "quantity", "price", "fee", "cash_after", "position_after", "reason", "pnl"),
        "signals": ("timestamp", "event_index", "action", "confidence", "reason", "status"),
        "events": ("timestamp", "event_index", "price", "volume", "stock_price", "sector_value", "market_value", "bid_price", "ask_price", "bid_volume", "ask_volume"),
    }
    public = _public_fields(value, ("task_id", "status", "progress", "message", "created_at", "started_at", "completed_at"))
    public.update(visibility="public_sample", config=_public_fields(value["config"], config_names),
                  usage=_public_fields(value["usage"], ("requests", "cached", "cost_usd", "cost_is_estimate")))
    quality = result.get("data_quality")
    warning_lines = quality.get("warnings") if isinstance(quality, dict) else []
    methodology = result.get("methodology")
    public["result"] = {"summary": _public_fields(result.get("summary"), summary_names),
                        **{key: [_public_fields(row, names) for row in result[key] if isinstance(row, dict)] for key, names in traces.items()},
                        "data_quality": {"warnings": [line for line in warning_lines if isinstance(line, str)] if isinstance(warning_lines, list) else []},
                        "methodology": [line for line in methodology if isinstance(line, str)] if isinstance(methodology, list) else []}
    return public


def engine_config(config, metadata):
    from src.services.jev_cb_t0_engine import CBT0Config
    instrument = metadata["instrument"]
    lot = int(instrument.get("lot_size", 10))
    if config["order_quantity"] % lot:
        raise ReplayStopped("每笔数量必须是标的最小交易单位的整数倍")
    account = instrument.get("simulation_account", "cash_t0")
    if config.get("account_mode") not in (None, account):
        raise ReplayStopped("账户模拟规则与所选标的不一致")
    values = {**config, "symbol": instrument["symbol"],
              "initial_base_value": config["base_position_value"],
              "lot_size": lot, "order_lots": config["order_quantity"] // lot,
              "instrument_type": instrument.get("instrument_type", "convertible_bond"),
              "currency": instrument.get("currency", "CNY"),
              "exchange_timezone": instrument.get("exchange_timezone", instrument.get("time_zone", "Asia/Shanghai")),
              "price_tick": instrument.get("price_tick", .001),
              "session_close": instrument.get("session_close", "15:00:00"),
              "simulation_account": "cash_cb" if account == "cash_t0" else account}
    return CBT0Config(**{k: v for k, v in values.items() if k in {f.name for f in fields(CBT0Config)}})


def public_result(result, events, metadata, config):
    """One UI schema for native engine results, without inventing realized P&L."""
    summary = result["summary"]
    visible_indices = {p["event_index"] for p in result["equity"]}
    equity = [{**p, "timestamp": p["time"], "hold_baseline_equity": p["baseline_equity"],
               "position_quantity": p["inventory"], "price": p["last_price"],
               "total_pnl": p["equity"] - summary["initial_equity"]} for p in result["equity"]]
    trades = [{**f, "timestamp": f["time"], "fee": f["commission"] + f.get("other_fee", 0),
               "position_after": f["inventory_after"]} for f in result["fills"]]
    return {**result, "summary": {**summary, "initial_base_value": config["base_position_value"],
                "initial_cash": config["initial_cash"], "initial_total_equity": summary["initial_equity"],
                "hold_baseline_equity": summary["baseline_final_equity"],
                "total_pnl": summary["final_equity"] - summary["initial_equity"],
                "fees": summary["commission"] + summary.get("other_fees", 0), "trade_count": summary["fill_count"]},
            "equity": equity, "trades": trades,
            "signals": [{**s, "timestamp": s["time"], "reason": s.get("execution_reason", "")} for s in result["signals"]],
            "events": [{**e, "event_index": index, "timestamp": e["time"], "price": e["last_price"], "volume": e.get("trade_volume")}
                       for index, e in enumerate(events) if index in visible_indices],
            "market_data": metadata}


class JevEventService(JevBacktestService):
    _instance = None
    _lock = threading.Lock()

    def __init__(self, repo=None, loader=None, client_factory=None):
        from src.services.jev_cb_data import load_cb_dataset
        super().__init__(repo=repo, loader=loader or load_cb_dataset, client_factory=client_factory or create_event_client)

    def submit(self, owner, config):
        from src.services.jev_cb_data import instruments
        item = next((i for i in instruments() if i["symbol"].split(".")[0] == config["symbol"].split(".")[0]
                     and (not config.get("dataset_id") or i.get("dataset_id") == config["dataset_id"])), None)
        if item:
            account = item.get("simulation_account", "cash_t0")
            if config.get("account_mode") not in (None, account):
                raise ValueError("账户模拟规则与所选标的不一致")
            config = {**config, "currency": item.get("currency", "CNY"),
                      "quantity_unit": item.get("quantity_unit", "bonds"), "market": item.get("market"),
                      "account_mode": account}
        return super().submit(owner, config)

    def _run(self, owner, task_id):
        from src.services.jev_cb_data import CBDataError
        from src.services.jev_cb_t0_engine import run_cb_t0_backtest
        task = self.repo.update(owner, task_id,
            {"status": "running", "started_at": timestamp(), "message": "核验事件数据、单位与时间覆盖"}, expected_status="queued")
        if not task:
            return
        config = task["config"]
        usage = {"requests": 0, "cached": 0, "cost_usd": 0., "cost_is_estimate": False}
        preview = {"equity": [], "signals": [], "trades": [], "events": [], "summary": {}}
        abort = None
        last_write = 0.
        try:
            events, contexts, metadata = self.loader(config)
            if config.get("evaluation_start"):
                first, last = (datetime.fromisoformat(config[k]) for k in ("evaluation_start", "evaluation_end"))
                metadata = {**metadata, "evaluation_start": first.isoformat(), "evaluation_end": last.isoformat()}
            execution = engine_config(config, metadata)
            instrument = metadata["instrument"]
            config = {**config, "currency": instrument.get("currency", "CNY"),
                      "quantity_unit": instrument.get("quantity_unit", "bonds"), "market": instrument.get("market"),
                      "account_mode": instrument.get("simulation_account", "cash_t0")}
            self.repo.update(owner, task_id, {"market_data": metadata}, expected_status="running")
            client = self.client_factory()

            def check_cancel():
                current = self.repo.get(owner, task_id)
                if not current or current["status"] != "running" or current.get("cancel_requested"):
                    raise ReplayStopped("任务已停止；已生成判断仍可复用")

            def decide(state):
                nonlocal abort
                try:
                    check_cancel()
                    digest = hashlib.sha256(encode({"version": client.cache_version, "model": client.model,
                                                   "endpoint": client.endpoint, "state": state}).encode()).hexdigest()
                    cached = self.repo.cached(owner, digest)
                    if cached:
                        usage["cached"] += 1
                        return {**cached, "usage": {**cached.get("usage", {}), "cached": True, "cost": 0}}
                    reserve = max(.000001, 32768 * client.input_price_per_million / 1e6)
                    if usage["cost_usd"] + reserve > config["max_cost_usd"]:
                        raise ReplayStopped("已到本次本地预算上限；后续模型调用已停止")
                    old_estimate = usage["cost_is_estimate"]
                    next_usage = {**usage, "requests": usage["requests"] + 1,
                                  "cost_usd": usage["cost_usd"] + reserve, "cost_is_estimate": True, "actual_cost_usd": None}
                    if not self.repo.update(owner, task_id, {"usage": next_usage, "message": "Jev 正在判断当前因子"},
                                            expected_status="running", require_not_cancelled=True):
                        raise ReplayStopped("任务已停止")
                    usage.update(next_usage)
                    try:
                        answer = client.decide(state)
                    except JevClientError as exc:
                        if exc.usage:
                            usage["cost_usd"] += exc.usage["cost"] - reserve
                            usage["cost_is_estimate"] = old_estimate or exc.usage.get("cost_is_estimate", False)
                            usage["actual_cost_usd"] = None if usage["cost_is_estimate"] else usage["cost_usd"]
                        raise ReplayStopped(f"Jev 调用中止（{exc.code}），未自动重试") from None
                    charge = answer["usage"]
                    usage["cost_usd"] += charge["cost"] - reserve
                    usage["cost_is_estimate"] = old_estimate or charge.get("cost_is_estimate", False)
                    usage["actual_cost_usd"] = None if usage["cost_is_estimate"] else usage["cost_usd"]
                    self.repo.cache(owner, digest, answer)
                    if usage["cost_usd"] > config["max_cost_usd"]:
                        raise ReplayStopped(("未核验预算估值" if usage["cost_is_estimate"] else "实际费用") + "超过本次预算上限，后续调用已停止")
                    return answer
                except ReplayStopped as exc:
                    abort = exc
                    raise
                except Exception:
                    abort = ReplayStopped("模型请求处理异常，后续调用已停止")
                    raise abort from None

            def progress(point):
                nonlocal last_write
                if abort:
                    raise abort
                p = point["equity_point"]
                preview["equity"].append({**p, "timestamp": p["time"], "hold_baseline_equity": p["baseline_equity"],
                    "position_quantity": p["inventory"], "price": p["last_price"],
                    "total_pnl": p["equity"] - config["initial_cash"] - config["base_position_value"]})
                for src, dst in (("latest_signal", "signals"), ("latest_fill", "trades")):
                    value = point.get(src)
                    identity = (value.get("event_index"), value.get("sequence"), value.get("time"), value.get("order_id")) if value else None
                    previous = preview[dst][-1] if preview[dst] else None
                    prior_identity = (previous.get("event_index"), previous.get("sequence"), previous.get("time"), previous.get("order_id")) if previous else None
                    if value and identity != prior_identity:
                        mapped = {**value, "timestamp": value["time"]}
                        if dst == "trades":
                            mapped.update(fee=value["commission"] + value.get("other_fee", 0), position_after=value["inventory_after"])
                        preview[dst].append(mapped)
                event = point.get("event")
                if event:
                    preview["events"].append({**event, "event_index": p["event_index"], "timestamp": event["time"], "price": event["last_price"], "volume": event.get("trade_volume")})
                if time.monotonic() - last_write < .7 and point["processed_events"] != point["total_events"]:
                    return
                check_cancel()
                last_write = time.monotonic()
                # A bounded live preview; the final result contains the complete trace.
                bounded = {**preview, "equity": preview["equity"][-2000:], "events": preview["events"][-2000:]}
                self.repo.update(owner, task_id, {"progress": round(point["processed_events"] / point["total_events"] * 99, 2),
                    "message": f"已回放 {point['processed_events']} / {point['total_events']} 个真实事件",
                    "preview": bounded, "usage": dict(usage)}, expected_status="running")

            result = run_cb_t0_backtest(events, execution, decide, contexts=contexts, progress=progress)
            check_cancel()
            result = public_result(result, events, metadata, config)
            result["data_quality"]["warnings"].extend(metadata.get("warnings", []))
            if not self.repo.update(owner, task_id, {"status": "completed", "progress": 100,
                "message": "研究完成，可回放或修改成本与条件重跑", "result": result, "preview": None,
                "usage": usage, "completed_at": timestamp()}, expected_status="running", require_not_cancelled=True):
                raise ReplayStopped("任务已停止；已生成判断仍可复用")
        except Exception as exc:
            current = self.repo.get(owner, task_id) or {}
            message = str(exc) if isinstance(exc, (ReplayStopped, CBDataError)) else "秒级回测失败，请检查数据或模型配置"
            self.repo.update(owner, task_id, {"status": "cancelled" if current.get("cancel_requested") else "failed",
                "error": message, "message": message, "usage": usage,
                "preview": {**preview, "equity": preview["equity"][-2000:], "events": preview["events"][-2000:]},
                "completed_at": timestamp()}, expected_status="running")
