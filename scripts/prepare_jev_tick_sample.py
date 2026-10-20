#!/usr/bin/env python3
"""Import an already downloaded Databento MBP-1 CSV gzip. No network or credentials.

Seconds are receive-time buckets [start,end); rows available at the exact end
belong to the next bucket. Empty buckets are omitted. T action size is counted
once; book updates never create fictitious trades. This is one venue's BBO.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

NANO = 1_000_000_000


def write_json(path, document, *, pretty=False):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False,
                                   indent=2 if pretty else None,
                                   separators=None if pretty else (",", ":")))
    temporary.replace(path)


def ns_time(value, cache):
    if not isinstance(value, str) or len(value) < 20 or not value.endswith("Z"):
        raise ValueError("expected UTC timestamp")
    day = value[:10]
    if day not in cache:
        cache[day] = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
    whole, dot, fraction = value[11:-1].partition(".")
    hh, mm, ss = (int(x) for x in whole.split(":"))
    if len(fraction) > 9:
        raise ValueError("timestamp precision exceeds nanoseconds")
    return (cache[day] + hh * 3600 + mm * 60 + ss) * NANO + int((fraction + "000000000")[:9])


def number(value):
    if value in (None, ""):
        return None
    result = float(value)
    if not 0 < result < 1_000_000:
        return None
    return result


def aggregate(rows, symbol="NVDA", trading_date="2025-09-16"):
    zone = ZoneInfo("America/New_York")
    opening = int(datetime.fromisoformat(trading_date + "T09:30:00").replace(tzinfo=zone).timestamp())
    closing = int(datetime.fromisoformat(trading_date + "T16:00:00").replace(tzinfo=zone).timestamp())
    events, stats, cache = [], Counter(), {}
    stats["symbols"] = Counter()
    previous_recv = None
    bucket = None
    state = {"last": None, "trade_ns": None, "bid": None, "ask": None, "bid_sz": None,
             "ask_sz": None, "quote_ns": None, "volume": 0, "notional_nano": 0, "sequence": 0, "recv_ns": None}

    def flush():
        if bucket is None or not opening < bucket <= closing or state["last"] is None:
            return
        end = datetime.fromtimestamp(bucket, zone)
        event = {"time": end.isoformat(), "sequence": state["sequence"], "last_price": state["last"],
                 "trade_volume": state["volume"], "quantity_unit": "shares", "volume_kind": "incremental",
                 "trade_notional": state["notional_nano"] / NANO,
                 "event_type": "quote", "trading_status": "trading",
                 "volume_interval_start": datetime.fromtimestamp(bucket - 1, zone).isoformat(),
                 "last_trade_age_ms": round((bucket * NANO - state["trade_ns"]) / 1e6, 3),
                 "source_recv_time": datetime.fromtimestamp(state["recv_ns"] / NANO, timezone.utc).isoformat(),
                 "receive_time_bucket": True}
        if state["bid"] is not None and state["ask"] is not None and state["bid"] <= state["ask"]:
            event.update(bid_price=state["bid"], ask_price=state["ask"],
                         bid_volume=state["bid_sz"], ask_volume=state["ask_sz"],
                         quote_age_ms=round((bucket * NANO - state["quote_ns"]) / 1e6, 3))
        events.append(event)

    for row in rows:
        stats["raw_rows"] += 1
        stats["symbols"][row["symbol"]] += 1
        if row["symbol"] != symbol:
            continue
        received = ns_time(row["ts_recv"], cache)
        if previous_recv is not None and received < previous_recv:
            raise ValueError("input is not ordered by ts_recv; refusing lookahead-prone aggregation")
        previous_recv = received
        key = received // NANO + 1
        if bucket != key:
            flush()
            bucket = key
            state["volume"] = 0
            state["notional_nano"] = 0
        state["recv_ns"] = received
        state["sequence"] = int(row["sequence"])
        stats["selected_rows"] += 1
        stats["action_" + row["action"]] += 1
        if row["action"] == "T":
            price = number(row["price"])
            size = int(row["size"])
            if price is None or size < 0:
                raise ValueError("invalid trade")
            state["last"], state["trade_ns"] = price, received
            state["volume"] += size
            notional = int(Decimal(row["price"]) * NANO) * size
            state["notional_nano"] += notional
            if opening * NANO <= received < closing * NANO:
                stats["regular_trade_volume"] += size
                stats["regular_trade_notional_nano"] += notional
                stats["regular_trade_records"] += 1
        bid, ask = number(row["bid_px_00"]), number(row["ask_px_00"])
        # Each MBP-1 row carries its current BBO. An empty side invalidates that
        # side now; retaining a withdrawn older quote would invent liquidity.
        state.update(bid=bid, ask=ask, bid_sz=int(row["bid_sz_00"]), ask_sz=int(row["ask_sz_00"]), quote_ns=received)
    flush()
    if not events:
        raise ValueError("no observed regular-session events")
    stats["aggregated_events"] = len(events)
    stats["aggregated_trade_volume"] = sum(e["trade_volume"] for e in events)
    stats["regular_trade_notional_usd"] = stats.pop("regular_trade_notional_nano", 0) / NANO
    stats["expected_session_seconds"] = closing - opening
    stats["unobserved_seconds"] = closing - opening - len(events)
    stats["coverage_start"] = events[0]["time"]
    stats["coverage_end"] = events[-1]["time"]
    stamps = [datetime.fromisoformat(e["time"]).timestamp() for e in events]
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    stats["max_gap_seconds"] = max(gaps, default=0)
    stats["missing_book_events"] = sum("bid_price" not in e for e in events)
    if stats["aggregated_trade_volume"] != stats["regular_trade_volume"]:
        raise ValueError("trade volume reconciliation failed")
    return events, dict(stats)


def prepare(input_path, output_root):
    source = Path(input_path)
    if source.suffix != ".gz" or not source.is_file():
        raise ValueError("use the completed .gz download, not a partial file")
    # A full gzip read also verifies the CRC and detects truncated downloads.
    digest = hashlib.sha256()
    with gzip.open(source, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    with gzip.open(source, "rt", newline="") as handle:
        events, stats = aggregate(csv.DictReader(handle))
    dataset_id = "nvda-xnas-20250916-1s"
    instrument = {"symbol": "NVDA", "name": "NVIDIA", "currency": "USD", "quantity_unit": "shares",
                  "market": "XNAS", "lot_size": 1, "price_tick": .01,
                  "exchange_timezone": "America/New_York", "session_close": "16:00:00",
                  "sessions": [["09:30:00", "16:00:00"]], "simulation_account": "unlevered_margin",
                  "unit_verified": True, "same_day_sell": True, "asset_class": "equity",
                  "instrument_type": "us_equity"}
    complete = stats["coverage_start"][:19].endswith("09:30:01") and stats["coverage_end"][:19].endswith("16:00:00")
    document = {"schema_version": 1, "dataset_id": dataset_id, "source": "databento.xnas.itch.mbp1",
                "source_sha256": digest.hexdigest(), "granularity": "1s", "instrument": instrument,
                "coverage": {"complete": complete, "trading_dates": ["2025-09-16"]},
                "events": events, "contexts": {"underlying": [], "sector": [], "market": []},
                "provenance": {"url": "https://hist.databento.com/v0/dataset/sample/download/xnas.itch/mbp-1?sample_type=equities",
                "aggregation": "nonempty receive-time second buckets; action T sizes only",
                "book_scope": "XNAS venue BBO, not consolidated NBBO", "statistics": stats}}
    target = Path(output_root) / dataset_id
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "dataset.json", document)
    catalog = {"default_dataset_id": dataset_id, "datasets": [{"dataset_id": dataset_id,
               "file": dataset_id + "/dataset.json", "instrument": instrument, "granularity": "1s",
               "default_start_date": "2025-09-16", "default_end_date": "2025-09-16"}]}
    write_json(target.parent / "catalog.json", catalog, pretty=True)
    write_json(target.parent / "import-report.json", {"source_sha256": digest.hexdigest(), **stats}, pretty=True)
    return {"dataset_id": dataset_id, "path": str(target / "dataset.json"), "source_sha256": digest.hexdigest(), **stats}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.output), ensure_ascii=False, indent=2))
