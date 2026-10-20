"""Small, timestamped Sina batches for paper execution, never synthetic ticks.

The existing Sina realtime adapter uses field 8 in shares and field 9 in CNY.
This projection also preserves source time, receive time and top-of-book depth.
"""
from __future__ import annotations

import math
import re
import json
import subprocess
import sys
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import requests

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value, *, integer=False):
    result = float(value)
    if not math.isfinite(result) or result < 0 or (integer and not result.is_integer()):
        raise ValueError("invalid live quote numeric field")
    return int(result) if integer else result


def session_status(stamp):
    local = stamp.astimezone(SHANGHAI)
    clock = local.time().replace(tzinfo=None)
    if local.weekday() >= 5:
        return "closed"
    if time(9, 30) <= clock < time(11, 30) or time(13) <= clock < time(14, 57):
        return "trading"
    return "auction" if time(14, 57) <= clock < time(15) or time(9, 15) <= clock < time(9, 30) else "closed"


def parse_sina_snapshots(text: str, received_at: str, requested_symbols):
    received = datetime.fromisoformat(received_at)
    if received.tzinfo is None:
        raise ValueError("received time requires timezone")
    result = {}
    for symbol, body in re.findall(r'var\s+hq_str_((?:sh|sz)\d{6})="([^"\n]*)";', text):
        if symbol not in requested_symbols:
            continue
        fields = body.split(",")
        if len(fields) < 33:
            raise ValueError("incomplete live snapshot")
        stamp = datetime.fromisoformat(fields[30] + "T" + fields[31]).replace(tzinfo=SHANGHAI)
        last = _number(fields[3])
        if last <= 0:
            raise ValueError("missing last price")
        quote = {"source_time": stamp.isoformat(), "received_at": received_at,
                 "last_price": last, "bid_price": _number(fields[11]), "ask_price": _number(fields[21]),
                 "bid_volume": _number(fields[10], integer=True), "ask_volume": _number(fields[20], integer=True),
                 "cumulative_volume": _number(fields[8], integer=True), "cumulative_amount": _number(fields[9]),
                 "volume_unit": "shares", "source": "sina.public_snapshot",
                 "trading_status": session_status(stamp), "provider_status": fields[32],
                 "name": fields[0], "symbol": symbol, "pre_close": _number(fields[2]),
                 "high": _number(fields[4]), "low": _number(fields[5])}
        # Different-day or unknown-status observations must never initialize a
        # live account, even when an upstream server returns HTTP 200.
        if stamp.date() != received.astimezone(SHANGHAI).date() or fields[32] != "00":
            quote["trading_status"] = "halted"
        if symbol.startswith("sh6") or symbol.startswith("sz"):
            vol, amount = quote["cumulative_volume"], quote["cumulative_amount"]
            if vol and not quote["low"] * .995 <= amount / vol <= quote["high"] * 1.005:
                raise ValueError("stock volume and amount units failed price-range crosscheck")
        result[symbol] = quote
    if set(result) != set(requested_symbols):
        raise ValueError("requested live snapshot is missing")
    return result


class SinaLiveSnapshot:
    def __init__(self, symbol: str, market_symbol="sh000001", *, market_symbols=None):
        if not re.fullmatch(r"(?:sh6|sz[03])\d{5}", symbol):
            raise ValueError("supported A-share exchange symbol required")
        self.symbol = symbol
        markets = tuple(market_symbols) if market_symbols is not None else (market_symbol,)
        if not markets or len(markets) > 5 or any(not re.fullmatch(r"sh000\d{3}", code) for code in markets):
            raise ValueError("supported public index symbols required")
        self.symbols = tuple(dict.fromkeys((symbol, *markets)))
        self.session = requests.Session()
        self.session.trust_env = False

    def poll(self):
        requested_at = datetime.now(timezone.utc).isoformat()
        with self.session.get("https://hq.sinajs.cn/list=" + ",".join(self.symbols),
                              headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"},
                              timeout=(2, 3), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError(f"quote_http_{response.status_code}")
            raw = response.raw.read(32769, decode_content=True)
            if len(raw) > 32768:
                raise ValueError("live quote response exceeds limit")
        received_at = datetime.now(timezone.utc).isoformat()
        text = raw.decode("gb18030")
        quotes = parse_sina_snapshots(text, received_at, self.symbols)
        return {"requested_at": requested_at, "received_at": received_at, "quotes": quotes, "raw": text}

    def close(self):
        self.session.close()


class BoundedSinaLiveSnapshot:
    """A request process gives DNS/TLS/body reads a real wall-clock deadline."""
    def __init__(self, symbol, market_symbol="sh000001", *, market_symbols=None):
        check = SinaLiveSnapshot(symbol, market_symbol, market_symbols=market_symbols)
        self.symbol, self.market_symbol = symbol, market_symbol
        self.market_symbols = tuple(code for code in check.symbols if code != symbol)
        check.close()

    def poll(self):
        try:
            completed = subprocess.run([sys.executable, "-m", "data_provider.jev_live_snapshot",
                                        self.symbol, *self.market_symbols], capture_output=True,
                                       text=True, timeout=6, check=False)
        except subprocess.TimeoutExpired:
            raise ValueError("quote_wall_clock_timeout") from None
        if completed.returncode != 0 or len(completed.stdout) > 100000:
            raise ValueError("quote_worker_failed")
        return json.loads(completed.stdout)

    def close(self):
        pass


if __name__ == "__main__":
    feed = SinaLiveSnapshot(sys.argv[1], market_symbols=sys.argv[2:])
    try:
        print(json.dumps(feed.poll(), ensure_ascii=False))
    except Exception:
        raise SystemExit(1)
    finally:
        feed.close()
