"""Bounded public context for live paper trading; missing data stays missing.

CPO uses a frozen actual Eastmoney BK1128 member list and fresh Sina prices,
explicitly labelled an equal-weight reference rather than an official index.
News is read only from the existing public Tushare-news collector. Optional
delayed board quotes are never promoted to fresh realtime context.
"""
from __future__ import annotations

import json
from copy import deepcopy
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from data_provider.jev_live_snapshot import parse_sina_snapshots

UTC = timezone.utc
CPO_REFRESH_SECONDS = 5
NEWS_REFRESH_SECONDS = 60
PUBLIC_NEWS_SOURCES = tuple("tushare.news." + name for name in
                            ("cls", "sina", "10jqka", "eastmoney", "wallstreetcn", "yicai"))
INDEX_IDENTITIES = {"hs300": ("sh000300", "沪深300"), "zz1000": ("sh000852", "中证1000")}
CPO_URL = "https://quote.eastmoney.com/bk/90.BK1128.html"
_SECTOR_TERMS = ("cpo", "npo", "光模块", "光通信", "光互连", "硅光", "共封装", "新易盛", "中际旭创", "天孚通信")
_MACRO_TERMS = ("沪深300", "中证1000", "央行", "降息", "降准", "货币政策", "人民币", "a股", "证监会")


def _stamp(value, *, naive_utc=False):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        if not naive_utc:
            raise ValueError("context timestamp requires timezone")
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _now():
    return datetime.now(UTC)


def _number(value, *, positive=False):
    if isinstance(value, bool):
        raise ValueError("invalid context numeric field")
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError("invalid context numeric field")
    return value


def _empty(key, label, source, now, reason, *, status="missing", max_age_ms=15000):
    return {"key": key, "label": label, "source": source, "status": status,
            "reason": reason, "source_time": None, "observed_at": now.isoformat(),
            "available_at": now.isoformat(), "age_ms": None, "max_age_ms": max_age_ms,
            "is_delayed": False, "metrics": {}, "items": []}


def parse_index_context(quote, key, now=None):
    """Project a separately verified Sina index; do not invent a new quote time."""
    now = now or _now()
    symbol, label = INDEX_IDENTITIES[key]
    item = _empty(key, label, "sina.public_snapshot", now, "index_missing")
    if not quote:
        return item
    try:
        if quote.get("symbol") != symbol or quote.get("name") != label:
            raise ValueError("index identity mismatch")
        stamp, received = _stamp(quote["source_time"]), _stamp(quote["received_at"])
        price, prior = _number(quote["last_price"], positive=True), _number(quote["pre_close"], positive=True)
        age = (now - stamp).total_seconds() * 1000
        future = stamp > received + timedelta(seconds=2) or received > now
        item.update(source_time=stamp.isoformat(), available_at=received.isoformat(),
                    age_ms=round(age), status="fresh" if 0 <= age <= 15000 and not future else "stale",
                    reason=None if 0 <= age <= 15000 and not future else "index_stale_or_future",
                    metrics={"price": price, "previous_close": prior,
                             "day_return_bps": (price / prior - 1) * 10000})
    except (KeyError, TypeError, ValueError, OverflowError):
        item.update(status="error", reason="invalid_index_context")
    return item


