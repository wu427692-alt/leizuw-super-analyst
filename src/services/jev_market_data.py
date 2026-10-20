"""Bounded, real Tushare minute bars for Jev research; no synthetic fallback.

``date`` is the original bar's close timestamp in Asia/Shanghai. Prices remain
unadjusted and volume remains shares. Warmup precedes the requested date range.
"""
from __future__ import annotations

import math
import re
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
MINUTE_FIELDS = "ts_code,trade_time,open,high,low,close,vol,amount"
MAX_EVALUATION_BARS = 2000
ERROR_MESSAGES = {
    "minute_invalid_config": "分钟回测配置无效。",
    "minute_invalid_date": "开始和结束日期须为 YYYY-MM-DD。",
    "minute_invalid_symbol": "请输入有效的沪深 A 股六位代码。",
    "minute_date_range_exceeds_31_days": "分钟回测日期跨度不能超过 31 天。",
    "minute_invalid_frequency": "仅支持 1、5、15 分钟原始行情。",
    "minute_invalid_lookback": "预热窗口须为 20 至 252 根。",
    "minute_permission_denied": "Tushare 当前账号没有所请求的分钟数据权限。",
    "minute_upstream_unavailable": "Tushare 分钟数据服务暂不可用，请稍后重试。",
    "minute_rate_limited": "Tushare 行情请求暂时限频，有限重试后仍未恢复，请稍后再试。",
    "minute_quota_exhausted": "Tushare 行情请求已达到较长周期配额，请等待配额恢复。",
    "minute_request_rejected": "Tushare 拒绝了行情请求参数，请核对股票、频率及日期。",
    "minute_request_timeout": "Tushare 行情请求超时，有限重试后仍未恢复。",
    "minute_invalid_response": "行情接口返回的数据结构不完整。",
    "minute_invalid_calendar": "交易日历数据无效，无法安全加载分钟行情。",
    "minute_symbol_mismatch": "分钟行情股票代码与请求不一致。",
    "minute_invalid_timestamp": "分钟行情时间无效或超出所请求日期。",
    "minute_invalid_numeric_value": "分钟行情包含无效价格或成交量。",
    "minute_invalid_ohlc": "分钟行情开高低收关系不合法。",
    "minute_conflicting_duplicate": "同一时刻存在冲突的分钟行情。",
    "minute_possible_response_truncation": "分钟行情可能达到接口行数上限，已停止加载。",
    "minute_insufficient_warmup_within_7_days": "开始日期前七天内没有足够的预热分钟数据。",
    "minute_evaluation_exceeds_2000_bars": "评估区间超过 2000 根分钟行情，请缩短日期范围。",
    "minute_no_closed_evaluation_bars": "评估区间内没有已完成的真实分钟行情。",
}


