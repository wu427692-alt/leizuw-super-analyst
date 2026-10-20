"""Bounded, explicit live-data paper run; quote polling continues during inference."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import statistics
import time
from uuid import uuid4

from data_provider.jev_live_snapshot import BoundedSinaLiveSnapshot, session_status
from src.services.jev_cb_decisions_client import JevCBDecisionsClient
from src.services.jev_decisions_client import JevClientError
from src.services.jev_live_t_engine import LiveTEngine, LiveTConfig
from src.services.jev_live_t_features import build_live_state
from src.services.jev_live_t_store import LiveTStore


def now():
    return datetime.now(timezone.utc).isoformat()


def project_account(summary):
    if not summary["initialized"]:
        return None
    names = {"initial_equity": "initial_equity", "current_equity": "equity",
             "hold_baseline_equity": "baseline_equity", "total_pnl": "total_pnl",
             "incremental_pnl": "net_increment", "initial_shares": "initial_base_quantity",
             "total_shares": "inventory", "sellable_yesterday_shares": "sellable_shares",
             "today_frozen_shares": "frozen_shares", "cash": "cash", "inventory_gap_shares": "inventory_gap",
             "closed_t_net_pnl": "closed_t_net_pnl", "cost_reduction_per_initial_share": "closed_t_cost_reduction_per_initial_share",
             "commission": "commission", "sell_tax": "sell_tax", "transfer_fee": "transfer_fees",
             "slippage_cost": "slippage_cost"}
    account = {key: summary[value] for key, value in names.items()}
    account["total_fees"] = summary["commission"] + summary["sell_tax"] + summary["transfer_fees"]
    account["actual_cost_basis"] = None
    return account


def update_projection(run, engine):
    result = engine.result()
    run["account"] = project_account(result["summary"])
    run["equity"] = [{"timestamp": p["time"], "equity": p["equity"],
                      "hold_baseline_equity": p["baseline_equity"], "incremental_pnl": p["net_increment"]}
                     for p in result["equity"]]
    run["signals"] = [{"market_ts": p["source_time"], "received_at": p["responded_at"],
                       "action": p["action"], "confidence": p["confidence"],
                       "requested_quantity": p.get("requested_quantity"),
                       "reason": p["execution_reason"], "execution_status": p["execution_reason"]}
                      for p in result["signals"]]
    run["trades"] = [{"timestamp": p["received_at"], "market_ts": p["source_time"],
                      "action": p["side"], "shares": p["quantity"], "price": p["price"],
                      "fee": p["total_fee"], "cash_after": p["cash_after"],
                      "sellable_after": p["sellable_after"], "frozen_after": p["frozen_after"],
                      "reason": "模拟盘口成交", "transfer_fee": p["transfer_fee"]}
                     for p in result["fills"]]
    run["quality"] = result["summary"]["quality_counts"]
    run["updated_at"] = now()


def run_live_paper(config: LiveTConfig, *, owner: str, store=None, source=None, client=None,
                   duration_seconds=300, max_model_requests=120, max_cost_usd=.1,
                   poll_seconds=1., decision_interval_seconds=1., lookback_events=20,
                   warmup_seconds=60., limits=None, audit=None, enriched_context=False, context_source=None,
                   continuous=False, stop_requested=None, resume_run=None, engine_checkpoint=None,
                   checkpoint_sink=None):
    if not (isinstance(continuous, bool)
            and (continuous or (30 <= duration_seconds <= 1800 and 1 <= max_model_requests <= 500))
            and 0 < max_cost_usd <= 10 and poll_seconds >= 1 and decision_interval_seconds >= 1
            and 2 <= lookback_events <= 60 and 30 <= warmup_seconds <= 300):
        raise ValueError("invalid bounded paper session configuration")
    if ((stop_requested is not None and not callable(stop_requested))
            or (checkpoint_sink is not None and not callable(checkpoint_sink))
            or (continuous and checkpoint_sink is None)):
        raise ValueError("continuous paper requires an atomic checkpoint sink")
    if (resume_run is None) != (engine_checkpoint is None) or (resume_run is not None and not continuous):
        raise ValueError("continuous resume requires both run and engine checkpoint")
    if owner != "admin" and not (owner.startswith("user:") and owner[5:].isdigit()):
        raise ValueError("explicit owner is required")
    if client is None:
        from src.services.jev_live_t_client import BoundedLiveDecisionClient
        client = BoundedLiveDecisionClient()
    if source is None:
        code, exchange = config.symbol.split(".")
        source = BoundedSinaLiveSnapshot(exchange.lower() + code,
                     market_symbols=("sh000300", "sh000852") if enriched_context else ("sh000001",))
    context_tracker = context_executor = None
    context_future, context_polled_at = None, float("-inf")
    if enriched_context:
        from src.services.jev_live_context_features import LiveContextTracker
        if context_source is None:
            from data_provider.jev_live_context import LiveExternalContext
            context_source = LiveExternalContext()
        context_tracker = LiveContextTracker()
        context_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-live-context")
    store = store or LiveTStore()
    engine = LiveTEngine.from_checkpoint(config, engine_checkpoint) if engine_checkpoint is not None else LiveTEngine(config)
    run = {"run_id": str(uuid4()), "status": "starting", "message": "正在核验当前行情并积累真实量价窗口",
           "created_at": now(), "started_at": now(), "updated_at": now(),
           "config": {**asdict(config), "name": "华懋科技" if config.symbol == "603306.SH" else config.symbol,
                      "currency": "CNY", "settlement": "t1", "quote_poll_interval_ms": poll_seconds * 1000,
                      "decision_interval_seconds": decision_interval_seconds,
                      "duration_seconds": None if continuous else duration_seconds,
                      "max_model_requests": None if continuous else max_model_requests, "max_cost_usd": max_cost_usd,
                      "limits": limits, "enriched_context": enriched_context, "continuous": continuous},
           "quote": None, "account": None, "usage": {"requests": 0, "cached": 0, "cost_usd": 0.,
                                                        "cost_is_estimate": False, "unconfirmed_requests": 0},
           "equity": [], "signals": [], "trades": [], "paid_request": None,
           "warnings": ["真实公开报价快照，1秒轮询不等于交易所每秒更新；不是逐笔或Level2行情。",
                        "盘口与区间成交量只用于保守模拟，不能还原排队、隐藏流动性或真实冲击。",
                        "未知真实历史买入成本；降本只表示已配对模拟交易净利润除以初始股数。",
                        "佣金及滑点为试跑假设，模型API费用单列、未计入交易账户盈亏。",
                        "行情或模型失败即停止试跑；结束时不虚构回补，库存偏离按当前市值计入增量收益。"],
           "methodology": [f"{config.initial_base_value:,.0f}元现值按首个有效价折为100股整手，余款归现金；原始总权益{config.initial_base_value + config.initial_cash:,.0f}元。",
                           "仅昨日底仓可卖；今日买入股份冻结。库存偏离最多初始股数的20%。",
                           "Jev只收到匿名归一化量价、盘口和当时已知指数因子，不收到持仓或资金金额。",
                           "模型返回后等待新的源时间报价，且整个累计量差额区间必须晚于可成交时间。",
                           "模型每秒检查是否有新信息；仅新行情、完整窗口、无待成交委托时发起一次判断。",
                           "只运行纸上账户，无券商连接或真实委托。"]}
    if context_tracker:
        run["context"] = context_tracker.view(now())
        run["context_diagnostics"] = context_tracker.diagnostics
        run["last_model_context"] = None
        run["methodology"][2] = "Jev接收归一化量价、盘口、沪深300、中证1000、CPO成分股等权参考及当时已取得的公开快讯；不接收持仓或资金金额。"
        run["methodology"].append("两指数、CPO和新闻源状态均有效后才发起判断；新闻是待核验事实，不是可执行指令。CPO为指定参照板块，不推断为华懋所属行业。")
        run["warnings"].append("CPO等权成分股参考不是官方板块指数；披露当前成员覆盖率，新闻按来源更新速度刷新，不伪称每秒新闻。")
    if config.confidence_sizing:
        run["methodology"].append(f"模型BUY/SELL/HOLD决定方向，不设置信度门槛；方向信号数量为上限{config.order_quantity}股乘置信度后向下取整到{config.lot_size}股，最低{config.lot_size}股。观望不下单。")
        run["warnings"].append("置信度仅用于模拟数量分配，不是经过校准的盈利概率；最低一手仍可能亏损。")
    if continuous:
        run["methodology"].append("持续运行沿用同一模拟账户与累计模型费用；休市暂停行情采集和新模型请求，开盘重新积累量价窗口。手动停止取消未成交委托，不平仓或重置账户。")
    if resume_run is not None:
        previous_config = resume_run.get("config", {})
        expected = {**asdict(config), "continuous": True, "enriched_context": enriched_context,
                    "max_cost_usd": max_cost_usd, "duration_seconds": None, "max_model_requests": None}
        usage = resume_run.get("usage", {})
        if (any(previous_config.get(key) != value for key, value in expected.items())
                or not isinstance(resume_run.get("run_id"), str)
                or not isinstance(usage.get("requests"), int) or isinstance(usage["requests"], bool)
                or usage["requests"] < 0 or not isinstance(usage.get("cost_usd"), (int, float))
                or isinstance(usage["cost_usd"], bool) or not math.isfinite(usage["cost_usd"])
                or usage["cost_usd"] < 0 or not isinstance(usage.get("cost_is_estimate"), bool)):
            raise ValueError("continuous resume configuration or accounting mismatch")
        run = deepcopy(resume_run)
        run.update(status="starting", message="恢复同一模拟账户，重新积累当前量价窗口", updated_at=now())
        for key in ("stopped_at", "stop_reason", "model_error"):
            run.pop(key, None)
        if run.get("paid_request"):
            # A crash between the durable reservation and its settlement has an
            # unknown paid outcome. Retain its cost; never replay that request.
            run["usage"]["cost_is_estimate"] = True
            run["usage"]["unconfirmed_requests"] = run["usage"].get("unconfirmed_requests", 0) + 1
        run["paid_request"] = None
        if context_tracker:
            run["context"] = context_tracker.view(now())
            run["context_diagnostics"] = context_tracker.diagnostics

    def persist():
        usage = run["usage"]
        usage["actual_cost_usd"] = None if usage["cost_is_estimate"] else usage["cost_usd"]
        if continuous:
            engine.compact_history(max_records=2000)
            update_projection(run, engine)
        if checkpoint_sink is not None:
            checkpoint_sink(deepcopy(run), engine.export_checkpoint())
        else:
            store.save(owner, run)

    persist()
    if audit:
        audit("started", {"run_id": run["run_id"], "config": run["config"]})
    events, market_context, intervals, latencies = [], [], [], []
    dispatch_times = []
    run["usage"]["observed_decision_interval_ms"] = None
    previous, market_previous, last_model_source = None, None, None
    market_history = []
    future, inflight = None, None
    failures = 0
    started = time.monotonic()
    warmup_started = started
    session_paused = False
    paused_saved_at = float("-inf")
    stop_reason = "duration_complete"
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-live-paper")

    def terminal_reason():
        if stop_requested is not None and stop_requested():
            return "manual_stop"
        if not continuous and time.monotonic() - started >= duration_seconds:
            return "duration_complete"
        if run["usage"]["cost_usd"] >= max_cost_usd:
            return "model_budget_reached"
        if not continuous and session_status(datetime.fromisoformat(now())) != "trading":
            return "continuous_session_ended"
        return None

    def market_open():
        return session_status(datetime.fromisoformat(now())) == "trading"

    def sleep_poll(seconds):
        # The real quote worker has a six-second hard deadline. Keeping sleep
        # to at most one second leaves time for bounded manual-stop settlement.
        time.sleep(min(max(0, seconds), 1.) if continuous else max(0, seconds))

    def settle_request(charged_usage=None):
        # Reservations remain visible after an unknown outcome; settle each
        # request once, including provider errors and responses arriving at stop.
        if not inflight or inflight.get("settled"):
            return
        valid = (isinstance(charged_usage, dict) and isinstance(charged_usage.get("cost"), (int, float))
                 and not isinstance(charged_usage["cost"], bool) and math.isfinite(charged_usage["cost"])
                 and charged_usage["cost"] >= 0 and isinstance(charged_usage.get("cost_is_estimate"), bool))
        if valid:
            run["usage"]["last_model_response_at"] = now()
            run["usage"]["cost_usd"] += charged_usage["cost"] - inflight["reserved_cost_usd"]
            run["usage"]["cost_is_estimate"] = inflight["prior_cost_is_estimate"] or charged_usage["cost_is_estimate"]
        else:
            run["usage"]["cost_is_estimate"] = True
            run["usage"]["unconfirmed_requests"] += 1
        inflight["settled"] = True
        run["paid_request"] = None

    try:
        while continuous or time.monotonic() - started < duration_seconds:
            tick_started = time.monotonic()
            if (ended := terminal_reason()):
                stop_reason = ended
                break
            if (continuous and future is not None and not future.done()
                    and time.monotonic() > inflight["response_deadline"]):
                raise JevClientError("timeout")
            if continuous and not market_open():
                if not session_paused:
                    engine.cancel_pending("market_closed")
                    if inflight is not None:
                        inflight["discard_response"] = True
                    events.clear()
                    market_context.clear()
                    market_history.clear()
                    previous = market_previous = last_model_source = None
                    dispatch_times.clear()
                    run["usage"]["observed_decision_interval_ms"] = None
                    if context_tracker:
                        context_tracker = LiveContextTracker()
                        context_polled_at = float("-inf")
                    session_paused = True
                    paused_saved_at = float("-inf")
                if future is not None and future.done():
                    completed_future, future = future, None
                    decision = completed_future.result()
                    settle_request(decision["usage"])
                    if audit:
                        audit("closed_session_decision_not_executed", {**inflight, "decision": decision})
                    inflight = None
                if future is not None and time.monotonic() > inflight["response_deadline"]:
                    raise JevClientError("timeout")
                if context_future is not None and context_future.done():
                    context_future = None
                run.update(status="paused", message="休市暂停行情采集与新模型请求，开盘后沿用当前账户继续")
                if run["quote"]:
                    run["quote"]["stale"] = True
                if context_tracker:
                    run["context"] = context_tracker.view(now())
                    run["context_diagnostics"] = context_tracker.diagnostics
                if time.monotonic() - paused_saved_at >= 5:
                    persist()
                    paused_saved_at = time.monotonic()
                sleep_poll(1)
                continue
            if continuous and session_paused:
                session_paused = False
                warmup_started = time.monotonic()
                run.update(status="starting", message="已开盘，沿用当前账户重新积累实时量价窗口")
            if context_tracker:
                if context_future is not None and context_future.done():
                    try:
                        external = context_future.result()
                        context_tracker.observe_external(external)
                        if audit:
                            audit("public_context", external)
                    except Exception:
                        context_tracker.external_failed()
                    context_future = None
                # The adapter owns CPO/news network cadence; cached views still age every second.
                if context_future is None and time.monotonic() - context_polled_at >= 1:
                    context_polled_at = time.monotonic()
                    context_future = context_executor.submit(lambda _: context_source.poll(), None)
                run["context"] = context_tracker.view(now())
                run["context_diagnostics"] = context_tracker.diagnostics
            if future is not None and future.done():
                completed_future, future = future, None
                decision = completed_future.result()
                responded = now()
                usage = decision["usage"]
                settle_request(usage)
                latencies.append(decision["latency_ms"])
                latencies = latencies[-2000:]
                run["usage"].update(last_latency_ms=latencies[-1], median_latency_ms=statistics.median(latencies))
                if (ended := terminal_reason()) or run["usage"]["cost_usd"] >= max_cost_usd:
                    stop_reason = ended or "model_budget_reached"
                    if audit:
                        audit("late_decision_not_executed", {**inflight, "decision": decision})
                    break
                if continuous and (not market_open() or inflight.get("discard_response")):
                    if audit:
                        audit("closed_session_decision_not_executed", {**inflight, "decision": decision})
                    inflight = None
                    continue
                submitted = engine.submit_decision(decision, signal_event_id=inflight["event_id"],
                                                   requested_at=inflight["requested_at"], responded_at=responded)
                if audit:
                    audit("decision", {**inflight, "responded_at": responded, "decision": decision, "submission": submitted})
                future = inflight = None
                if run["usage"]["cost_usd"] >= max_cost_usd:
                    stop_reason = "model_budget_reached"
                    break
            try:
                batch = source.poll()
                failures = 0
            except (requests_error_types()):
                failures += 1
                run["message"] = "行情请求失败，当前没有模拟成交"
                run["quote"] = ({**run["quote"], "stale": True} if run["quote"] else None)
                update_projection(run, engine)
                persist()
                if failures >= 3:
                    stop_reason = "quote_failures"
                    if continuous:
                        run["status"] = "failed"
                    break
                sleep_poll(poll_seconds - (time.monotonic() - tick_started))
                continue
            if (ended := terminal_reason()):
                stop_reason = ended
                break
            if continuous and not market_open():
                continue
            quote = batch["quotes"][source.symbol]
            if context_tracker:
                context_tracker.observe_indices(batch, now())
                run["context"] = context_tracker.view(now())
            if audit:
                audit("quote", batch)
            stamp = datetime.fromisoformat(quote["source_time"])
            received = datetime.fromisoformat(quote["received_at"])
            if limits and stamp.date().isoformat() == limits["date"]:
                quote.update(limit_up=limits["limit_up"], limit_down=limits["limit_down"])
            if (ended := terminal_reason()):
                stop_reason = ended
                break
            if continuous and not market_open():
                continue
            step = engine.step(quote)
            age_ms = (received - stamp).total_seconds() * 1000
            if step["accepted"] and previous is not None:
                intervals.append((stamp - datetime.fromisoformat(previous["source_time"])).total_seconds() * 1000)
                intervals = intervals[-2000:]
            run["quote"] = {"market_ts": quote["source_time"], "received_at": quote["received_at"],
                            "age_ms": age_ms, "source": quote["source"], "price": quote["last_price"],
                            "volume": quote["cumulative_volume"], "volume_unit": "股（当日累计）",
                            "observed_update_interval_ms": statistics.median(intervals) if intervals else None,
                            "stale": age_ms < 0 or age_ms > config.max_quote_age_ms,
                            "duplicate_count": engine.snapshot()["quality_counts"].get("duplicate_source_timestamp", 0)}
            market = batch["quotes"].get("sh000001") if not enriched_context else None
            if market and (market_previous is None or market["source_time"] > market_previous["source_time"]):
                market_history.append(market)
                market_history = market_history[-100:]
                earlier = next((m for m in reversed(market_history)
                                if (datetime.fromisoformat(market["source_time"]) - datetime.fromisoformat(m["source_time"])).total_seconds() >= 30), None)
                if earlier:
                    market_context.append({"asof": market["source_time"], "available_at": market["received_at"],
                                           "values": {"return_30s": market["last_price"] / earlier["last_price"] - 1}})
                    market_context = market_context[-100:]
                market_previous = market
            if step["accepted"]:
                volume = step["incremental_volume"]
                amount = (quote["cumulative_amount"] - previous["cumulative_amount"] if previous else None)
                if step["reason"] in ("gap_resync_no_fill", "new_day_volume_baseline_no_fill"):
                    events.clear()
                    if continuous:
                        warmup_started = time.monotonic()
                event = {"time": quote["source_time"], "sequence": step["event_id"],
                         "last_price": quote["last_price"], "bid_price": quote["bid_price"], "ask_price": quote["ask_price"],
                         "bid_volume": quote["bid_volume"], "ask_volume": quote["ask_volume"],
                         "trade_volume": volume or 0, "quantity_unit": "shares", "volume_kind": "incremental",
                         "event_type": "quote", "trading_status": quote["trading_status"],
                         "quote_age_ms": age_ms, "last_trade_age_ms": None,
                         "trade_notional": amount if volume and amount is not None and amount > 0 else 0 if not volume else None}
                # First/gap observations establish cumulative-volume baselines;
                # unknown interval volume must not be advertised as zero trades.
                if volume is not None:
                    events.append(event)
                events = events[-lookback_events:]
                previous = quote.copy()
                window_ready = len(events) >= lookback_events and time.monotonic() - warmup_started >= warmup_seconds
                new_signal = last_model_source is None or (stamp - last_model_source).total_seconds() >= decision_interval_seconds
                context_ready = not context_tracker or all(item["status"] == "fresh" for item in context_tracker.view(now()).values())
                if context_tracker and window_ready and not context_ready:
                    run["message"] = "等待沪深300、中证1000、CPO和新闻源的有效时点数据，暂不调用模型"
                if window_ready and context_ready and new_signal and future is None and engine.snapshot()["pending_order"] is None:
                    if (ended := terminal_reason()):
                        stop_reason = ended
                        break
                    if not continuous and run["usage"]["requests"] >= max_model_requests:
                        stop_reason = "model_request_limit"
                        break
                    features = build_live_state(events, market_context, lookback_events, include_legacy_market=not enriched_context)
                    if context_tracker:
                        features = context_tracker.model_state(features, now(), {"symbol": config.symbol, "name": run["config"]["name"]})
                    # Reserve input bytes using a local budget coefficient, not a verified provider price.
                    if hasattr(client, "build_budget_body"):
                        body = client.build_budget_body(features)
                    else:
                        if context_tracker:
                            from src.services.jev_live_context_client import JevLiveContextDecisionsClient
                            questions = JevLiveContextDecisionsClient(api_key="")._build_questions()
                        else:
                            questions = JevCBDecisionsClient(api_key="")._build_questions()
                        body = {"state": features, "questions": questions}
                    reserve = len(json.dumps(body, ensure_ascii=False).encode()) * client.input_price_per_million / 1e6
                    if run["usage"]["cost_usd"] + reserve > max_cost_usd:
                        stop_reason = "model_budget_reserved"
                        break
                    requested_at = now()
                    inflight = {"event_id": step["event_id"], "requested_at": requested_at,
                                "reserved_cost_usd": reserve,
                                "prior_cost_is_estimate": run["usage"].get("cost_is_estimate", False),
                                "response_deadline": time.monotonic() + min(120, max(1, getattr(client, "timeout", 40))) + 1,
                                "state_sha256": hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest(),
                                "state": features}
                    run["usage"]["requests"] += 1
                    run["usage"]["cost_usd"] += reserve
                    run["usage"]["cost_is_estimate"] = True
                    run["paid_request"] = {key: inflight[key] for key in
                                           ("requested_at", "reserved_cost_usd", "state_sha256")}
                    try:
                        persist()
                        ended = terminal_reason()
                        context_expired = context_tracker and not all(item["status"] == "fresh" for item in context_tracker.view(now()).values())
                        dispatch_quote_age = (datetime.fromisoformat(now()) - stamp).total_seconds() * 1000
                        quote_expired = not 0 <= dispatch_quote_age <= config.max_quote_age_ms
                        session_expired = continuous and not market_open()
                        if ended or context_expired or quote_expired or session_expired:
                            run["usage"]["requests"] -= 1
                            run["usage"]["cost_usd"] -= reserve
                            run["usage"]["cost_is_estimate"] = inflight["prior_cost_is_estimate"]
                            inflight = None
                            run["paid_request"] = None
                            if ended:
                                stop_reason = ended
                                break
                            if context_tracker:
                                run["context"] = context_tracker.view(now())
                            run["message"] = "提交前行情或上下文已过期，撤回费用预留并等待新数据"
                            update_projection(run, engine)
                            persist()
                            sleep_poll(poll_seconds-(time.monotonic()-tick_started))
                            continue
                        future = executor.submit(client.decide, features)
                        dispatch_times.append(time.monotonic())
                        dispatch_times = dispatch_times[-31:]
                        run["usage"]["last_model_request_at"] = now()
                        if len(dispatch_times) > 1:
                            run["usage"]["observed_decision_interval_ms"] = statistics.median(
                                (later - earlier) * 1000 for earlier, later in zip(dispatch_times, dispatch_times[1:]))
                    except Exception:
                        # Submission never occurred, so this is not an unknown
                        # paid outcome and must not leave a phantom reservation.
                        if inflight is not None:
                            run["usage"]["requests"] -= 1
                            run["usage"]["cost_usd"] -= reserve
                            run["usage"]["cost_is_estimate"] = inflight["prior_cost_is_estimate"]
                        inflight = None
                        run["paid_request"] = None
                        raise
                    last_model_source = stamp
                    if context_tracker:
                        run["last_model_context"] = {"decision_at": features["decision_at"], "context": features["context"]}
                    run["status"] = "running"
                    run["message"] = "接收新行情；Jev判断与模拟成交按真实到达时间推进"
            update_projection(run, engine)
            persist()
            sleep_poll(poll_seconds - (time.monotonic() - tick_started))
    except JevClientError as exc:
        stop_reason = "model_" + exc.code
        settle_request(getattr(exc, "usage", None))
        run["status"] = "failed"
    except Exception as exc:
        stop_reason = "runner_" + type(exc).__name__
        if future is None:
            settle_request()
        run["status"] = "failed"
    finally:
        if (context_tracker and stop_reason == "duration_complete" and run["usage"]["requests"] == 0
                and not all(item["status"] == "fresh" for item in context_tracker.view(now()).values())):
            stop_reason = "context_not_ready"
        stopped_at = now()
        engine.stop(stop_reason, stopped_at)
        # Await any already charged request for cost accounting, but never place
        # its order after the bounded session has ended.
        if future is not None:
            try:
                remaining = max(0, inflight["response_deadline"] - time.monotonic())
                pending = future.result(timeout=min(2., remaining) if continuous else remaining)
                settle_request(pending["usage"])
                if audit:
                    audit("late_decision_not_executed", {**inflight, "decision": pending})
            except JevClientError as exc:
                settle_request(getattr(exc, "usage", None))
                run["status"] = "failed"
                run["model_error"] = exc.code
            except Exception:
                settle_request()
                if not (continuous and stop_reason == "manual_stop"):
                    run["status"] = "failed"
                run["model_error"] = "outcome_unknown"
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        if context_future is not None:
            context_future.cancel()
        if context_executor is not None:
            context_executor.shutdown(wait=False, cancel_futures=True)
        source.close()
        close_client = getattr(client, "close", None)
        if callable(close_client):
            try:
                close_client()
            except Exception:
                # Account persistence and fee settlement must survive cleanup.
                run["warnings"].append("模型连接清理异常；账户和费用仍按已保存记录结算。")
        if context_source is not None and hasattr(context_source, "close"):
            context_source.close()
        update_projection(run, engine)
        run.update(status=run["status"] if run["status"] == "failed" else "completed" if stop_reason == "duration_complete" else "stopped",
                   stopped_at=stopped_at, stop_reason=stop_reason,
                   message="已手动停止；账户和累计费用已保存，再开启将继续同一账户" if continuous and stop_reason == "manual_stop"
                   else "模拟已停止；未成交委托已取消，账户和累计费用保留")
        persist()
        if audit:
            audit("finished", {"run": run, "ledger": engine.result()})
    return run


def requests_error_types():
    import requests
    return (requests.RequestException, ValueError, UnicodeError)