def parse_cpo_board(payload, observed_at, *, delayed=False):
    now = _stamp(observed_at)
    source = "eastmoney.public_board_delayed" if delayed else "eastmoney.public_board"
    item = _empty("cpo", "CPO概念 · 东方财富 BK1128", source, now, "board_missing")
    item["source_url"] = CPO_URL
    item["is_delayed"] = delayed
    try:
        data = payload["data"]
        if payload.get("rc") != 0 or data["f57"] != "BK1128" or data["f58"] != "CPO概念":
            raise ValueError("CPO board identity mismatch")
        digits = int(data["f59"])
        if digits != 2:
            raise ValueError("unverified CPO scale")
        price = _number(data["f43"], positive=True) / 10 ** digits
        prior = _number(data["f60"], positive=True) / 10 ** digits
        stamp = datetime.fromtimestamp(_number(data["f86"], positive=True), UTC)
        age = (now - stamp).total_seconds() * 1000
        # f86 is the publisher's update clock, not an exchange trade clock.
        fresh = 0 <= age <= 15000 and not delayed
        metrics = {"price": price, "previous_close": prior,
                   "day_return_bps": (price / prior - 1) * 10000}
        for raw, field, divisor in (("f48", "amount_cny", 1), ("f168", "turnover_pct", 100)):
            if data.get(raw) not in (None, "-"):
                value = _number(data[raw]) / divisor
                if value < 0:
                    raise ValueError("invalid CPO metric")
                metrics[field] = value
        item.update(source_time=stamp.isoformat(), age_ms=round(age), metrics=metrics,
                    status="fresh" if fresh else "stale",
                    reason=None if fresh else "delayed_source_latency_unverified" if delayed else "board_stale_or_future",
                    timestamp_kind="publisher_update")
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        item.update(status="error", reason="invalid_cpo_board", metrics={})
    return item


