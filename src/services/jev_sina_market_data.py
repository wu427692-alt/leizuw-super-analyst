"""Bounded Sina public minute snapshot, with explicit recent-window limitations.

Request shape follows AKShare stock_zh_a_minute; parsing never evaluates scripts.
No Tushare calls, unit conversion, adjustment, interpolation, or silent fallback.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

import requests

from src.services.jev_market_data import (
    MAX_EVALUATION_BARS, SHANGHAI, MinuteDataError, _iso_date, _number, _parse_bar, _symbol,
)

SINA_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData"
SOURCE_REFERENCE = "https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_zh_a_sina.py"
MAX_SOURCE_BARS = 1970
MAX_RESPONSE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 20
_JSONP = re.compile(
    r"\A\s*(?:/\*<script>location\.href='//sina\.com';</script>\*/\s*)?"
    r"=\s*\(\s*(\[.*\])\s*\)\s*;\s*\Z", re.DOTALL,
)
_MESSAGES = {
    "sina_unavailable": "新浪分钟行情暂不可用，请稍后重试。",
    "sina_timeout": "新浪分钟行情请求超时，未自动重试。",
    "sina_invalid_response": "新浪返回的分钟行情格式无效，已停止加载。",
    "sina_response_too_large": "新浪分钟行情响应超过大小限制，已停止加载。",
    "sina_date_range_not_covered": "新浪仅提供近期分钟快照，未完整覆盖所选起止日期，请缩短或调整日期。",
    "sina_insufficient_warmup": "新浪近期分钟快照中没有足够的前期预热数据，请调整开始日期。",
    "sina_no_closed_bars": "新浪快照中没有可用的已完成分钟行情。",
}


class SinaMinuteDataError(MinuteDataError):
    """Fixed, safe provider-specific messages, compatible with the shared error type."""

    def __init__(self, code: str):
        super().__init__(code)
        if code in _MESSAGES:
            self.args = (_MESSAGES[code],)


def _now():
    return datetime.now(SHANGHAI)


def _reject_constant(_):
    raise ValueError


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _parse_jsonp(text: str) -> list[dict]:
    match = _JSONP.fullmatch(text)
    if not match:
        raise SinaMinuteDataError("sina_invalid_response")
    try:
        rows = json.loads(match.group(1), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except (ValueError, TypeError, RecursionError):
        raise SinaMinuteDataError("sina_invalid_response") from None
    if not rows or len(rows) > MAX_SOURCE_BARS or any(not isinstance(row, dict) for row in rows):
        raise SinaMinuteDataError("sina_invalid_response")
    return rows


def _request_rows(symbol: str, scale: int) -> list[dict]:
    # Do not allow arbitrary symbols, endpoints, callbacks, or redirect destinations.
    if not re.fullmatch(r"(?:sh6[0-9]{5}|sz[03][0-9]{5})", symbol) or scale not in (1, 5, 15):
        raise MinuteDataError("minute_invalid_symbol")
    started = time.monotonic()
    try:
        with requests.get(SINA_URL, params={"symbol": symbol, "scale": str(scale),
                                           "ma": "no", "datalen": str(MAX_SOURCE_BARS)},
                          timeout=TIMEOUT_SECONDS, stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise SinaMinuteDataError("sina_unavailable")
            length = response.headers.get("Content-Length", "")
            if length and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                raise SinaMinuteDataError("sina_response_too_large")
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise SinaMinuteDataError("sina_response_too_large")
                if time.monotonic() - started > TIMEOUT_SECONDS:
                    raise SinaMinuteDataError("sina_timeout")
                chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8")
    except requests.Timeout:
        raise SinaMinuteDataError("sina_timeout") from None
    except requests.RequestException:
        raise SinaMinuteDataError("sina_unavailable") from None
    except UnicodeDecodeError:
        raise SinaMinuteDataError("sina_invalid_response") from None
    return _parse_jsonp(text)


def _numeric(value):
    if isinstance(value, str):
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
            raise MinuteDataError("minute_invalid_numeric_value")
        try:
            value = float(value)
        except (ValueError, OverflowError):
            raise MinuteDataError("minute_invalid_numeric_value") from None
    try:
        return _number(value)
    except OverflowError:
        raise MinuteDataError("minute_invalid_numeric_value") from None


def _reference_gaps(evaluation: list[dict], scale: int) -> tuple[list[str], list[dict]]:
    """Compare observed days to a reference session grid, not an exchange calendar."""
    grouped = {}
    for bar in evaluation:
        grouped.setdefault(bar["date"][:10], set()).add(bar["date"][11:16])
    expected = {f"{minute // 60:02d}:{minute % 60:02d}"
                for start in (9 * 60 + 30, 13 * 60)
                for minute in range(start + scale, start + 121, scale)}
    gaps = []
    for day, observed in grouped.items():
        missing = sorted(expected - observed)
        if missing:
            gaps.append({"date": day, "missing_labels": missing})
    first, last = _iso_date(evaluation[0]["date"][:10]), _iso_date(evaluation[-1]["date"][:10])
    unobserved = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        if day.weekday() < 5 and day.isoformat() not in grouped:
            unobserved.append(day.isoformat())
    return unobserved, gaps


def load_sina_minute_bars(config: dict) -> tuple[list[dict], dict]:
    """Load one real recent-minute snapshot and preserve its gaps and raw units."""
    if not isinstance(config, dict):
        raise MinuteDataError("minute_invalid_config")
    raw_symbol = str(config.get("symbol") or "").strip()
    match = re.fullmatch(r"(sh|sz)([0-9]{6})", raw_symbol, re.IGNORECASE)
    symbol = _symbol(f"{match.group(2)}.{match.group(1)}" if match else raw_symbol)
    sina_symbol = symbol[-2:].lower() + symbol[:6]
    start, end = _iso_date(config.get("start_date")), _iso_date(config.get("end_date"))
    if end < start or (end - start).days > 31:
        raise MinuteDataError("minute_date_range_exceeds_31_days")
    frequency = config.get("timeframe", "1min")
    if frequency not in ("1min", "5min", "15min"):
        raise MinuteDataError("minute_invalid_frequency")
    scale = int(frequency[:-3])
    lookback = config.get("lookback_bars", 60)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or not 20 <= lookback <= 252:
        raise MinuteDataError("minute_invalid_lookback")
    needed = max(60, lookback)
    cutoff = _now().astimezone(SHANGHAI).replace(second=0, microsecond=0)
    if end > cutoff.date():
        raise SinaMinuteDataError("sina_date_range_not_covered")
    rows = _request_rows(sina_symbol, scale)
    unique, duplicates, excluded = {}, 0, 0
    for row in rows:
        adapted = {"ts_code": symbol, "trade_time": row.get("day"),
                   **{field: _numeric(row.get(field)) for field in ("open", "high", "low", "close")},
                   "vol": _numeric(row.get("volume"))}
        if "amount" in row:
            adapted["amount"] = _numeric(row["amount"])
        stamp, bar = _parse_bar(adapted, symbol)
        label = stamp.strftime("%H:%M")
        if stamp.weekday() >= 5 or stamp.minute % scale or not (
                "09:30" <= label <= "11:30" or "13:00" <= label <= "15:00"):
            raise MinuteDataError("minute_invalid_timestamp")
        if stamp >= cutoff:
            excluded += 1
            continue
        key = bar["date"]
        if key in unique:
            if unique[key] != bar:
                raise MinuteDataError("minute_conflicting_duplicate")
            duplicates += 1
        unique[key] = bar
    ordered = [unique[key] for key in sorted(unique)]
    if not ordered:
        raise SinaMinuteDataError("sina_no_closed_bars")
    available_start, available_end = ordered[0]["date"], ordered[-1]["date"]
    if available_start[:10] > start.isoformat() or available_end[:10] < end.isoformat():
        raise SinaMinuteDataError("sina_date_range_not_covered")
    # Date-only requests describe whole days; don't present an unfinished last day as covered.
    if available_end[:10] == end.isoformat() and available_end[11:19] < "15:00:00":
        raise SinaMinuteDataError("sina_date_range_not_covered")
    evaluation = [bar for bar in ordered if start.isoformat() <= bar["date"][:10] <= end.isoformat()]
    if not evaluation or evaluation[0]["date"][:10] != start.isoformat() or evaluation[-1]["date"][:10] != end.isoformat():
        raise SinaMinuteDataError("sina_date_range_not_covered")
    if len(evaluation) > MAX_EVALUATION_BARS:
        raise MinuteDataError("minute_evaluation_exceeds_2000_bars")
    warmup = [bar for bar in ordered if bar["date"][:10] < start.isoformat()]
    if len(warmup) < needed:
        raise SinaMinuteDataError("sina_insufficient_warmup")
    warmup = warmup[-needed:]
    bars = warmup + evaluation
    unobserved, gaps = _reference_gaps(evaluation, scale)
    warnings = [
        "新浪仅提供近期分钟快照，本次最多读取 1970 根，无法覆盖任意历史区间。",
        "成交量和成交额保留数据源原值，单位尚未核验，未作股、手等单位换算。",
        "时间按上海时区解读为分钟结束标签；该含义据交易时段推断，尚未经数据源确认。",
        "缺少复权因子、涨跌停与停牌信息，除权除息等公司行为和实际可成交性尚未核验。",
        "未查询权威交易日历；工作日和分钟时段网格仅用于参考检查。",
    ]
    if gaps:
        warnings.append("部分分钟时间标签缺失，已保留原始缺口，未插值补齐。")
    if unobserved:
        warnings.append("部分工作日没有行情，尚无法区分休市、停牌或数据缺失。")
    if excluded:
        warnings.append("已排除未来或无法确认已经结束的分钟数据。")
    metadata = {
        "source": "sina", "resource": "CN_MarketDataService.getKLineData", "symbol": symbol,
        "frequency": frequency, "volume_unit": "provider_raw", "amount_unit": "provider_raw",
        "price_adjustment": "raw", "time_zone": "Asia/Shanghai",
        "timestamp_semantics": "inferred_bar_close_label", "reference": SOURCE_REFERENCE,
        "coverage": {
            "requested_start": start.isoformat(), "requested_end": end.isoformat(),
            "source_start": available_start, "source_end": available_end, "source_rows": len(ordered),
            "loaded_start": bars[0]["date"], "loaded_end": bars[-1]["date"],
            "evaluation_start": evaluation[0]["date"], "evaluation_end": evaluation[-1]["date"],
            "warmup_bars": len(warmup), "evaluation_bars": len(evaluation), "lookback_bars": lookback,
            "duplicate_rows": duplicates, "unclosed_rows_excluded": excluded,
            "unobserved_weekdays": unobserved, "reference_session_gaps": gaps,
            "calendar_verified": False, "source_limit": MAX_SOURCE_BARS,
        },
        "warnings": warnings,
    }
    return bars, metadata