class MinuteDataError(ValueError):
    """Safe error code, never an upstream body, credential, or URL."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(ERROR_MESSAGES.get(code, "分钟行情加载失败。"))


def _create_gateway():
    # Reuse configured transport/auth/rate limits; keep pure validation importable.
    from src.services.financial_data_service import TushareGatewayService
    return TushareGatewayService()


def _now() -> datetime:
    return datetime.now(SHANGHAI)


def _sleep(seconds: float):
    time.sleep(seconds)


def _upstream_error_code(exc: Exception) -> str:
    # Classify in memory only. Never attach an upstream body/message to results.
    messages = []
    current = exc
    for _ in range(4):
        if current is None:
            break
        messages.append(str(current).lower())
        current = current.__cause__
    text = " ".join(messages)
    rate_limited = any(word in text for word in (
        "每秒", "每分钟", "频率", "频次", "too many", "rate limit", "http 429"))
    if rate_limited:
        if any(word in text for word in ("每天", "每日", "每小时", "daily quota", "hourly quota")) or re.search(
                r"\d+\s*次\s*/\s*(?:小时|天|日)", text):
            return "minute_quota_exhausted"
        return "minute_rate_limited"
    if any(word in text for word in ("权限", "permission", "unauthorized", "积分")):
        return "minute_permission_denied"
    if any(word in text for word in ("timeout", "timed out", "超时")):
        return "minute_request_timeout"
    if any(word in text for word in ("参数", "parameter", "日期格式", "时间格式")):
        return "minute_request_rejected"
    return "minute_upstream_unavailable"


def _iso_date(value: Any) -> date:
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != value:
        raise MinuteDataError("minute_invalid_date")
    return parsed


def _symbol(value: Any) -> str:
    match = re.fullmatch(r"([0-9]{6})(?:\.?(SH|SZ))?", str(value or "").strip().upper())
    if not match:
        raise MinuteDataError("minute_invalid_symbol")
    code, exchange = match.groups()
    inferred = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else None
    if inferred is None or (exchange and exchange != inferred):
        raise MinuteDataError("minute_invalid_symbol")
    return f"{code}.{inferred}"


def _number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MinuteDataError("minute_invalid_numeric_value")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise MinuteDataError("minute_invalid_numeric_value")
    return result


def _query(gateway, api: str, params: dict, fields: str) -> list[dict]:
    # The generic gateway's account-wide rate ceiling is not a guarantee of the
    # minute endpoint's burst capacity. Pace reads and retry only transient cases.
    delays = (2.0, 8.0, 30.0)
    for attempt in range(len(delays) + 1):
        _sleep(1.1 if attempt == 0 else delays[attempt - 1])
        try:
            response = gateway.query(api, params=params, fields=fields)
            break
        except Exception as exc:
            code = _upstream_error_code(exc)
            if code not in ("minute_rate_limited", "minute_request_timeout") or attempt == len(delays):
                raise MinuteDataError(code) from None
    if not isinstance(response, dict) or not isinstance(response.get("rows"), list):
        raise MinuteDataError("minute_invalid_response")
    rows = response["rows"]
    if any(not isinstance(row, dict) for row in rows):
        raise MinuteDataError("minute_invalid_response")
    if "count" in response and response["count"] != len(rows):
        raise MinuteDataError("minute_invalid_response")
    return rows


def _trading_days(gateway, symbol: str, start: date, end: date) -> list[date]:
    rows = _query(gateway, "trade_cal", {
        "exchange": "SSE" if symbol.endswith(".SH") else "SZSE",
        "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"),
    }, "cal_date,is_open")
    days = set()
    for row in rows:
        try:
            day = datetime.strptime(str(row["cal_date"]), "%Y%m%d").date()
        except (KeyError, TypeError, ValueError):
            raise MinuteDataError("minute_invalid_calendar") from None
        if row.get("is_open") not in (0, 1, "0", "1") or not start <= day <= end:
            raise MinuteDataError("minute_invalid_calendar")
        if str(row["is_open"]) == "1":
            days.add(day)
    return sorted(days)


def _parse_bar(row: dict, symbol: str) -> tuple[datetime, dict]:
    if row.get("ts_code") != symbol:
        raise MinuteDataError("minute_symbol_mismatch")
    raw_time = row.get("trade_time")
    if not isinstance(raw_time, str) or len(raw_time) < 16:
        raise MinuteDataError("minute_invalid_timestamp")
    try:
        stamp = datetime.fromisoformat(raw_time)
    except ValueError:
        raise MinuteDataError("minute_invalid_timestamp") from None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=SHANGHAI)
    stamp = stamp.astimezone(SHANGHAI)
    if stamp.second or stamp.microsecond:
        raise MinuteDataError("minute_invalid_timestamp")
    prices = {name: _number(row.get(name), positive=True)
              for name in ("open", "high", "low", "close")}
    if not prices["low"] <= min(prices["open"], prices["close"]) <= max(
            prices["open"], prices["close"]) <= prices["high"]:
        raise MinuteDataError("minute_invalid_ohlc")
    bar = {"date": stamp.isoformat(), **prices, "volume": _number(row.get("vol")),
           "adj_factor": None, "limit_up": None, "limit_down": None}
    if row.get("amount") is not None:
        bar["amount"] = _number(row["amount"])
    return stamp, bar


def _auxiliary(gateway, api: str, symbol: str, start: date, end: date,
               mapping: dict[str, str], warnings: list[str]) -> dict[str, dict]:
    try:
        rows = _query(gateway, api, {
            "ts_code": symbol, "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        }, "ts_code,trade_date," + ",".join(mapping))
        result = {}
        for row in rows:
            day = datetime.strptime(str(row.get("trade_date")), "%Y%m%d").date()
            if row.get("ts_code") != symbol or not start <= day <= end:
                raise ValueError
            values = {target: _number(row.get(source), positive=True)
                      for source, target in mapping.items()}
            if "limit_up" in values and values["limit_down"] > values["limit_up"]:
                raise ValueError
            key = day.isoformat()
            if key in result and result[key] != values:
                raise ValueError
            result[key] = values
        return result
    except (MinuteDataError, ValueError, TypeError, OverflowError):
        warnings.append(f"{api}_unavailable_or_invalid: auxiliary values remain missing")
        return {}


def load_minute_bars(config: dict) -> tuple[list[dict], dict]:
    """Return real warmup + evaluation bars and explicit provenance/coverage.

    At most a 31-day evaluation date span and 2000 evaluation bars. Only closed
    bars are retained. Require at least max(60, lookback_bars) earlier bars within
    seven calendar days; never interpolate, forward-fill, or substitute daily data.
    One minute request covers the entire range: at most 39 calendar days, safely
    below 8000 normal Shanghai/Shenzhen one-minute rows. Hitting 8000 still fails.
    """
    if not isinstance(config, dict):
        raise MinuteDataError("minute_invalid_config")
    symbol = _symbol(config.get("symbol"))
    start, end = _iso_date(config.get("start_date")), _iso_date(config.get("end_date"))
    if end < start or (end - start).days > 31:
        raise MinuteDataError("minute_date_range_exceeds_31_days")
    frequency = config.get("timeframe", "1min")
    if frequency not in ("1min", "5min", "15min"):
        raise MinuteDataError("minute_invalid_frequency")
    lookback = config.get("lookback_bars", 60)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or not 20 <= lookback <= 252:
        raise MinuteDataError("minute_invalid_lookback")
    warmup_required = max(60, lookback)
    now = _now().astimezone(SHANGHAI)
    if start > now.date():
        raise MinuteDataError("minute_no_closed_evaluation_bars")
    warmup_start = start - timedelta(days=7)
    gateway = _create_gateway()
    days = _trading_days(gateway, symbol, warmup_start, min(end, now.date()))
    warnings: list[str] = []
    counters = {"duplicate_rows": 0, "unclosed_rows_excluded": 0}
    rows = _query(gateway, "stk_mins", {
        "ts_code": symbol, "freq": frequency,
        "start_date": f"{warmup_start.isoformat()} 00:00:00",
        "end_date": f"{min(end, now.date()).isoformat()} 23:59:59",
    }, MINUTE_FIELDS)
    if len(rows) >= 8000:
        raise MinuteDataError("minute_possible_response_truncation")
    trading_days = set(days)
    unique = {}
    for row in rows:
        stamp, bar = _parse_bar(row, symbol)
        if stamp.date() not in trading_days:
            raise MinuteDataError("minute_invalid_timestamp")
        # A close timestamp at the current minute has no completed-bar guarantee.
        if stamp >= now.replace(second=0, microsecond=0):
            counters["unclosed_rows_excluded"] += 1
            continue
        key = bar["date"]
        if key in unique:
            if unique[key] != bar:
                raise MinuteDataError("minute_conflicting_duplicate")
            counters["duplicate_rows"] += 1
        unique[key] = bar
    ordered = [unique[key] for key in sorted(unique)]
    warmup = [bar for bar in ordered if bar["date"][:10] < start.isoformat()]
    if len(warmup) < warmup_required:
        raise MinuteDataError("minute_insufficient_warmup_within_7_days")
    warmup = warmup[-warmup_required:]
    evaluation = [bar for bar in ordered if start.isoformat() <= bar["date"][:10] <= end.isoformat()]
    if len(evaluation) > MAX_EVALUATION_BARS:
        raise MinuteDataError("minute_evaluation_exceeds_2000_bars")
    if not evaluation:
        raise MinuteDataError("minute_no_closed_evaluation_bars")
    covered_days = {bar["date"][:10] for bar in evaluation}
    missing_days = [day.isoformat() for day in days
                    if start <= day <= end and day.isoformat() not in covered_days]
    bars = warmup + evaluation
    first, last = _iso_date(bars[0]["date"][:10]), _iso_date(bars[-1]["date"][:10])
    factors = _auxiliary(gateway, "adj_factor", symbol, first, last,
                         {"adj_factor": "adj_factor"}, warnings)
    limits = _auxiliary(gateway, "stk_limit", symbol, first, last,
                        {"up_limit": "limit_up", "down_limit": "limit_down"}, warnings)
    for bar in bars:
        day = bar["date"][:10]
        bar.update(factors.get(day, {}))
        bar.update(limits.get(day, {}))
    for key in ("adj_factor", "limit_up", "limit_down"):
        missing = sum(bar[key] is None for bar in bars)
        if missing:
            warnings.append(f"{key}_missing_for_{missing}_bars: not inferred or forward-filled")
    if missing_days:
        warnings.append("no_minute_rows_for_trading_dates: " + ",".join(sorted(missing_days)))
    if counters["unclosed_rows_excluded"]:
        warnings.append("future_or_unclosed_minute_bars_excluded")
    metadata = {
        "source": "tushare", "resource": "stk_mins", "symbol": symbol,
        "frequency": frequency, "volume_unit": "shares", "amount_unit": "CNY",
        "price_adjustment": "raw", "time_zone": "Asia/Shanghai",
        "coverage": {
            "requested_start": start.isoformat(), "requested_end": end.isoformat(),
            "loaded_start": bars[0]["date"], "loaded_end": bars[-1]["date"],
            "evaluation_start": evaluation[0]["date"], "evaluation_end": evaluation[-1]["date"],
            "warmup_bars": len(warmup), "evaluation_bars": len(evaluation),
            "lookback_bars": lookback, "missing_trading_dates": sorted(missing_days), **counters,
        },
        "warnings": warnings,
    }
    return bars, metadata