def fetch_cpo_board():
    """One bounded HTTP attempt per host; the parent enforces a hard deadline."""
    session = requests.Session()
    session.trust_env = False
    fields = "f43,f48,f57,f58,f59,f60,f86,f168"
    try:
        for host, delayed in (("push2.eastmoney.com", False), ("push2delay.eastmoney.com", True)):
            try:
                with session.get("https://" + host + "/api/qt/stock/get",
                                 params={"secid": "90.BK1128", "fields": fields},
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": CPO_URL},
                                 timeout=(1, 1.5), allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        continue
                    raw = response.raw.read(32769, decode_content=True)
                    if len(raw) > 32768:
                        continue
                item = parse_cpo_board(json.loads(raw), _now().isoformat(), delayed=delayed)
                if item["status"] != "error":
                    return item
            except (requests.RequestException, ValueError, OSError):
                continue
        return _empty("cpo", "CPO概念 · 东方财富 BK1128", "eastmoney.public_board", _now(),
                      "cpo_source_unavailable", status="error")
    finally:
        session.close()


def bounded_cpo_board():
    try:
        result = subprocess.run([sys.executable, "-m", "data_provider.jev_live_context", "cpo"],
                                capture_output=True, text=True, timeout=6, check=False)
        if result.returncode or len(result.stdout) > 32768:
            raise ValueError("context worker failed")
        item = json.loads(result.stdout)
        if not isinstance(item, dict) or item.get("key") != "cpo":
            raise ValueError("invalid worker result")
        return item
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return _empty("cpo", "CPO概念 · 东方财富 BK1128", "eastmoney.public_board", _now(),
                      "cpo_worker_unavailable", status="error")


def parse_cpo_members(payload, observed_at):
    """Use the vendor's full constituent list, including unsupported exchanges."""
    data = payload["data"]
    rows = data["diff"]
    rows = list(rows.values()) if isinstance(rows, dict) else rows
    total = int(data["total"])
    if payload.get("rc") != 0 or not 1 <= total <= 200 or len(rows) != total:
        raise ValueError("incomplete CPO membership")
    members = []
    seen = set()
    for row in rows:
        code = str(row["f12"])
        market = row["f13"]
        if not re.fullmatch(r"\d{6}", code) or code in seen or market not in (0, 1):
            raise ValueError("invalid CPO member")
        seen.add(code)
        symbol = "sh" + code if market == 1 and code.startswith("6") else (
            "sz" + code if market == 0 and code.startswith(("0", "3")) else None)
        members.append({"code": code, "symbol": symbol})
    return {"members": members, "member_count": total, "available_at": observed_at,
            "source": "eastmoney.BK1128.constituents", "source_url": CPO_URL}


def fetch_cpo_members():
    # The delayed host supplies membership only. None of its prices are used.
    with requests.Session() as session:
        session.trust_env = False
        with session.get("https://push2delay.eastmoney.com/api/qt/clist/get",
                         params={"fs": "b:BK1128", "pn": 1, "pz": 200, "fields": "f12,f13,f14"},
                         timeout=(1, 2), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError("membership unavailable")
            raw = response.raw.read(65537, decode_content=True)
            if len(raw) > 65536:
                raise ValueError("membership response too large")
        return parse_cpo_members(json.loads(raw), _now().isoformat())


def fetch_cpo_quotes(membership):
    symbols = [row["symbol"] for row in membership["members"] if row["symbol"]]
    if not 1 <= len(symbols) <= 200 or any(not re.fullmatch(r"(?:sh6|sz[03])\d{5}", x) for x in symbols):
        raise ValueError("invalid quote batch")
    with requests.Session() as session:
        session.trust_env = False
        with session.get("https://hq.sinajs.cn/list=" + ",".join(symbols),
                         headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"},
                         timeout=(1, 3), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError("constituent quotes unavailable")
            raw = response.raw.read(131073, decode_content=True)
            if len(raw) > 131072:
                raise ValueError("constituent quote response too large")
    observed = _now().isoformat()
    text, quotes = raw.decode("gb18030"), {}
    for symbol in symbols:
        try:
            quotes.update(parse_sina_snapshots(text, observed, (symbol,)))
        except (ValueError, TypeError, OverflowError):
            continue
    return {"quotes": quotes, "observed_at": observed}


def _bounded_worker(action, payload=None):
    result = subprocess.run([sys.executable, "-m", "data_provider.jev_live_context", action],
                            input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=6, check=False)
    if result.returncode or len(result.stdout) > 180000:
        raise ValueError("context worker unavailable")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("invalid context result")
    return value


def build_cpo_proxy(membership, batch, now=None):
    now = now or _now()
    item = _empty("cpo", "CPO成分股等权参考（非官方板块指数）",
                  "sina.CPO_members_equal_weight_proxy", now, "constituent_coverage_insufficient")
    total = membership["member_count"]
    observed = _stamp(batch["observed_at"])
    available = max(observed, _stamp(membership["available_at"]))
    quotes = []
    valid = {}
    requested = {row["symbol"] for row in membership["members"] if row["symbol"]}
    for symbol, quote in batch["quotes"].items():
        if symbol not in requested:
            continue
        stamp = _stamp(quote["source_time"])
        if (0 <= (now - stamp).total_seconds() <= 15 and stamp <= observed <= now
                and quote["trading_status"] == "trading" and quote["pre_close"] > 0):
            quotes.append(quote)
            valid[symbol] = quote
    coverage = len(quotes) / total
    metrics = {"member_count": total, "quoted_member_count": len(quotes), "coverage_ratio": coverage}
    if quotes:
        returns = [q["last_price"] / q["pre_close"] - 1 for q in quotes]
        spreads = [(q["ask_price"] - q["bid_price"]) / ((q["ask_price"] + q["bid_price"]) / 2) * 10000
                   for q in quotes if 0 < q["bid_price"] <= q["ask_price"]]
        stamp = min(_stamp(q["source_time"]) for q in quotes)
        metrics.update(return_since_prev_close=sum(returns) / len(returns),
                       breadth_up=sum(value > 0 for value in returns) / len(returns))
        if spreads:
            metrics["mean_spread_bps"] = sum(spreads) / len(spreads)
        item.update(source_time=stamp.isoformat(), age_ms=round((now - stamp).total_seconds() * 1000))
    fresh = coverage >= .8 and available <= now
    item.update(status="fresh" if fresh else "stale", reason=None if fresh else item["reason"],
                available_at=available.isoformat(), metrics=metrics,
                membership_source=membership["source"], membership_available_at=membership["available_at"],
                membership_codes=[row["code"] for row in membership["members"]],
                refresh_interval_ms=CPO_REFRESH_SECONDS * 1000, timestamp_kind="oldest_included_exchange_snapshot")
    return item, valid


def _news_kind(title, symbols):
    text = title.lower()
    if "华懋" in text or "603306" in symbols:
        return "company"
    if any(term in text for term in _SECTOR_TERMS):
        return "cpo"
    if any(term in text for term in _MACRO_TERMS):
        return "macro"
    return None


def read_public_news(database_path, now=None):
    """Read a strict public-source allowlist without opening the ORM or writing."""
    now = now or _now()
    item = _empty("news", "华懋 / CPO / 大盘公开快讯", "local.tushare_public_news", now,
                  "public_news_cache_missing", max_age_ms=300000)
    path = Path(database_path)
    if not path.is_file():
        return item
    placeholders = ",".join("?" for _ in PUBLIC_NEWS_SOURCES)
    stamp = now.astimezone(UTC).replace(tzinfo=None).isoformat(" ")
    cutoff = (now - timedelta(hours=6)).astimezone(UTC).replace(tzinfo=None).isoformat(" ")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.3)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            deadline = time.monotonic() + .8
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
            rows = db.execute(
                "SELECT source_key,source_name,title,symbol_codes,event_at,ingested_at,updated_at "
                "FROM monitoring_events WHERE event_type='news' AND source_key IN (" + placeholders + ") "
                "AND event_at>=? AND event_at<=? AND ingested_at<=? AND updated_at<=? "
                "ORDER BY event_at DESC LIMIT 600", (*PUBLIC_NEWS_SOURCES, cutoff, stamp, stamp, stamp)).fetchall()
            health = db.execute(
                "SELECT max(last_success_at) FROM monitoring_sources WHERE source_key IN (" + placeholders + ") "
                "AND enabled=1 AND last_success_at<=?", (*PUBLIC_NEWS_SOURCES, stamp)).fetchone()[0]
        items, seen, counts = [], set(), {"company": 0, "cpo": 0, "macro": 0}
        candidates = []
        for row in rows:
            title = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(row["title"] or ""))).strip()[:160]
            kind = _news_kind(title, str(row["symbol_codes"] or ""))
            dedup = re.sub(r"[^\w\u4e00-\u9fff]", "", title).lower()
            if not title or not kind or dedup in seen:
                continue
            published, ingested = _stamp(row["event_at"], naive_utc=True), _stamp(row["ingested_at"], naive_utc=True)
            updated = _stamp(row["updated_at"], naive_utc=True)
            if max(published, ingested, updated) > now:
                continue
            seen.add(dedup)
            candidates.append({"title": title, "category": kind, "source": row["source_name"],
                               "source_key": row["source_key"], "published_at": published.isoformat(),
                               "first_observed_at": ingested.isoformat(), "available_at": now.isoformat(),
                               "age_ms": round((now - published).total_seconds() * 1000),
                               "untrusted_text": True})
        # Keep at least room for company and macro information when available.
        for candidate in candidates:
            kind = candidate["category"]
            if counts[kind] >= (4 if kind == "cpo" else 2):
                continue
            items.append(candidate)
            counts[kind] += 1
        for candidate in candidates:
            if len(items) == 8:
                break
            if candidate not in items:
                items.append(candidate)
        items.sort(key=lambda entry: entry["published_at"], reverse=True)
        last_ingest = max((_stamp(row["ingested_at"], naive_utc=True) for row in rows), default=None)
        health_at = min(_stamp(health, naive_utc=True), last_ingest) if health and last_ingest else None
        age = round((now - health_at).total_seconds() * 1000) if health_at else None
        fresh = age is not None and 0 <= age <= 300000
        item.update(status="fresh" if fresh else "stale", reason=None if fresh else "news_collector_stale",
                    source_time=health_at.isoformat() if health_at else None, age_ms=age,
                    items=items[:8], metrics={"headline_count": len(items[:8]), "lookback_minutes": 360},
                    timestamp_kind="collector_last_success", refresh_interval_ms=60000)
        if not items:
            item["reason"] = "no_relevant_public_headlines" if fresh else item["reason"]
    except (sqlite3.Error, OSError, ValueError, TypeError):
        item.update(status="error", reason="public_news_cache_unavailable")
    return item


