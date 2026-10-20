"""Bounded paid minute replay jobs. Read endpoints never call the model."""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.repositories.jev_backtest_repo import JevReplayRepository, encode, timestamp
from src.services.jev_decisions_client import JevDecisionsClient, JevClientError
from src.services.jev_signal_engine import BacktestConfig, run_backtest


def credential_status():
    if os.getenv("JEV_RELAY_URL"):
        from src.services.jev_relay_client import JevRelayClient
        return JevRelayClient().configured
    if os.getenv("JEV_API_KEY"):
        return True
    if platform.system() != "Darwin":
        return False
    result = subprocess.run(
        ["security", "find-generic-password", "-s", os.getenv("JEV_KEYCHAIN_SERVICE", "codex.jev-use.worldseek")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    return result.returncode == 0


def load_credential():
    key = os.getenv("JEV_API_KEY")
    if key:
        return key
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["security", "find-generic-password", "-s", os.getenv("JEV_KEYCHAIN_SERVICE", "codex.jev-use.worldseek"), "-w"],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
    raise ValueError("Jev 专用凭据尚未配置")


def create_client():
    if os.getenv("JEV_RELAY_URL"):
        from src.services.jev_relay_client import JevRelayClient
        return JevRelayClient()
    return JevDecisionsClient(api_key=load_credential())


def load_market_bars(config):
    if config.get("data_source", "tushare") == "sina":
        from src.services.jev_sina_market_data import load_sina_minute_bars
        return load_sina_minute_bars(config)
    from src.services.jev_market_data import load_minute_bars
    return load_minute_bars(config)


def capabilities():
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    while day.weekday() > 4:
        day -= timedelta(days=1)
    start = day - timedelta(days=1)
    while start.weekday() > 4:
        start -= timedelta(days=1)
    client = JevDecisionsClient(api_key="")
    ready = credential_status()
    return {"configured": ready, "model": client.model, "provider": "WorldSeek",
            "timeframe": "1min", "timeframes": ["1min", "5min", "15min"],
            "limits": {"max_evaluation_bars": 2000, "max_cost_usd": .5},
            "defaults": {"symbol": "600000", "start_date": start.isoformat(), "end_date": day.isoformat(),
                         "data_source": "sina",
                         "timeframe": "1min", "lookback_bars": 60, "initial_cash": 100000,
                         "confidence_threshold": .65, "commission_bps": 3, "min_commission": 5,
                         "sell_tax_bps": 5, "slippage_bps": 5, "position_pct": 1,
                         "max_holding_bars": 60, "decision_interval": 1, "settlement_mode": "t1",
                         "stop_loss_pct": 0, "take_profit_pct": 0,
                         "refresh_prices": True, "max_cost_usd": .2},
            "data_sources": ["新浪公开分钟（近期历史，原始量用于相对比较）", "Tushare 历史分钟（依账户权限）"],
            "message": "仅按顺序输入历史量价；账户参数不上传模型。" if ready else "需在服务器配置 Jev 专用凭据"}


class ReplayStopped(Exception):
    pass


class JevBacktestService:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self, repo=None, loader=None, client_factory=None):
        self.repo = repo or JevReplayRepository()
        if loader is None:
            loader = load_market_bars
        self.loader = loader
        self.client_factory = client_factory or create_client
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jev-replay")

    def submit(self, owner, config):
        task, created = self.repo.create(owner, config)
        if created:
            self.pool.submit(self._run, owner, task["task_id"])
        return task

    def cancel(self, owner, task_id):
        task = self.repo.get(owner, task_id)
        if not task:
            return None
        if task["status"] == "queued":
            cancelled = self.repo.update(owner, task_id,
                {"status": "cancelled", "completed_at": timestamp(), "message": "已取消"}, expected_status="queued")
            if cancelled:
                return cancelled
        if task["status"] in ("queued", "running"):
            return self.repo.update(owner, task_id,
                {"cancel_requested": True, "message": "正在停止，已发出请求的费用保留"}, expected_status="running") or self.repo.get(owner, task_id)
        return task

    def _run(self, owner, task_id):
        task = self.repo.update(owner, task_id,
            {"status": "running", "started_at": timestamp(), "message": "读取真实分钟行情"}, expected_status="queued")
        if not task:
            return
        config = task["config"]
        usage = {"requests": 0, "cached": 0, "cost_usd": 0., "cost_is_estimate": False}
        preview = {"equity": [], "signals": [], "trades": [], "summary": {"initial_cash": config["initial_cash"]}}
        abort = None
        try:
            price_digest = hashlib.sha256(encode({"kind": "minute-snapshot-v2", "source": config.get("data_source", "tushare"),
                **{k: config[k] for k in ("symbol", "start_date", "end_date", "timeframe", "lookback_bars")}}).encode()).hexdigest()
            snapshot = self.repo.cached(owner, price_digest) if not config.get("refresh_prices", True) else None
            if snapshot:
                bars, metadata = snapshot["bars"], snapshot["metadata"]
            else:
                bars, metadata = self.loader(config)
                self.repo.cache(owner, price_digest, {"bars": bars, "metadata": metadata}, replace=True)
            client = self.client_factory()
            bar_by_date = {bar["date"]: bar for bar in bars}
            preview["market_data"] = {**metadata, "bars": []}
            model_config = BacktestConfig(**{k: v for k, v in config.items()
                                            if k in {f.name for f in fields(BacktestConfig)}})

            def check_cancel():
                current = self.repo.get(owner, task_id)
                if not current or current["status"] != "running":
                    raise ReplayStopped("任务已停止，禁止继续调用模型")
                if current.get("cancel_requested"):
                    raise ReplayStopped("已取消；已经生成的信号仍可复用")

            def decide(state):
                nonlocal abort
                try:
                    check_cancel()
                    # Version includes the fixed question and feature semantics.
                    digest = hashlib.sha256(encode({"version": "ohlcv-direction-v1", "model": client.model,
                                                   "endpoint": client.endpoint, "state": state}).encode()).hexdigest()
                    cached = self.repo.cached(owner, digest)
                    if cached:
                        usage["cached"] += 1
                        return {**cached, "usage": {**cached.get("usage", {}), "cached": True, "cost": 0}}
                    # Reserve 32K input units at the unverified local budget coefficient.
                    reserve = max(.000001, 32768 * client.input_price_per_million / 1e6)
                    if usage["cost_usd"] + reserve > config["max_cost_usd"]:
                        raise ReplayStopped("已到本次本地预算上限；提高预算后重跑可复用已缓存信号")
                    old_estimate = usage["cost_is_estimate"]
                    next_usage = {**usage, "requests": usage["requests"] + 1,
                                  "cost_usd": usage["cost_usd"] + reserve, "cost_is_estimate": True, "actual_cost_usd": None}
                    if not self.repo.update(owner, task_id,
                        {"usage": next_usage, "message": "Jev 正在判断当前量价窗口"},
                        expected_status="running", require_not_cancelled=True):
                        raise ReplayStopped("任务已停止，禁止继续调用模型")
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
                        raise ReplayStopped(("未核验预算估值" if usage["cost_is_estimate"] else "服务端实际费用") + "超过本次预算上限，后续调用已停止")
                    return answer
                except ReplayStopped as exc:
                    abort = exc
                    raise
                except Exception:
                    abort = ReplayStopped("Jev 请求处理异常，后续调用已停止")
                    raise abort from None

            def progress(point):
                # Engine turns ordinary provider failures into unavailable signals;
                # cancellation/budget failures must instead terminate the job.
                if abort:
                    raise abort
                check_cancel()
                preview["equity"].append(point["equity_point"])
                if point["date"] in bar_by_date:
                    preview["market_data"]["bars"].append(bar_by_date[point["date"]])
                for source, dest in (("latest_signal", "signals"), ("latest_trade", "trades")):
                    item = point.get(source)
                    if item and (not preview[dest] or preview[dest][-1]["date"] != item["date"]):
                        preview[dest].append(item)
                updates = {row["date"]: row for row in point.get("signal_updates", [])}
                for index in range(max(0, len(preview["signals"]) - 2), len(preview["signals"])):
                    when = preview["signals"][index]["date"]
                    if when in updates:
                        preview["signals"][index] = updates[when]
                equity = point["equity_point"]
                preview["summary"].update(final_equity=equity["equity"], cash=equity["cash"],
                                           position_shares=equity["position_shares"])
                self.repo.update(owner, task_id, {"progress": round(point["processed_bars"] / point["total_bars"] * 99, 2),
                    "message": f"已推进 {point['processed_bars']} / {point['total_bars']} 根分钟行情",
                    "preview": preview, "usage": dict(usage)}, expected_status="running")

            result = run_backtest(bars, model_config, decide, progress)
            check_cancel()
            result["market_data"] = {**metadata, "bars": [b for b in bars
                if config["start_date"] <= b["date"][:10] <= config["end_date"]]}
            result["data_quality"]["warnings"].extend(metadata.get("warnings", []))
            if not self.repo.update(owner, task_id, {"status": "completed", "progress": 100,
                "message": "回测完成，可加速回放或修改条件重跑", "result": result, "preview": None,
                "usage": usage, "completed_at": timestamp()}, expected_status="running", require_not_cancelled=True):
                raise ReplayStopped("任务已停止，已生成信号仍可复用")
        except Exception as exc:
            current = self.repo.get(owner, task_id) or {}
            cancelled = current.get("cancel_requested", False)
            # Only our bounded validation/control errors may reach the UI.
            from src.services.jev_market_data import MinuteDataError
            message = str(exc) if isinstance(exc, (ReplayStopped, MinuteDataError)) else "分钟回测失败，请检查行情或服务配置"
            self.repo.update(owner, task_id, {"status": "cancelled" if cancelled else "failed",
                "error": message, "message": message, "usage": usage,
                "preview": preview, "completed_at": timestamp()}, expected_status="running")
