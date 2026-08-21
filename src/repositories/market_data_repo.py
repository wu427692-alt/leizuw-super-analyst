"""Persistence helpers for locally cached multi-timeframe market data."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.storage import DatabaseManager, MarketIndexBar, StockDaily, StockIntraday, StockTick, normalize_daily_storage_code


# Multi-row SQLite inserts allocate one host parameter for every column in
# every row.  Fifty rows stays below even conservative 999-variable builds.
_SQLITE_INSERT_BATCH_ROWS = 50


class MarketDataRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def daily_range(self, code: str, start: date, end: date) -> List[StockDaily]:
        storage_code = normalize_daily_storage_code(code)
        with self.db.get_session() as session:
            return list(session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == storage_code, StockDaily.date >= start, StockDaily.date <= end))
                .order_by(StockDaily.date)
            ).scalars().all())

    def previous_daily_close(self, code: str, before: date) -> Optional[float]:
        storage_code = normalize_daily_storage_code(code)
        with self.db.get_session() as session:
            return session.execute(
                select(StockDaily.close)
                .where(and_(StockDaily.code == storage_code, StockDaily.date < before))
                .order_by(StockDaily.date.desc())
                .limit(1)
            ).scalar_one_or_none()

    def intraday_range(
        self,
        code: str,
        start: datetime,
        end: datetime,
        *,
        frequency: str = "1MIN",
    ) -> List[StockIntraday]:
        storage_code = normalize_daily_storage_code(code)
        with self.db.get_session() as session:
            return list(session.execute(
                select(StockIntraday)
                .where(and_(
                    StockIntraday.code == storage_code,
                    StockIntraday.frequency == frequency,
                    StockIntraday.timestamp >= start,
                    StockIntraday.timestamp <= end,
                ))
                .order_by(StockIntraday.timestamp)
            ).scalars().all())

    def upsert_intraday(
        self,
        code: str,
        rows: Iterable[Dict[str, Any]],
        *,
        frequency: str = "1MIN",
        source: str,
    ) -> int:
        storage_code = normalize_daily_storage_code(code)
        now = datetime.now()
        payload: List[Dict[str, Any]] = []
        for row in rows:
            timestamp = row.get("timestamp")
            if not isinstance(timestamp, datetime):
                continue
            payload.append({
                "code": storage_code,
                "timestamp": timestamp.replace(second=0, microsecond=0),
                "frequency": frequency,
                "open": _number(row.get("open")),
                "high": _number(row.get("high")),
                "low": _number(row.get("low")),
                "close": _number(row.get("close")),
                "volume": _number(row.get("volume")),
                "amount": _number(row.get("amount")),
                "data_source": source,
                "created_at": now,
                "updated_at": now,
            })
        if not payload:
            return 0
        with self.db.get_session() as session:
            for offset in range(0, len(payload), _SQLITE_INSERT_BATCH_ROWS):
                stmt = sqlite_insert(StockIntraday).values(
                    payload[offset:offset + _SQLITE_INSERT_BATCH_ROWS]
                )
                excluded = stmt.excluded
                session.execute(stmt.on_conflict_do_update(
                    index_elements=["code", "timestamp", "frequency"],
                    set_={
                        "open": excluded.open,
                        "high": excluded.high,
                        "low": excluded.low,
                        "close": excluded.close,
                        "volume": excluded.volume,
                        "amount": excluded.amount,
                        "data_source": excluded.data_source,
                        "updated_at": excluded.updated_at,
                    },
                ))
            session.commit()
        return len(payload)

    def tick_range(self, code: str, start: datetime, end: datetime) -> List[StockTick]:
        storage_code = normalize_daily_storage_code(code)
        with self.db.get_session() as session:
            return list(session.execute(
                select(StockTick)
                .where(and_(StockTick.code == storage_code, StockTick.timestamp >= start, StockTick.timestamp <= end))
                .order_by(StockTick.timestamp)
            ).scalars().all())

    def prune_realtime_sessions(self, *, keep_sessions: int = 5) -> Dict[str, int]:
        """Keep only the newest N observed trading dates in second-level tables."""
        safe_sessions = max(1, min(int(keep_sessions), 30))
        removed_ticks = 0
        removed_indices = 0
        with self.db.get_session() as session:
            tick_codes = session.execute(select(StockTick.code).distinct()).scalars().all()
            for code in tick_codes:
                dates = session.execute(
                    select(func.date(StockTick.timestamp))
                    .where(StockTick.code == code)
                    .distinct()
                    .order_by(func.date(StockTick.timestamp).desc())
                ).scalars().all()
                if len(dates) > safe_sessions:
                    cutoff = datetime.fromisoformat(str(dates[safe_sessions - 1]))
                    result = session.execute(delete(StockTick).where(and_(
                        StockTick.code == code,
                        StockTick.timestamp < cutoff,
                    )))
                    removed_ticks += int(result.rowcount or 0)
            index_symbols = session.execute(
                select(MarketIndexBar.symbol)
                .where(MarketIndexBar.frequency == "1SEC")
                .distinct()
            ).scalars().all()
            for symbol in index_symbols:
                dates = session.execute(
                    select(func.date(MarketIndexBar.timestamp))
                    .where(and_(MarketIndexBar.symbol == symbol, MarketIndexBar.frequency == "1SEC"))
                    .distinct()
                    .order_by(func.date(MarketIndexBar.timestamp).desc())
                ).scalars().all()
                if len(dates) > safe_sessions:
                    cutoff = datetime.fromisoformat(str(dates[safe_sessions - 1]))
                    result = session.execute(delete(MarketIndexBar).where(and_(
                        MarketIndexBar.symbol == symbol,
                        MarketIndexBar.frequency == "1SEC",
                        MarketIndexBar.timestamp < cutoff,
                    )))
                    removed_indices += int(result.rowcount or 0)
            session.commit()
        return {"stock_ticks": removed_ticks, "index_ticks": removed_indices}

    def latest_ticks(self, codes: Iterable[str]) -> Dict[str, StockTick]:
        """Return one newest locally collected tick for each requested symbol."""
        normalized = list(dict.fromkeys(
            normalize_daily_storage_code(str(code or "")) for code in codes if str(code or "").strip()
        ))
        if not normalized:
            return {}
        with self.db.get_session() as session:
            latest = (
                select(StockTick.code.label("code"), func.max(StockTick.timestamp).label("timestamp"))
                .where(StockTick.code.in_(normalized))
                .group_by(StockTick.code)
                .subquery()
            )
            rows = session.execute(
                select(StockTick).join(
                    latest,
                    and_(StockTick.code == latest.c.code, StockTick.timestamp == latest.c.timestamp),
                )
            ).scalars().all()
            return {row.code: row for row in rows}

    def upsert_ticks(self, rows: Iterable[Dict[str, Any]], *, source: str) -> int:
        now = datetime.now()
        payload: List[Dict[str, Any]] = []
        for row in rows:
            timestamp = row.get("timestamp")
            code = normalize_daily_storage_code(str(row.get("code") or ""))
            if not code or not isinstance(timestamp, datetime):
                continue
            payload.append({
                "code": code, "timestamp": timestamp.replace(microsecond=0),
                "price": _number(row.get("price")), "open": _number(row.get("open")),
                "high": _number(row.get("high")), "low": _number(row.get("low")),
                "pre_close": _number(row.get("pre_close")), "volume": _number(row.get("volume")),
                "amount": _number(row.get("amount")),
                "volume_delta": _number(row.get("volume_delta")),
                "amount_delta": _number(row.get("amount_delta")),
                "change": _number(row.get("change")),
                "pct_chg": _number(row.get("change_percent")), "data_source": source,
                "fetched_at": now, "created_at": now, "updated_at": now,
            })
        if not payload:
            return 0
        latest = self.latest_ticks(item["code"] for item in payload)
        state: Dict[str, Dict[str, Any]] = {
            code: {
                "timestamp": row.timestamp,
                "volume": row.volume,
                "amount": row.amount,
                "volume_delta": row.volume_delta,
                "amount_delta": row.amount_delta,
            }
            for code, row in latest.items()
        }
        payload.sort(key=lambda item: (item["code"], item["timestamp"]))
        for item in payload:
            previous = state.get(item["code"])
            if previous and item["timestamp"] == previous["timestamp"]:
                if item["volume_delta"] is None:
                    item["volume_delta"] = previous["volume_delta"]
                if item["amount_delta"] is None:
                    item["amount_delta"] = previous["amount_delta"]
            elif previous and item["timestamp"].date() == previous["timestamp"].date():
                if item["volume_delta"] is None:
                    item["volume_delta"] = _non_negative_delta(item["volume"], previous["volume"])
                if item["amount_delta"] is None:
                    item["amount_delta"] = _non_negative_delta(item["amount"], previous["amount"])
            state[item["code"]] = item
        with self.db.get_session() as session:
            for offset in range(0, len(payload), _SQLITE_INSERT_BATCH_ROWS):
                stmt = sqlite_insert(StockTick).values(
                    payload[offset:offset + _SQLITE_INSERT_BATCH_ROWS]
                )
                excluded = stmt.excluded
                session.execute(stmt.on_conflict_do_update(
                    index_elements=["code", "timestamp"],
                    set_={
                        "price": excluded.price, "open": excluded.open, "high": excluded.high,
                        "low": excluded.low, "pre_close": excluded.pre_close, "volume": excluded.volume,
                        "amount": excluded.amount, "volume_delta": excluded.volume_delta,
                        "amount_delta": excluded.amount_delta, "change": excluded.change,
                        "pct_chg": excluded.pct_chg,
                        "data_source": excluded.data_source, "fetched_at": excluded.fetched_at,
                        "updated_at": excluded.updated_at,
                    },
                ))
            session.commit()
        return len(payload)

    def status(self) -> Dict[str, Any]:
        with self.db.get_session() as session:
            daily = session.query(StockDaily).count()
            intraday = session.query(StockIntraday).count()
            ticks = session.query(StockTick).count()
            index_bars = session.query(MarketIndexBar).count()
            latest_intraday = session.execute(
                select(StockIntraday.timestamp).order_by(StockIntraday.timestamp.desc()).limit(1)
            ).scalar_one_or_none()
            latest_tick = session.execute(
                select(StockTick.timestamp).order_by(StockTick.timestamp.desc()).limit(1)
            ).scalar_one_or_none()
            tick_symbols = session.execute(select(func.count(func.distinct(StockTick.code)))).scalar_one()
            index_tick_symbols = session.execute(select(func.count(func.distinct(MarketIndexBar.symbol))).where(
                MarketIndexBar.frequency == "1SEC"
            )).scalar_one()
            minute_symbols = session.execute(select(func.count(func.distinct(StockIntraday.code)))).scalar_one()
        return {
            "daily_rows": daily,
            "intraday_rows": intraday,
            "tick_rows": ticks,
            "index_rows": index_bars,
            "latest_intraday_at": latest_intraday.isoformat() if latest_intraday else None,
            "latest_tick_at": latest_tick.isoformat() if latest_tick else None,
            "tick_symbols": int(tick_symbols or 0),
            "index_tick_symbols": int(index_tick_symbols or 0),
            "minute_symbols": int(minute_symbols or 0),
            "storage": "sqlite",
        }

    def index_range(self, symbol: str, start: datetime, end: datetime, *, frequency: str) -> List[MarketIndexBar]:
        with self.db.get_session() as session:
            return list(session.execute(
                select(MarketIndexBar)
                .where(and_(
                    MarketIndexBar.symbol == symbol.upper(),
                    MarketIndexBar.frequency == frequency,
                    MarketIndexBar.timestamp >= start,
                    MarketIndexBar.timestamp <= end,
                ))
                .order_by(MarketIndexBar.timestamp)
            ).scalars().all())

    def latest_index_bars(self, symbols: Iterable[str], *, frequency: str = "1SEC") -> Dict[str, MarketIndexBar]:
        normalized = list(dict.fromkeys(str(symbol).upper() for symbol in symbols if str(symbol or "").strip()))
        if not normalized:
            return {}
        with self.db.get_session() as session:
            latest = (
                select(MarketIndexBar.symbol.label("symbol"), func.max(MarketIndexBar.timestamp).label("timestamp"))
                .where(and_(MarketIndexBar.symbol.in_(normalized), MarketIndexBar.frequency == frequency))
                .group_by(MarketIndexBar.symbol).subquery()
            )
            rows = session.execute(select(MarketIndexBar).join(latest, and_(
                MarketIndexBar.symbol == latest.c.symbol, MarketIndexBar.timestamp == latest.c.timestamp,
            )).where(MarketIndexBar.frequency == frequency)).scalars().all()
            return {row.symbol: row for row in rows}

    def previous_index_close(self, symbol: str, before: datetime) -> Optional[float]:
        with self.db.get_session() as session:
            return session.execute(
                select(MarketIndexBar.close)
                .where(and_(
                    MarketIndexBar.symbol == symbol.upper(),
                    MarketIndexBar.frequency == "1D",
                    MarketIndexBar.timestamp < before,
                ))
                .order_by(MarketIndexBar.timestamp.desc())
                .limit(1)
            ).scalar_one_or_none()

    def upsert_index(self, symbol: str, rows: Iterable[Dict[str, Any]], *, frequency: str, source: str) -> int:
        now = datetime.now()
        payload: List[Dict[str, Any]] = []
        for row in rows:
            timestamp = row.get("timestamp")
            if not isinstance(timestamp, datetime):
                continue
            payload.append({
                "symbol": symbol.upper(), "timestamp": timestamp, "frequency": frequency,
                "open": _number(row.get("open")), "high": _number(row.get("high")),
                "low": _number(row.get("low")), "close": _number(row.get("close")),
                "volume": _number(row.get("volume")), "amount": _number(row.get("amount")),
                "volume_delta": _number(row.get("volume_delta")),
                "amount_delta": _number(row.get("amount_delta")),
                "pct_chg": _number(row.get("change_percent")), "data_source": source,
                "created_at": now, "updated_at": now,
            })
        if not payload:
            return 0
        if frequency == "1SEC":
            previous = self.latest_index_bars([symbol], frequency="1SEC").get(symbol.upper())
            state = {
                "timestamp": previous.timestamp,
                "volume": previous.volume,
                "amount": previous.amount,
                "volume_delta": previous.volume_delta,
                "amount_delta": previous.amount_delta,
            } if previous is not None else None
            payload.sort(key=lambda item: item["timestamp"])
            for item in payload:
                if state and item["timestamp"] == state["timestamp"]:
                    if item["volume_delta"] is None:
                        item["volume_delta"] = state["volume_delta"]
                    if item["amount_delta"] is None:
                        item["amount_delta"] = state["amount_delta"]
                elif state and item["timestamp"].date() == state["timestamp"].date():
                    if item["volume_delta"] is None:
                        item["volume_delta"] = _non_negative_delta(item["volume"], state["volume"])
                    if item["amount_delta"] is None:
                        item["amount_delta"] = _non_negative_delta(item["amount"], state["amount"])
                state = item
        with self.db.get_session() as session:
            for offset in range(0, len(payload), _SQLITE_INSERT_BATCH_ROWS):
                stmt = sqlite_insert(MarketIndexBar).values(
                    payload[offset:offset + _SQLITE_INSERT_BATCH_ROWS]
                )
                excluded = stmt.excluded
                session.execute(stmt.on_conflict_do_update(
                    index_elements=["symbol", "timestamp", "frequency"],
                    set_={
                        "open": excluded.open, "high": excluded.high, "low": excluded.low,
                        "close": excluded.close, "volume": excluded.volume, "amount": excluded.amount,
                        "volume_delta": excluded.volume_delta, "amount_delta": excluded.amount_delta,
                        "pct_chg": excluded.pct_chg, "data_source": excluded.data_source,
                        "updated_at": excluded.updated_at,
                    },
                ))
            session.commit()
        return len(payload)


def _number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _non_negative_delta(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Convert cumulative exchange totals into an interval fact without inventing resets."""
    if current is None or previous is None or current < previous:
        return None
    return current - previous