def wire_context(cpo, news, now=None):
    """Only causally fresh values cross the narrow model boundary."""
    now = now or _now()
    cpo, news = deepcopy(cpo), deepcopy(news)
    if cpo.get("source_time"):
        cpo["age_ms"] = round((now - _stamp(cpo["source_time"])).total_seconds() * 1000)
        if not 0 <= cpo["age_ms"] <= 15000 and cpo["status"] == "fresh":
            cpo.update(status="stale", reason="constituent_snapshot_stale")
    if news.get("source_time"):
        news["age_ms"] = round((now - _stamp(news["source_time"])).total_seconds() * 1000)
        if not 0 <= news["age_ms"] <= 300000 and news["status"] == "fresh":
            news.update(status="stale", reason="news_collector_stale")
    cpo_keys = ("return_since_prev_close", "breadth_up", "mean_spread_bps",
                "member_count", "coverage_ratio")
    numeric = {"status": cpo["status"], "source": cpo["source"], "source_time": cpo["source_time"],
               "available_at": cpo["available_at"], "age_seconds": cpo["age_ms"] / 1000 if cpo["age_ms"] is not None else None,
               "values": {key: cpo["metrics"][key] for key in cpo_keys if key in cpo["metrics"]}
               if cpo["status"] == "fresh" else {}}
    headlines = {"status": news["status"], "source": news["source"], "fetched_at": news["observed_at"],
                 "items": [{"title": item["title"], "summary": "", "source": item["source"][:120],
                            "url": None, "published_at": item["published_at"], "available_at": item["available_at"]}
                           for item in news["items"]
                           if 0 <= (now - _stamp(item["published_at"])).total_seconds() <= 21600]
                 if news["status"] == "fresh" else []}
    return {"context": {"cpo": numeric, "news": headlines}, "diagnostics": {"cpo": cpo, "news": news}}


