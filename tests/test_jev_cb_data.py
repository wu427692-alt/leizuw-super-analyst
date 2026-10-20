"""Offline guards for recorded-event datasets, source isolation and time causality."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from src.services import jev_cb_data as data
from scripts.prepare_jev_tick_sample import aggregate


@pytest.fixture
def document():
    start = datetime(2025, 9, 16, 9, 30, tzinfo=timezone(timedelta(hours=-4)))
    events = [{"time": (start + timedelta(seconds=i)).isoformat(), "sequence": i,
               "last_price": 100., "bid_price": 99.99, "ask_price": 100.01,
               "bid_volume": 100, "ask_volume": 200, "trade_volume": 10,
               "volume_interval_start": (start + timedelta(seconds=i - 1)).isoformat(),
               "event_type": "quote", "quantity_unit": "shares", "volume_kind": "incremental",
               "trading_status": "trading"} for i in range(1, 61)]
    return {"schema_version": 1, "dataset_id": "fixture-nvda", "source": "offline.fixture",
            "instrument": {"symbol": "NVDA", "quantity_unit": "shares", "unit_verified": True,
                           "instrument_type": "us_equity", "currency": "USD", "lot_size": 1,
                           "simulation_account": "unlevered_margin",
                           "same_day_sell": True, "exchange_timezone": "America/New_York",
                           "sessions": [["09:30:00", "09:31:00"]]},
            "granularity": "1s", "coverage": {"complete": True, "trading_dates": ["2025-09-16"]},
            "events": events, "contexts": {"underlying": [], "sector": [], "market": []}}


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_CB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "absent.sqlite"))
    def write(document):
        (tmp_path / "dataset.json").write_text(json.dumps(document))
    return write


def config(**changes):
    return {"symbol": "NVDA", "start_date": "2025-09-16", "end_date": "2025-09-16",
            "event_granularity": "1s", "lookback_events": 2, "factors": ["ohlcv", "orderbook"], **changes}


def test_valid_raw_events_preserve_quantity_and_never_create_contexts(document, install):
    install(document)
    events, contexts, metadata = data.load_cb_dataset(config())
    assert len(events) == 60 and sum(e["trade_volume"] for e in events) == 600
    assert contexts == {"underlying": [], "sector": [], "market": []}
    assert metadata["quantity_unit"] == "shares" and metadata["unit"] == "股"
    assert metadata["factor_availability"]["orderbook"]["available"]
    assert not metadata["factor_availability"]["stock"]["available"]
    assert data.availability(config())["status"] == "available"


@pytest.mark.parametrize("edit,code", [
    (lambda d: d["instrument"].update(unit_verified=False), "units"),
    (lambda d: d["events"][0].update(volume_kind="cumulative"), "units"),
    (lambda d: d["events"][0].update(quantity_unit="bonds"), "units"),
    (lambda d: d["events"][0].update(last_price=float("nan")), "invalid"),
    (lambda d: d["events"][0].update(trade_volume=-1), "invalid"),
    (lambda d: d["events"][0].update(bid_price=101), "invalid"),
    (lambda d: d["events"][0].update(time="2025-09-16T09:30:01"), "invalid"),
    (lambda d: d["events"][0].update(volume_interval_start=d["events"][0]["time"]), "units"),
    (lambda d: d["coverage"].update(complete=False), "coverage"),
    (lambda d: d.update(events=d["events"][10:]), "coverage"),
    (lambda d: d.update(events=d["events"][::3]), "granularity"),
    (lambda d: d["events"].insert(2, deepcopy(d["events"][1])), "invalid"),
])
def test_unsafe_or_incomplete_data_is_rejected(document, install, edit, code):
    edit(document); install(document)
    with pytest.raises(data.CBDataError) as error:
        data.load_cb_dataset(config())
    assert error.value.code == code


def test_sparse_real_observations_are_not_filled(document, install):
    document["events"].pop(10); install(document)
    events, _, _ = data.load_cb_dataset(config())
    assert len(events) == 59
    assert not any(e["time"].endswith("09:30:11-04:00") for e in events)


def test_missing_requested_factor_fails_before_model(document, install):
    install(document)
    with pytest.raises(data.CBDataError, match="关联因子"):
        data.load_cb_dataset(config(factors=["stock"]))


@pytest.mark.parametrize("row", [
    {"asof": "2025-09-16T09:30:01-04:00", "values": {"return_1s": .1}},
    {"asof": "2025-09-16T09:30:02-04:00", "available_at": "2025-09-16T09:30:01-04:00", "values": {"return_1s": .1}},
    {"asof": "2025-09-16T09:30:01-04:00", "available_at": "2025-09-16T09:30:01-04:00", "values": {"raw_secret": 1}},
])
def test_context_requires_explicit_availability_time_and_allowed_values(document, install, row):
    document["contexts"]["underlying"] = [row]; install(document)
    with pytest.raises(data.CBDataError):
        data.load_cb_dataset(config())


def test_holdout_window_excludes_later_events_and_future_context(document, install):
    document["contexts"]["underlying"] = [
        {"asof": "2025-09-16T09:30:10-04:00", "available_at": "2025-09-16T09:30:11-04:00", "values": {"return_1s": .1}},
        {"asof": "2025-09-16T09:30:50-04:00", "available_at": "2025-09-16T09:30:51-04:00", "values": {"return_1s": .2}}]
    install(document)
    events, contexts, metadata = data.load_cb_dataset(config(evaluation_start="2025-09-16T09:30:10-04:00",
                                                              evaluation_end="2025-09-16T09:30:40-04:00"))
    assert len(events) == 31 and len(contexts["underlying"]) == 1
    assert metadata["coverage"]["scope"] == "evaluation_window"


def test_catalog_selection_is_not_a_user_file_path(document, tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_CB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "absent.db"))
    target = tmp_path / "fixture-nvda"; target.mkdir()
    (target / "dataset.json").write_text(json.dumps(document))
    catalog = {"default_dataset_id": "fixture-nvda", "datasets": [{"dataset_id": "fixture-nvda", "file": "fixture-nvda/dataset.json"}]}
    (tmp_path / "catalog.json").write_text(json.dumps(catalog))
    assert data.load_cb_dataset(config(dataset_id="fixture-nvda"))[2]["dataset_id"] == "fixture-nvda"
    with pytest.raises(data.CBDataError):
        data.load_cb_dataset(config(dataset_id="../../private"))
    outside = tmp_path.parent / "other.json"; outside.write_text(json.dumps(document))
    (target / "dataset.json").unlink(); (target / "dataset.json").symlink_to(outside)
    with pytest.raises(data.CBDataError, match="目录"):
        data.load_cb_dataset(config(dataset_id="fixture-nvda"))


def test_db_is_only_surveyed_and_never_used_as_unit_verified_events(tmp_path, monkeypatch):
    path = tmp_path / "prices.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stock_ticks(code TEXT,timestamp TEXT)")
        conn.execute("INSERT INTO stock_ticks VALUES('113677','2026-08-20 11:20:13')")
    before = path.read_bytes()
    monkeypatch.setenv("DATABASE_PATH", str(path)); monkeypatch.delenv("JEV_CB_DATA_DIR", raising=False)
    status = data.availability()
    assert status["status"] == "partial" and status["available_granularities"] == []
    with pytest.raises(data.CBDataError, match="碎片快照"):
        data.load_cb_dataset(config(symbol="113677"))
    assert before == path.read_bytes()


def raw_row(received, action="A", price="100", size="10", sequence="1"):
    return {"ts_recv": received, "ts_event": "2025-09-16T13:29:59.000000000Z", "symbol": "NVDA",
            "action": action, "price": price, "size": size, "sequence": sequence,
            "bid_px_00": "99.99", "ask_px_00": "100.01", "bid_sz_00": "100", "ask_sz_00": "200"}


def test_importer_receive_time_boundary_trade_volume_and_missing_seconds():
    rows = [raw_row("2025-09-16T13:29:59.000000001Z", "T", "99", "99"),
            raw_row("2025-09-16T13:30:00.500000001Z", "T", "100", "10", "2"),
            raw_row("2025-09-16T13:30:01.000000000Z", "T", "101", "20", "3"),
            raw_row("2025-09-16T13:30:04.000000001Z", "A", size="999", sequence="4")]
    events, stats = aggregate(rows)
    assert [e["time"][11:19] for e in events] == ["09:30:01", "09:30:02", "09:30:05"]
    assert [e["trade_volume"] for e in events] == [10, 20, 0]
    assert [e["trade_notional"] for e in events] == [1000, 2020, 0]
    assert [e["last_price"] for e in events] == [100, 101, 101]
    assert events[-1]["last_trade_age_ms"] == 4000
    assert stats["aggregated_trade_volume"] == 30


def test_importer_refuses_receive_time_reversal():
    with pytest.raises(ValueError, match="ts_recv"):
        aggregate([raw_row("2025-09-16T13:30:02.000000001Z", "T"), raw_row("2025-09-16T13:30:01.000000001Z", "T")])


def test_withdrawn_book_side_is_not_carried_forward_as_liquidity():
    withdrawn = raw_row("2025-09-16T13:30:01.100000000Z", sequence="2")
    withdrawn["ask_px_00"] = ""
    events, _ = aggregate([raw_row("2025-09-16T13:30:00.100000000Z", "T"), withdrawn])
    assert "ask_price" in events[0] and "ask_price" not in events[1]


def test_bucket_notional_uses_each_trade_price_not_bucket_last_price():
    events, _ = aggregate([raw_row("2025-09-16T13:30:00.100000000Z", "T", "100", "10", "1"),
                           raw_row("2025-09-16T13:30:00.200000000Z", "T", "200", "20", "2")])
    assert events[0]["trade_notional"] == 5000
    assert events[0]["trade_volume"] == 30
    assert events[0]["trade_notional"] != events[0]["last_price"] * events[0]["trade_volume"]


def test_verified_bucket_notional_survives_loader(document, install):
    document["events"][0]["trade_notional"] = 1000.01; install(document)
    assert data.load_cb_dataset(config())[0][0]["trade_notional"] == 1000.01
    document["events"][0]["trade_volume"] = 0; install(document)
    with pytest.raises(data.CBDataError, match="增量成交量"):
        data.load_cb_dataset(config())
