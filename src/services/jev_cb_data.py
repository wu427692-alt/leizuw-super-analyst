"""Read-only, fail-closed inputs for recorded intraday T+0 research.

A server-controlled dataset.json may supply verified recorded events. Existing
stock_ticks is inspected for coverage only: its mixed-source cumulative units
and local polling timestamps are not silently promoted to executable ticks.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

MAX_BYTES = 40 * 1024 * 1024
MAX_EVENTS = 200000
FEATURES = frozenset(("return_1s", "return_5s", "return_30s", "return_60s", "return_300s",
                      "vwap_gap", "relative_volume", "realized_volatility", "order_imbalance",
                      "breadth", "limit_up_ratio", "conversion_premium"))
_MESSAGES = {
    "config": "标的、日期或秒级数据配置无效。",
    "missing": "尚未配置经过核验的完整逐笔或秒级行情，现有碎片快照不能用于本次回测。",
    "invalid": "行情文件格式或数值无效，未启动回测。",
    "units": "行情单位或增量成交量语义尚未核验，不能进行撮合。",
    "coverage": "行情未覆盖所选日期的完整交易时段，不能作为整日秒级回测。",
    "granularity": "行情的真实采样频度与所选逐笔或一秒频度不一致。",
    "context": "所选关联因子缺少同期可用数据，请补充数据或取消该因子。",
    "path": "服务器行情目录或文件不可用。",
}


class CBDataError(ValueError):
    def __init__(self, code="invalid"):
        self.code = code if code in _MESSAGES else "invalid"
        super().__init__(_MESSAGES[self.code])


def _now():
    return datetime.now(timezone.utc)


def _finite(value, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CBDataError()
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise CBDataError()
    return value


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > _now():
            raise ValueError()
        return stamp
    except (ValueError, TypeError, OverflowError):
        raise CBDataError() from None


def _symbol(value):
    value = str(value or "113677").upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,23}", value):
        raise CBDataError("config")
    return value


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CBDataError()
        result[key] = value
    return result


def _read_json(path, max_bytes=MAX_BYTES):
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise CBDataError("path")
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise CBDataError("path")
    return json.loads(raw, object_pairs_hook=_unique_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(CBDataError()))


def _catalog():
    configured = os.environ.get("JEV_CB_DATA_DIR")
    if not configured:
        raise CBDataError("missing")
    root = Path(configured).resolve(strict=True)
    catalog_path = (root / "catalog.json").resolve(strict=True)
    if catalog_path.parent != root:
        raise CBDataError("path")
    catalog = _read_json(catalog_path, 256 * 1024)
    entries = catalog.get("datasets") if isinstance(catalog, dict) else None
    if not isinstance(entries, list) or len(entries) > 30:
        raise CBDataError()
    seen = set()
    for entry in entries:
        key = entry.get("dataset_id") if isinstance(entry, dict) else None
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", key) or key in seen:
            raise CBDataError()
        seen.add(key)
    return root, catalog, entries


def _dataset(config=None):
    configured = os.environ.get("JEV_CB_DATA_DIR")
    if not configured:
        raise CBDataError("missing")
    try:
        root = Path(configured).resolve(strict=True)
        key = (config or {}).get("dataset_id")
        if (root / "catalog.json").exists():
            root, catalog, entries = _catalog()
            key = key or catalog.get("default_dataset_id")
            entry = next((e for e in entries if e["dataset_id"] == key), None)
            if entry is None:
                raise CBDataError("config")
            path = (root / entry["file"]).resolve(strict=True)
        else:
            if key is not None:
                raise CBDataError("config")
            path = (root / "dataset.json").resolve(strict=True)
        if not path.is_relative_to(root):
            raise CBDataError("path")
        document = _read_json(path)
        if not isinstance(document, dict):
            raise CBDataError()
        if key is not None and document.get("dataset_id") != key:
            raise CBDataError()
        return document
    except CBDataError:
        raise
    except (OSError, ValueError, UnicodeError, KeyError, TypeError):
        raise CBDataError("path") from None


def _instrument(document):
    instrument = document.get("instrument")
    if not isinstance(instrument, dict):
        raise CBDataError("units")
    symbol = _symbol(instrument.get("symbol"))
    unit = instrument.get("quantity_unit")
    if unit not in ("bonds", "shares") or instrument.get("unit_verified") is not True:
        raise CBDataError("units")
    if instrument.get("same_day_sell") is not True:
        raise CBDataError("config")
    kind = instrument.get("instrument_type")
    if kind == "us_equity":
        if (unit, instrument.get("currency"), instrument.get("simulation_account"), instrument.get("lot_size")) != (
                "shares", "USD", "unlevered_margin", 1):
            raise CBDataError("units")
    elif kind == "convertible_bond":
        if (unit, instrument.get("currency"), instrument.get("simulation_account"), instrument.get("lot_size")) != (
                "bonds", "CNY", "cash_t0", 10):
            raise CBDataError("units")
    else:
        raise CBDataError("config")
    try:
        zone = ZoneInfo(instrument.get("exchange_timezone", instrument.get("time_zone")))
        sessions = [(time.fromisoformat(a), time.fromisoformat(b)) for a, b in instrument["sessions"]]
        if not 1 <= len(sessions) <= 3 or any(a >= b for a, b in sessions):
            raise ValueError()
        if sessions != sorted(sessions) or any(a[1] >= b[0] for a, b in zip(sessions, sessions[1:])):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise CBDataError("config") from None
    return instrument, symbol, unit, zone, sessions


def _contexts(document):
    raw = document.get("contexts", {})
    if not isinstance(raw, dict) or set(raw) - {"underlying", "sector", "market"}:
        raise CBDataError()
    result = {}
    for key in ("underlying", "sector", "market"):
        rows = raw.get(key, [])
        if not isinstance(rows, list) or len(rows) > MAX_EVENTS:
            raise CBDataError()
        cleaned = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("values"), dict):
                raise CBDataError()
            asof, available = _stamp(row.get("asof")), _stamp(row.get("available_at"))
            if available < asof or not row["values"] or set(row["values"]) - FEATURES:
                raise CBDataError()
            values = {name: _finite(value) for name, value in row["values"].items()}
            cleaned.append({"asof": asof.isoformat(), "available_at": available.isoformat(), "values": values})
        result[key] = sorted(cleaned, key=lambda r: (r["asof"], r["available_at"]))
    return result


def _prepare(document, config=None):
    config = config or {}
    instrument, symbol, unit, zone, sessions = _instrument(document)
    if _symbol(config.get("symbol", symbol)).split(".")[0] != symbol.split(".")[0]:
        raise CBDataError("config")
    granularity = document.get("granularity")
    if granularity not in ("tick", "1s") or config.get("event_granularity", granularity) != granularity:
        raise CBDataError("granularity")
    source = document.get("source")
    if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", source):
        raise CBDataError()
    coverage = document.get("coverage", {})
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        raise CBDataError("coverage")
    try:
        days = [date.fromisoformat(x) for x in coverage["trading_dates"]]
        if not days or days != sorted(set(days)):
            raise ValueError()
        start = date.fromisoformat(config.get("start_date", days[0].isoformat()))
        end = date.fromisoformat(config.get("end_date", days[-1].isoformat()))
        if not 0 <= (end - start).days <= 31 or start < days[0] or end > days[-1]:
            raise ValueError()
        selected_days = [d for d in days if start <= d <= end]
        if not selected_days:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise CBDataError("coverage") from None
    raw = document.get("events")
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_EVENTS:
        raise CBDataError()
    events, previous, seen = [], None, set()
    for row in raw:
        if not isinstance(row, dict):
            raise CBDataError()
        stamp = _stamp(row.get("time")); local = stamp.astimezone(zone)
        if local.date() not in days:
            raise CBDataError("coverage")
        if row.get("quantity_unit") != unit or row.get("volume_kind") != "incremental":
            raise CBDataError("units")
        event_type = row.get("event_type")
        if event_type not in ("trade", "quote") or row.get("trading_status") not in ("trading", "halted"):
            raise CBDataError()
        if granularity == "1s" and (stamp.microsecond or event_type != "quote"):
            raise CBDataError("granularity")
        if granularity == "tick" and event_type != "trade":
            raise CBDataError("granularity")
        sequence = row.get("sequence", 0)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise CBDataError()
        order = (stamp, sequence)
        unique = stamp if granularity == "1s" else order
        if unique in seen or (previous is not None and order <= previous):
            raise CBDataError()
        seen.add(unique); previous = order
        event = {"time": local.isoformat(), "sequence": sequence,
                 "last_price": _finite(row.get("last_price"), positive=True),
                 "event_type": event_type, "trading_status": row["trading_status"],
                 "quantity_unit": unit, "volume_kind": "incremental"}
        for name in ("bid_price", "ask_price", "bid_volume", "ask_volume", "trade_volume"):
            if row.get(name) is not None:
                event[name] = _finite(row[name], positive=name.endswith("price"), nonnegative=True)
        if event_type == "trade" and "trade_volume" not in event:
            raise CBDataError("units")
        if row.get("trade_notional") is not None:
            notional = _finite(row["trade_notional"], nonnegative=True)
            if "trade_volume" not in event or (event["trade_volume"] == 0) != (notional == 0):
                raise CBDataError("units")
            event["trade_notional"] = notional
        if "volume_interval_start" in row:
            interval = _stamp(row["volume_interval_start"])
            if interval >= stamp or interval.astimezone(zone).date() != local.date():
                raise CBDataError("units")
            event["volume_interval_start"] = interval.astimezone(zone).isoformat()
        for name in ("quote_age_ms", "last_trade_age_ms"):
            if row.get(name) is not None:
                event[name] = _finite(row[name], nonnegative=True)
        if "source_recv_time" in row:
            received = _stamp(row["source_recv_time"])
            if received > stamp:
                raise CBDataError()
            event["source_recv_time"] = received.isoformat()
        if row.get("receive_time_bucket") is True:
            event["receive_time_bucket"] = True
        if "bid_price" in event and "ask_price" in event and event["bid_price"] > event["ask_price"]:
            raise CBDataError()
        if start <= local.date() <= end:
            events.append(event)
    lookback = config.get("lookback_events", 60)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or not 2 <= lookback <= 10000 or len(events) <= lookback:
        raise CBDataError("coverage")
    # Declared full-day coverage is also checked against observed session edges.
    # For snapshots enforce genuine ~1s density; never upsample missing observations.
    for day in selected_days:
        stamps = [datetime.fromisoformat(e["time"]) for e in events if e["time"][:10] == day.isoformat()]
        for opening, closing in sessions:
            a, b = datetime.combine(day, opening, zone), datetime.combine(day, closing, zone)
            observed = [t for t in stamps if a <= t <= b]
            if not observed or (observed[0] - a).total_seconds() > 5 or (b - observed[-1]).total_seconds() > 5:
                raise CBDataError("coverage")
            if granularity == "1s":
                gaps = [(y - x).total_seconds() for x, y in zip(observed, observed[1:])]
                if len(observed) < .95 * (b - a).total_seconds() or max(gaps, default=0) > 5:
                    raise CBDataError("granularity")
    contexts = _contexts(document)
    first, last = _stamp(events[0]["time"]), _stamp(events[-1]["time"])
    for key, rows in contexts.items():
        contexts[key] = [r for r in rows if _stamp(r["available_at"]) <= last and _stamp(r["asof"]) >= first - timedelta(seconds=300)]
    factors = {"ohlcv": {"available": True, "reason": "可从真实事件的历史窗口计算量价特征。"}}
    for public, key in (("stock", "underlying"), ("sector", "sector"), ("market", "market")):
        valid = any(_stamp(r["available_at"]) >= first for r in contexts[key])
        factors[public] = {"available": valid, "reason": "已提供带可用时间的同期因子，逐事件仍需检查时效。" if valid else "缺少同期可用的历史因子。"}
    book = any(all(k in e for k in ("bid_price", "ask_price", "bid_volume", "ask_volume")) for e in events)
    factors["orderbook"] = {"available": book, "reason": "样本含最优买卖档，不代表五档完整。" if book else "缺少买卖报价和委托量。"}
    premium = any("conversion_premium" in r["values"] for r in contexts["underlying"])
    factors["conversion_premium"] = {"available": premium, "reason": "提供历史时点转股溢价因子。" if premium else "缺少当时有效的转股价和同期正股数据。"}
    requested = config.get("factors", ["ohlcv"])
    if not isinstance(requested, list) or any(k not in factors for k in requested):
        raise CBDataError("config")
    if any(not factors[k]["available"] for k in requested):
        raise CBDataError("context")
    warnings = ["仅回放实际记录的行情事件，不填补秒点；完整性声明来自配置的数据集。",
                "关联因子按可用时间和最大时效逐事件检查，缺失值不会填为零。"]
    if not book:
        warnings.append("没有盘口深度，无法据此核验五档排队和市场冲击。")
    if source.startswith("databento"):
        warnings.append("数据仅代表纳斯达克单一场所的最优档报价，并非全市场 NBBO 或五档盘口。")
    evaluation_start = _stamp(config["evaluation_start"]) if config.get("evaluation_start") else None
    evaluation_end = _stamp(config["evaluation_end"]) if config.get("evaluation_end") else None
    if evaluation_start or evaluation_end:
        if not evaluation_start or not evaluation_end or not first <= evaluation_start < evaluation_end <= last:
            raise CBDataError("config")
        events = [e for e in events if evaluation_start <= _stamp(e["time"]) <= evaluation_end]
        if len(events) <= lookback:
            raise CBDataError("coverage")
        contexts = {key: [r for r in rows if _stamp(r["available_at"]) <= evaluation_end] for key, rows in contexts.items()}
    metadata = {"dataset_id": document.get("dataset_id"), "source": source,
                "hash": hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "source_sha256": document.get("source_sha256"),
                "granularity": granularity, "unit": "张" if unit == "bonds" else "股",
                "quantity_unit": unit, "instrument": instrument, "warnings": warnings,
                "factor_availability": factors, "coverage": {"start": events[0]["time"], "end": events[-1]["time"],
                "trading_dates": [d.isoformat() for d in selected_days], "events": len(events), "complete": True,
                "scope": "evaluation_window" if evaluation_start else "full_session"}}
    return events, contexts, metadata


def load_cb_dataset(config: dict):
    """Return validated events, PIT contexts and metadata; never call a provider."""
    try:
        return _prepare(_dataset(config), config)
    except CBDataError:
        raise
    except Exception:
        raise CBDataError() from None


def _db_sources(config):
    symbol = _symbol((config or {}).get("symbol", "113677")).split(".")[0]
    path = Path(os.environ.get("DATABASE_PATH", "./data/stock_analysis.db")).resolve()
    if not path.is_file():
        return []
    result = []
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
            conn.execute("PRAGMA query_only=ON")
            targets = [("stock_ticks", symbol, "本地标的采集快照")]
            if symbol == "113677":
                targets.append(("stock_ticks", "603306", "本地华懋科技采集快照"))
            for table, code, label in targets:
                row = conn.execute(f"SELECT count(*),min(timestamp),max(timestamp) FROM {table} WHERE code=?", (code,)).fetchone()
                result.append({"key": f"local-{table}-{code}", "label": label,
                               "status": "partial" if row[0] else "unavailable", "frequency": "irregular_snapshot",
                               "coverage_start": row[1], "coverage_end": row[2], "rows": row[0],
                               "reason": "采集快照并非逐笔；单位、增量区间和完整交易日尚未核验。"})
    except sqlite3.Error:
        return []
    return result


def availability(config=None):
    """Availability is factual and read-only, with safe fixed user-facing errors."""
    checked_at = _now().isoformat()
    try:
        sources = _db_sources(config)
    except CBDataError:
        sources = []
    default_factors = {key: {"available": False, "reason": "缺少已核验的同期秒级数据。"}
                       for key in ("ohlcv", "stock", "sector", "market", "orderbook", "conversion_premium")}
    try:
        _, _, metadata = load_cb_dataset(config or {})
        actual_symbol = metadata["instrument"]["symbol"].split(".")[0]
        allowed_codes = {actual_symbol, "603306"} if actual_symbol == "113677" else {actual_symbol}
        sources = [s for s in sources if s["key"].split("-")[-1] in allowed_codes]
        coverage = metadata["coverage"]
        sources.insert(0, {"key": "prepared-dataset", "label": "已配置的真实事件数据集", "status": "available",
                           "frequency": metadata["granularity"], "coverage_start": coverage["start"],
                           "coverage_end": coverage["end"], "reason": "已通过单位、事件语义和交易时段检查。"})
        return {"status": "available", "checked_at": checked_at, "message": "已配置可回放的真实事件数据。",
                "sources": sources, "available_granularities": [metadata["granularity"]],
                "factor_availability": metadata["factor_availability"], "instrument": metadata["instrument"]}
    except CBDataError as exc:
        if os.environ.get("JEV_CB_DATA_DIR"):
            sources.insert(0, {"key": "prepared-dataset", "label": "已配置的事件数据集", "status": "unavailable",
                               "frequency": None, "coverage_start": None, "coverage_end": None, "reason": str(exc)})
        return {"status": "partial" if any(s["status"] == "partial" for s in sources) else "unavailable",
                "checked_at": checked_at, "message": str(exc), "sources": sources,
                "available_granularities": [], "factor_availability": default_factors}


def instruments():
    """List server catalog instruments, retaining the originally requested bond."""
    rows = []
    try:
        _, _, entries = _catalog()
        for entry in entries:
            state = availability({"dataset_id": entry["dataset_id"]})
            prepared = next((s for s in state["sources"] if s["key"] == "prepared-dataset"), {})
            rows.append({"dataset_id": entry["dataset_id"],
                         **state.get("instrument", entry.get("instrument", {})),
                         "status": state["status"], "reason": state["message"],
                         "granularity": prepared.get("frequency"),
                         "default_start_date": (prepared.get("coverage_start") or "")[:10] or None,
                         "default_end_date": (prepared.get("coverage_end") or "")[:10] or None,
                         "available_granularities": state["available_granularities"],
                         "sources": state["sources"]})
    except (CBDataError, OSError, KeyError, TypeError, ValueError):
        pass
    if not any(str(row.get("symbol", "")).split(".")[0] == "113677" for row in rows):
        bond = availability({"symbol": "113677"})
        rows.append({"dataset_id": None, "symbol": "113677", "name": "华懋转债", "currency": "CNY",
                     "quantity_unit": "bonds", "market": "SSE", "lot_size": 10, "price_tick": .001,
                     "exchange_timezone": "Asia/Shanghai", "session_close": "15:00:00",
                     "simulation_account": "cash_t0", "instrument_type": "convertible_bond",
                     "status": "partial" if bond["sources"] else "unavailable",
                     "reason": "现有华懋转债碎片快照未通过单位、交易所路由和完整交易日核验。",
                     "available_granularities": [], "sources": [s for s in bond["sources"] if s["key"].startswith("local-")]})
    return rows