class LiveExternalContext:
    """Actual CPO member quotes every 5s; public-news cache read once/minute."""
    def __init__(self, database_path=None):
        self.database_path = database_path or os.getenv("DATABASE_PATH") or "data/stock_analysis.db"
        self._news = None
        self._news_checked = float("-inf")
        self._membership = None
        self._membership_day = None
        self._cpo = None
        self._cpo_checked = float("-inf")

    def poll(self):
        poll_started = time.monotonic()
        observed_day = _now().astimezone(ZoneInfo("Asia/Shanghai")).date()
        if self._membership_day != observed_day:
            # Continuous accounts must not keep yesterday's constituent set.
            # Re-fetch at today's actual observation time, never backdate it.
            self._membership = self._cpo = None
            self._membership_day = observed_day
        if self._cpo is None or poll_started - self._cpo_checked >= CPO_REFRESH_SECONDS:
            try:
                if self._membership is None:
                    self._membership = _bounded_worker("members")
                    self._membership_day = observed_day
                batch = _bounded_worker("quotes", self._membership)
                self._cpo, _ = build_cpo_proxy(self._membership, batch, _now())
            except (subprocess.TimeoutExpired, ValueError, TypeError, KeyError, OSError):
                self._cpo = _empty("cpo", "CPO成分股等权参考（非官方板块指数）",
                                   "sina.CPO_members_equal_weight_proxy", _now(),
                                   "cpo_constituent_feed_unavailable", status="error")
            # Only this adapter throttles network calls. Measure start-to-start
            # so network latency does not add another full refresh interval.
            self._cpo_checked = poll_started
        if self._news is None or time.monotonic() - self._news_checked >= NEWS_REFRESH_SECONDS:
            self._news = read_public_news(self.database_path)
            self._news_checked = time.monotonic()
        return wire_context(self._cpo, self._news)

    def close(self):
        pass


if __name__ == "__main__":
    try:
        action = sys.argv[1]
        if action == "cpo":
            result = fetch_cpo_board()
        elif action == "members":
            result = fetch_cpo_members()
        elif action == "quotes":
            raw = sys.stdin.read(65537)
            if len(raw) > 65536:
                raise ValueError("context input too large")
            result = fetch_cpo_quotes(json.loads(raw))
        else:
            raise ValueError("unknown context action")
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except Exception:
        raise SystemExit(1)
