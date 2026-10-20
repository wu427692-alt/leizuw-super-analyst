from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess

import pytest

from data_provider import jev_live_context as context

NOW = datetime(2026, 9, 21, 5, 1, tzinfo=timezone.utc)


def membership(count=5):
    return {"members": [{"code": "60000" + str(i), "symbol": "sh60000" + str(i)} for i in range(count)],
            "member_count": count, "available_at": (NOW - timedelta(seconds=20)).isoformat(),
            "source": "eastmoney.BK1128.constituents"}


def quote(price=101, age=2):
    return {"source_time": (NOW - timedelta(seconds=age)).isoformat(), "last_price": price,
            "pre_close": 100, "trading_status": "trading", "bid_price": 100.9, "ask_price": 101.1}


def batch(count=5):
    return {"observed_at": (NOW - timedelta(seconds=1)).isoformat(),
            "quotes": {"sh60000" + str(i): quote() for i in range(count)}}


def news_db(tmp_path):
    path = tmp_path / "news.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
          CREATE TABLE monitoring_events(source_key TEXT,source_name TEXT,event_type TEXT,title TEXT,
            symbol_codes TEXT,event_at TEXT,ingested_at TEXT,updated_at TEXT);
          CREATE TABLE monitoring_sources(source_key TEXT,enabled INT,last_success_at TEXT);
        """)
    return path


def add_news(path, title="CPO optical news", source="tushare.news.cls", category="news", age=30,
             ingested_age=10, updated_age=10):
    def stamp(seconds):
        return (NOW - timedelta(seconds=seconds)).replace(tzinfo=None).isoformat(" ")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO monitoring_events VALUES (?,?,?,?,?,?,?,?)",
                   (source, "财联社", category, title, "[]", stamp(age), stamp(ingested_age), stamp(updated_age)))
        db.execute("INSERT INTO monitoring_sources VALUES (?,?,?)", (source, 1, stamp(10)))


def test_membership_requires_all_rows_and_retains_unsupported_denominator():
    payload = {"rc": 0, "data": {"total": 2, "diff": {
        "0": {"f12": "600001", "f13": 1}, "1": {"f12": "920045", "f13": 0}}}}
    got = context.parse_cpo_members(payload, NOW.isoformat())
    assert got["member_count"] == 2
    assert got["members"][1]["symbol"] is None
    payload["data"]["total"] = 3
    with pytest.raises(ValueError, match="incomplete"):
        context.parse_cpo_members(payload, NOW.isoformat())


def test_membership_rejects_duplicate_vendor_rows():
    payload = {"rc": 0, "data": {"total": 2, "diff": [
        {"f12": "600001", "f13": 1}, {"f12": "600001", "f13": 1}]}}
    with pytest.raises(ValueError, match="member"):
        context.parse_cpo_members(payload, NOW.isoformat())


def test_proxy_equal_weights_and_truthful_full_membership_coverage():
    data = batch(4)
    data["quotes"]["sh600001"] = quote(99)
    got, valid = context.build_cpo_proxy(membership(5), data, NOW)
    assert got["status"] == "fresh"
    assert got["metrics"]["coverage_ratio"] == .8
    assert got["metrics"]["member_count"] == 5
    assert got["metrics"]["breadth_up"] == .75
    assert got["metrics"]["return_since_prev_close"] == pytest.approx(.005)
    assert len(valid) == 4
    assert "price" not in got["metrics"]


@pytest.mark.parametrize("change", ["old", "future", "halted", "outside_membership"])
def test_proxy_drops_invalid_member_observations_without_fill(change):
    data = batch(4)
    if change == "old":
        data["quotes"]["sh600000"] = quote(age=16)
    elif change == "future":
        data["quotes"]["sh600000"] = quote(age=-1)
    elif change == "halted":
        data["quotes"]["sh600000"]["trading_status"] = "halted"
    else:
        data["quotes"]["sh999999"] = data["quotes"].pop("sh600000")
    got, _ = context.build_cpo_proxy(membership(5), data, NOW)
    assert got["status"] == "stale"
    assert got["metrics"]["coverage_ratio"] == .6


def test_delayed_board_never_marked_realtime_even_with_current_clock():
    payload = {"rc": 0, "data": {"f57": "BK1128", "f58": "CPO概念", "f59": 2,
                                "f43": 100001, "f60": 100000, "f86": NOW.timestamp()}}
    got = context.parse_cpo_board(payload, NOW.isoformat(), delayed=True)
    assert got["status"] == "stale"
    assert got["reason"] == "delayed_source_latency_unverified"


def test_index_identity_and_time_cannot_be_substituted():
    data = {**quote(), "symbol": "sh000300", "name": "沪深300",
            "received_at": (NOW - timedelta(seconds=1)).isoformat()}
    assert context.parse_index_context(data, "hs300", NOW)["status"] == "fresh"
    assert context.parse_index_context(data, "zz1000", NOW)["status"] == "error"


def test_news_only_public_allowlist_and_read_time_availability(tmp_path):
    path = news_db(tmp_path)
    add_news(path, title="CPO\n芯片\x00新闻")
    add_news(path, title="CPO private post", source="zsxq.private")
    add_news(path, title="CPO private essay", category="essay")
    got = context.read_public_news(path, NOW)
    assert got["status"] == "fresh"
    assert len(got["items"]) == 1
    assert got["items"][0]["title"] == "CPO 芯片 新闻"
    assert got["items"][0]["available_at"] == NOW.isoformat()
    assert got["items"][0]["first_observed_at"] != NOW.isoformat()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM monitoring_events").fetchone()[0] == 3


@pytest.mark.parametrize("field", ["published", "ingested", "updated"])
def test_news_future_knowledge_excluded(tmp_path, field):
    path = news_db(tmp_path)
    kwargs = {"age": 30, "ingested_age": 10, "updated_age": 10}
    kwargs[{"published": "age", "ingested": "ingested_age", "updated": "updated_age"}[field]] = -1
    add_news(path, **kwargs)
    got = context.read_public_news(path, NOW)
    assert got["items"] == []


def test_fresh_database_read_does_not_refresh_stale_ingestion(tmp_path):
    path = news_db(tmp_path)
    add_news(path, age=600, ingested_age=500, updated_age=500)
    got = context.read_public_news(path, NOW)
    assert got["status"] == "stale"
    assert got["age_ms"] == 500000


def test_news_is_bounded_deduped_and_keeps_macro_room(tmp_path):
    path = news_db(tmp_path)
    for i in range(12):
        add_news(path, title="CPO headline " + str(i), age=30 + i)
    add_news(path, title="CPO headline 0", age=60)
    add_news(path, title="央行今日消息", age=100)
    add_news(path, title="华懋科技公告", age=100)
    got = context.read_public_news(path, NOW)
    assert len(got["items"]) == 8
    assert {i["category"] for i in got["items"]} == {"cpo", "macro", "company"}
    assert len({i["title"] for i in got["items"]}) == 8


def test_wire_discards_stale_values_and_age_uses_current_time(tmp_path):
    path = news_db(tmp_path)
    add_news(path)
    cpo, _ = context.build_cpo_proxy(membership(), batch(), NOW)
    news = context.read_public_news(path, NOW)
    got = context.wire_context(cpo, news, NOW + timedelta(seconds=16))
    assert got["context"]["cpo"]["status"] == "stale"
    assert got["context"]["cpo"]["values"] == {}
    assert got["context"]["news"]["items"][0]["summary"] == ""
    assert got["context"]["news"]["items"][0]["url"] is None


def test_worker_hard_timeout_does_not_leak_exception(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 6
        raise subprocess.TimeoutExpired("private irrelevant command", 6)
    monkeypatch.setattr(context.subprocess, "run", timeout)
    feed = context.LiveExternalContext(tmp_path / "missing.db")
    got = feed.poll()
    assert got["context"]["cpo"]["status"] == "error"
    assert "private" not in json.dumps(got)


def test_news_missing_db_never_created(tmp_path):
    path = tmp_path / "not-created.db"
    assert context.read_public_news(path, NOW)["status"] == "missing"
    assert not path.exists()


def test_cpo_wire_never_labels_sparse_observations_as_exact_30_or_60_seconds(tmp_path):
    cpo, _ = context.build_cpo_proxy(membership(), batch(), NOW)
    cpo["metrics"].update(return_30s=.02, return_60s=.03)
    news = context.read_public_news(tmp_path / "missing.db", NOW)
    values = context.wire_context(cpo, news, NOW)["context"]["cpo"]["values"]
    assert "return_30s" not in values
    assert "return_60s" not in values
    assert values["return_since_prev_close"] == pytest.approx(.01)


def test_cpo_refresh_uses_request_start_not_request_completion(monkeypatch, tmp_path):
    clock, calls = [0.0], []
    monkeypatch.setattr(context.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(context, "_now", lambda: NOW)

    def worker(action, payload=None):
        calls.append(action)
        clock[0] += 1.0
        return membership() if action == "members" else batch()

    monkeypatch.setattr(context, "_bounded_worker", worker)
    feed = context.LiveExternalContext(tmp_path / "missing.db")
    feed.poll()
    assert clock[0] == 2.0
    clock[0] = 4.9
    feed.poll()
    assert calls.count("quotes") == 1
    clock[0] = 5.0
    feed.poll()
    assert calls.count("quotes") == 2
    assert calls.count("members") == 1


def test_one_second_poll_refreshes_cpo_every_five_seconds_but_news_only_once_per_minute(monkeypatch, tmp_path):
    clock, network, reads = [0.], [], []
    monkeypatch.setattr(context.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(context, "_now", lambda: NOW + timedelta(seconds=clock[0]))
    def worker(action, payload=None):
        network.append((action, clock[0]))
        if action == "members":
            return membership()
        value = batch()
        value["observed_at"] = context._now().isoformat()
        for item in value["quotes"].values():
            item["source_time"] = (context._now() - timedelta(seconds=2)).isoformat()
        return value
    original_read = context.read_public_news
    def read(path):
        reads.append(clock[0])
        return original_read(path, context._now())
    monkeypatch.setattr(context, "_bounded_worker", worker)
    monkeypatch.setattr(context, "read_public_news", read)
    feed = context.LiveExternalContext(tmp_path / "missing.db")
    for second in range(61):
        clock[0] = float(second)
        result = feed.poll()
        assert result["context"]["cpo"]["status"] == "fresh"
        assert result["context"]["cpo"]["age_seconds"] <= 6
        assert result["diagnostics"]["cpo"]["refresh_interval_ms"] == 5000
        assert result["diagnostics"]["cpo"]["max_age_ms"] == 15000
    assert [at for action, at in network if action == "quotes"] == list(range(0, 61, 5))
    assert sum(action == "members" for action, _ in network) == 1
    assert reads == [0, 60]


def test_failed_membership_is_still_throttled_during_one_second_poll(monkeypatch, tmp_path):
    clock, calls = [0.], []
    monkeypatch.setattr(context.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(context, "_now", lambda: NOW)
    def fail(action, payload=None):
        calls.append((action, clock[0]))
        raise ValueError("source unavailable")
    monkeypatch.setattr(context, "_bounded_worker", fail)
    feed = context.LiveExternalContext(tmp_path / "missing.db")
    for second in range(7):
        clock[0] = float(second)
        result = feed.poll()
        assert result["context"]["cpo"]["status"] == "error"
    assert calls == [("members", 0), ("members", 5)]


def test_continuous_cpo_membership_refreshes_on_new_shanghai_date(monkeypatch, tmp_path):
    day, mono, calls = [NOW], [0.], []
    monkeypatch.setattr(context, '_now', lambda: day[0])
    monkeypatch.setattr(context.time, 'monotonic', lambda: mono[0])
    def worker(action, payload=None):
        calls.append(action)
        return membership() if action == 'members' else batch()
    monkeypatch.setattr(context, '_bounded_worker', worker)
    feed = context.LiveExternalContext(tmp_path / 'missing.db')
    feed.poll()
    mono[0] = 16
    feed.poll()
    assert calls.count('members') == 1
    day[0] += timedelta(days=1)
    mono[0] = 17
    feed.poll()
    assert calls.count('members') == 2 and calls.count('quotes') == 3
