from datetime import date, datetime, timedelta
import os
from types import SimpleNamespace

import pytest

from src.config import Config
from src.repositories.market_data_repo import MarketDataRepository
from src.services.market_data_service import (
    MarketDataService,
    _legacy_snapshot_timestamp,
    _latest_a_share_close_timestamp,
    _latest_completed_a_share_session,
    _parse_tencent_day_minutes,
    _tick_needs_refresh,
    _tencent_realtime_stock_snapshots,
)
from src.storage import DatabaseManager, StockDaily


class _NoNetworkFetcher:
    def get_stock_name(self, code, allow_realtime=False):
        return {"603306": "华懋科技"}.get(code)

    def get_daily_data(self, code, days):
        return None, None

    def get_realtime_quote(self, code, log_final_failure=False):
        return None


class _MinuteGateway:
    available = True

    def query(self, api_name, *, params):
        if api_name == "stk_mins":
            return {"rows": [{
                "ts_code": "603306.SH", "trade_time": "2026-08-20 10:45:00",
                "open": 74.80, "high": 74.90, "low": 74.75, "close": 74.84,
                "vol": 1200, "amount": 89808,
            }]}
        assert api_name == "rt_min"
        assert params["ts_code"] == "603306.SH"
        return {"rows": [{
            "ts_code": "603306.SH", "time": "2026-08-20 10:45:00",
            "open": 74.80, "high": 74.90, "low": 74.75, "close": 74.84,
            "vol": 1200, "amount": 89808,
        }]}


def _tick_batch(codes):
    assert codes == ["603306"]
    return [{
        "code": "603306", "timestamp": datetime(2026, 8, 20, 10, 45, 1),
        "price": 74.84, "open": 71.98, "high": 75.16, "low": 70.66,
        "pre_close": 70.11, "volume": 120000, "amount": 8980800,
        "change": 4.73, "change_percent": 6.75,
    }]


@pytest.fixture()
def market_db(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "market-data.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def test_daily_rows_are_aggregated_locally_to_week_month_and_year(market_db, monkeypatch):
    with market_db.get_session() as session:
        for offset in range(40):
            day = date(2025, 12, 20) + timedelta(days=offset)
            close = 10 + offset / 10
            session.add(StockDaily(
                code="603306", date=day, open=close - .1, high=close + .2, low=close - .2,
                close=close, volume=1000 + offset, amount=10000 + offset, data_source="test.daily",
            ))
        session.commit()
    monkeypatch.setattr("src.services.market_data_service.date", type("FixedDate", (date,), {"today": classmethod(lambda cls: date(2026, 1, 31))}))
    service = MarketDataService(repository=MarketDataRepository(market_db), fetcher=_NoNetworkFetcher(), tushare=_MinuteGateway())

    weekly = service.get_series("603306", period="weekly", range_key="3m")
    monthly = service.get_series("603306", period="monthly", range_key="1y")
    yearly = service.get_series("603306", period="yearly", range_key="5y")

    assert weekly["storage"] == "sqlite"
    assert len(weekly["data"]) >= 5
    assert len(monthly["data"]) == 2
    assert monthly["data"][-1]["date"] == "2026-01-28"
    assert len(yearly["data"]) == 2
    assert monthly["source"] == "test.daily"


def test_realtime_tick_is_upserted_and_returned_from_sqlite(market_db, monkeypatch):
    with market_db.get_session() as session:
        session.add(StockDaily(code="603306", date=date(2026, 8, 19), close=70.11, data_source="test.daily"))
        session.commit()
    monkeypatch.setattr("src.services.market_data_service.datetime", type("FixedDateTime", (datetime,), {"now": classmethod(lambda cls: datetime(2026, 8, 20, 10, 46))}))
    service = MarketDataService(
        repository=MarketDataRepository(market_db), fetcher=_NoNetworkFetcher(),
        tushare=_MinuteGateway(), realtime_batch_fetcher=_tick_batch,
    )

    result = service.get_series("603306", period="intraday", range_key="1d", refresh=True)

    assert result["stored_count"] == 1
    assert result["source"] == "tushare.legacy_snapshot"
    assert result["data"][0]["date"] == "2026-08-20T10:45:01"
    assert result["data"][0]["close"] == 74.84
    assert result["pre_close"] == 70.11
    assert service.status()["tick_rows"] == 1


def test_legacy_snapshot_uses_exchange_date_and_time_instead_of_poll_time():
    fallback = datetime(2026, 8, 22, 0, 23, 57)
    parsed = _legacy_snapshot_timestamp(
        {"date": "2026-08-21", "time": "15:00:03"}, fallback,
    )

    assert parsed == datetime(2026, 8, 21, 15, 0, 3)


def test_latest_completed_a_share_session_handles_after_close_and_weekend():
    assert _latest_completed_a_share_session(datetime(2026, 8, 21, 14, 0)) == date(2026, 8, 20)
    assert _latest_completed_a_share_session(datetime(2026, 8, 21, 16, 0)) == date(2026, 8, 21)
    assert _latest_completed_a_share_session(datetime(2026, 8, 22, 10, 0)) == date(2026, 8, 21)


def test_missing_legacy_exchange_time_falls_back_to_latest_session_close():
    assert _latest_a_share_close_timestamp(datetime(2026, 8, 22, 0, 23, 57)) == datetime(2026, 8, 21, 15, 0)


def test_post_close_refresh_retries_an_incomplete_same_day_snapshot():
    partial = SimpleNamespace(timestamp=datetime(2026, 8, 24, 14, 53, 27))
    completed = SimpleNamespace(timestamp=datetime(2026, 8, 24, 15, 0, 0))
    now = datetime(2026, 8, 24, 15, 12, 50)

    assert _tick_needs_refresh(partial, now) is True
    assert _tick_needs_refresh(completed, now) is False


def test_latest_quotes_prefers_completed_daily_close_over_partial_same_day_tick(market_db, monkeypatch):
    repo = MarketDataRepository(market_db)
    repo.upsert_ticks([{
        "code": "603306", "timestamp": datetime(2026, 8, 21, 11, 29, 58),
        "price": 73.8, "pre_close": 74.28,
    }], source="test.tick")
    with market_db.get_session() as session:
        session.add_all([
            StockDaily(code="603306", date=date(2026, 8, 20), close=74.28, data_source="test.daily"),
            StockDaily(
                code="603306", date=date(2026, 8, 21), open=74.2, high=75.65, low=72.24,
                close=74.26, volume=105087.53, amount=781333567, data_source="test.daily",
            ),
        ])
        session.commit()
    monkeypatch.setattr(
        "src.services.market_data_service.datetime",
        type("FixedDateTime", (datetime,), {"now": classmethod(lambda cls: datetime(2026, 8, 22, 10, 0))}),
    )

    quote = MarketDataService(
        repository=repo, fetcher=_NoNetworkFetcher(), tushare=_UnavailableGateway(),
    ).latest_quotes(["603306"])[0]

    assert quote["current_price"] == 74.26
    assert quote["prev_close"] == 74.28
    assert quote["update_time"] == "2026-08-21T15:00:00"
    assert quote["source"] == "test.daily"


def test_latest_quotes_replaces_present_but_stale_tick_with_fallback_snapshot(market_db, monkeypatch):
    repo = MarketDataRepository(market_db)
    repo.upsert_ticks([{
        "code": "300308", "timestamp": datetime(2026, 8, 21, 15, 0),
        "price": 943.0, "pre_close": 904.2, "change_percent": 4.29,
    }], source="tushare.daily:cross_section")

    class RealtimeFetcher(_NoNetworkFetcher):
        def get_realtime_quote(self, code, log_final_failure=False):
            assert code == "300308"
            return SimpleNamespace(
                price=851.31, change_pct=-9.72, change_amount=-91.69,
                open_price=945.0, high=949.73, low=850.0, pre_close=943.0,
                volume=32_059_700, amount=28_571_083_346,
                provider_timestamp="2026-08-24T13:51:33+08:00",
                source=SimpleNamespace(value="tencent"),
            )

    monkeypatch.setattr(
        "src.services.market_data_service.datetime",
        type("FixedDateTime", (datetime,), {
            "now": classmethod(lambda cls: datetime(2026, 8, 24, 13, 51, 35)),
            "strptime": datetime.strptime,
            "fromisoformat": datetime.fromisoformat,
            "combine": datetime.combine,
        }),
    )
    service = MarketDataService(
        repository=repo, fetcher=RealtimeFetcher(), tushare=_UnavailableGateway(),
        realtime_batch_fetcher=lambda _codes: [],
    )

    quote = service.latest_quotes(["300308"], refresh_missing=True)[0]

    assert quote["current_price"] == 851.31
    assert quote["change_percent"] == -9.72
    assert quote["update_time"] == "2026-08-24T13:51:33"
    assert quote["source"] == "tencent.snapshot"
    assert quote["is_stale"] is False


def test_intraday_series_ignores_impossible_overnight_a_share_tick(market_db):
    repo = MarketDataRepository(market_db)
    repo.upsert_ticks([{
        "code": "603306", "timestamp": datetime(2026, 8, 22, 0, 23, 57),
        "price": 74.26, "pre_close": 74.28,
    }], source="tushare.legacy_snapshot")
    repo.upsert_intraday("603306", [{
        "timestamp": datetime(2026, 8, 21, 15, 0),
        "open": 74.0, "high": 76.0, "low": 72.0, "close": 74.28,
    }], source="tushare.rt_min")
    service = MarketDataService(
        repository=repo, fetcher=_NoNetworkFetcher(), tushare=_UnavailableGateway(),
        realtime_batch_fetcher=lambda _codes: [], minute_history_fetcher=lambda _symbol: [],
    )

    result = service.get_series("603306", period="intraday", range_key="1d")

    assert result["latest_at"] == "2026-08-21T15:00"
    assert result["data"][-1]["close"] == 74.28


def test_intraday_zero_axis_uses_displayed_sessions_prior_close_after_close_is_saved(market_db, monkeypatch):
    with market_db.get_session() as session:
        session.add_all([
            StockDaily(code="603306", date=date(2026, 8, 19), close=70.11, data_source="test.daily"),
            StockDaily(code="603306", date=date(2026, 8, 20), close=74.28, data_source="test.daily"),
        ])
        session.commit()
    repo = MarketDataRepository(market_db)
    repo.upsert_intraday("603306", [{
        "timestamp": datetime(2026, 8, 20, 15, 0),
        "open": 74.20, "high": 74.30, "low": 74.18, "close": 74.28,
        "volume": 1000, "amount": 74280,
    }], source="test.minute")
    monkeypatch.setattr("src.services.market_data_service.date", type(
        "FixedDate", (date,), {"today": classmethod(lambda cls: date(2026, 8, 21))},
    ))
    monkeypatch.setattr("src.services.market_data_service.datetime", type(
        "FixedDateTime", (datetime,), {"now": classmethod(lambda cls: datetime(2026, 8, 21, 8, 0))},
    ))
    service = MarketDataService(
        repository=repo, fetcher=_NoNetworkFetcher(), tushare=_UnavailableGateway(),
        realtime_batch_fetcher=lambda _codes: [], minute_history_fetcher=lambda _symbol: [],
    )

    result = service.get_series("603306", period="intraday", range_key="1d")

    assert result["latest_at"] == "2026-08-20T15:00"
    assert result["data"][-1]["close"] == 74.28
    assert result["pre_close"] == 70.11


def test_second_volume_is_derived_from_cumulative_exchange_totals(market_db, monkeypatch):
    monkeypatch.setattr("src.services.market_data_service.datetime", type(
        "FixedDateTime", (datetime,), {"now": classmethod(lambda cls: datetime(2026, 8, 20, 10, 46))},
    ))
    repo = MarketDataRepository(market_db)
    repo.upsert_ticks([{
        "code": "603306", "timestamp": datetime(2026, 8, 20, 10, 45, 1),
        "price": 74.80, "volume": 120000, "amount": 8_980_800,
    }], source="test.1sec")
    repo.upsert_ticks([{
        "code": "603306", "timestamp": datetime(2026, 8, 20, 10, 45, 2),
        "price": 74.84, "volume": 120600, "amount": 9_025_704,
    }], source="test.1sec")
    service = MarketDataService(
        repository=repo, fetcher=_NoNetworkFetcher(), tushare=_UnavailableGateway(),
        realtime_batch_fetcher=lambda _codes: [], minute_history_fetcher=lambda _symbol: [],
    )

    result = service.get_series("603306", period="intraday", range_key="1d")
    quote = service.latest_quotes(["603306"])[0]

    assert result["stored_count"] == 1
    assert result["data"][0]["date"] == "2026-08-20T10:45:02"
    assert result["data"][0]["volume"] == 600
    assert result["data"][0]["amount"] == 44_904
    assert result["data"][0]["cumulative_volume"] == 120600
    assert quote["volume"] == 120600
    assert quote["second_volume"] == 600
    assert quote["second_amount"] == 44_904


def test_tencent_minute_cumulative_totals_become_interval_deltas() -> None:
    rows = _parse_tencent_day_minutes("20260820", [
        "0930 100.00 1200 120000",
        "0931 100.20 1950 195150",
        "0932 100.10 1950 195150",
    ])

    assert [row["volume"] for row in rows] == [1200, 750, 0]
    assert [row["amount"] for row in rows] == [120000, 75150, 0]
    assert rows[1]["close"] == 100.20


def test_tencent_batch_snapshot_keeps_exchange_time_price_and_volume(monkeypatch) -> None:
    fields = [""] * 50
    fields[1] = "中际旭创"; fields[2] = "300308"; fields[3] = "851.31"
    fields[4] = "943.00"; fields[5] = "945.00"; fields[6] = "320597"
    fields[30] = "20260824135133"; fields[31] = "-91.69"; fields[32] = "-9.72"
    fields[33] = "949.73"; fields[34] = "850.00"
    fields[35] = "851.31/320597/28571083346"; fields[36] = "320597"; fields[37] = "2857108.3346"
    monkeypatch.setattr(
        "src.services.market_data_service._tencent_quote_payload",
        lambda _symbols: {"sz300308": fields},
    )

    row = _tencent_realtime_stock_snapshots(["300308"])[0]

    assert row["timestamp"] == datetime(2026, 8, 24, 13, 51, 33)
    assert row["price"] == 851.31
    assert row["change_percent"] == -9.72
    assert row["volume"] == 32_059_700
    assert row["amount"] == 28_571_083_346
    assert row["source"] == "tencent.snapshot"


def test_index_second_volume_uses_same_delta_contract(market_db):
    repo = MarketDataRepository(market_db)
    repo.upsert_index("000001.SH", [{
        "timestamp": datetime(2026, 8, 20, 10, 0, 1), "close": 3800,
        "volume": 1_000_000, "amount": 2_000_000,
    }], frequency="1SEC", source="test.index")
    repo.upsert_index("000001.SH", [{
        "timestamp": datetime(2026, 8, 20, 10, 0, 2), "close": 3801,
        "volume": 1_002_500, "amount": 2_010_000,
    }], frequency="1SEC", source="test.index")

    rows = repo.index_range(
        "000001.SH", datetime(2026, 8, 20, 10), datetime(2026, 8, 20, 11), frequency="1SEC",
    )

    assert rows[0].volume_delta is None
    assert rows[1].volume_delta == 2500
    assert rows[1].amount_delta == 10_000


class _IndexGateway:
    available = True

    def query(self, api_name, *, params):
        assert api_name == "index_daily"
        assert params["ts_code"] == "000001.SH"
        return {"rows": [
            {"trade_date": "20260818", "open": 3700, "high": 3720, "low": 3690, "close": 3710, "vol": 10, "amount": 20, "pct_chg": .3},
            {"trade_date": "20260819", "open": 3710, "high": 3730, "low": 3700, "close": 3725, "vol": 11, "amount": 21, "pct_chg": .4},
        ]}


def test_index_uses_exchange_qualified_separate_storage(market_db, monkeypatch):
    monkeypatch.setattr("src.services.market_data_service.date", type("FixedDate", (date,), {"today": classmethod(lambda cls: date(2026, 8, 20))}))
    monkeypatch.setattr("src.services.market_data_service.datetime", type("FixedDateTime", (datetime,), {"now": classmethod(lambda cls: datetime(2026, 8, 20, 12, 0)), "strptime": datetime.strptime}))
    service = MarketDataService(repository=MarketDataRepository(market_db), fetcher=_NoNetworkFetcher(), tushare=_IndexGateway())

    result = service.get_index_series("000001.SH", period="daily", range_key="1m", refresh=True)

    assert result["stock_code"] == "000001.SH"
    assert result["stock_name"] == "上证指数"
    assert result["source"] == "tushare.index_daily"
    assert result["stored_count"] == 2
    assert service.status()["index_rows"] == 2


class _UnavailableGateway:
    available = False


def test_five_day_intraday_keeps_minute_history_and_only_latest_minute_live(market_db):
    minute_rows = []
    for day_offset in range(6):
        timestamp = datetime(2026, 8, 13 + day_offset, 10, 0)
        minute_rows.append({
            "timestamp": timestamp,
            "open": 70 + day_offset,
            "high": 70 + day_offset,
            "low": 70 + day_offset,
            "close": 70 + day_offset,
            "volume": 1000,
            "amount": 70000,
        })
    minute_rows.append({
        "timestamp": datetime(2026, 8, 18, 10, 30),
        "open": 76.05,
        "high": 76.10,
        "low": 76.00,
        "close": 76.08,
        "volume": 800,
        "amount": 60864,
    })
    service = MarketDataService(
        repository=MarketDataRepository(market_db),
        fetcher=_NoNetworkFetcher(),
        tushare=_UnavailableGateway(),
        realtime_batch_fetcher=lambda codes: [],
        minute_history_fetcher=lambda symbol: list(minute_rows),
    )
    service.repo.upsert_ticks([{
        "code": "603306",
        "timestamp": datetime(2026, 8, 18, 10, 31, 1),
        "price": 76.1,
        "open": 76,
        "high": 76.2,
        "low": 75.9,
        "volume": 120000,
        "amount": 9_132_000,
    }], source="test.1sec")
    service.repo.upsert_ticks([{
        "code": "603306",
        "timestamp": datetime(2026, 8, 18, 10, 31, 2),
        "price": 76.2,
        "open": 76,
        "high": 76.2,
        "low": 75.9,
        "volume": 120500,
        "amount": 9_170_100,
    }], source="test.1sec")

    result = service.get_series("603306", period="intraday", range_key="5d", refresh=True)

    dates = {item["date"][:10] for item in result["data"]}
    assert dates == {"2026-08-14", "2026-08-15", "2026-08-16", "2026-08-17", "2026-08-18"}
    assert any(item["date"] == "2026-08-18T10:30" for item in result["data"])
    assert result["data"][-1]["date"] == "2026-08-18T10:31:02"
    assert result["data"][-1]["volume"] == 500
    assert not any(item["date"] == "2026-08-18T10:31:01" for item in result["data"])
    assert result["source"] == "tencent.5day_minute+test.1sec"
    assert service.status()["tick_symbols"] == 1
    assert service.status()["minute_symbols"] == 1


def test_index_five_day_intraday_prefers_local_seconds_and_keeps_minute_fallback(market_db):
    minute_rows = [{
        "timestamp": datetime(2026, 8, 14 + offset, 10, 0),
        "open": 3800 + offset,
        "high": 3800 + offset,
        "low": 3800 + offset,
        "close": 3800 + offset,
        "volume": 100,
        "amount": 1000,
    } for offset in range(5)]
    service = MarketDataService(
        repository=MarketDataRepository(market_db),
        fetcher=_NoNetworkFetcher(),
        tushare=_UnavailableGateway(),
        minute_history_fetcher=lambda symbol: list(minute_rows),
    )
    service.refresh_historical_index_intraday(["000001.SH"], sessions=5)
    service.repo.upsert_index("000001.SH", [{
        "timestamp": datetime(2026, 8, 18, 10, 0, 1),
        "open": 3810,
        "high": 3810,
        "low": 3810,
        "close": 3810,
        "volume": 200,
        "amount": 2000,
    }], frequency="1SEC", source="test.index.1sec")

    result = service.get_index_series("000001.SH", period="intraday", range_key="5d")

    assert result["data"][-1]["date"] == "2026-08-18T10:00:01"
    assert result["source"] == "tencent.5day_minute+test.index.1sec"
    assert service.status()["index_tick_symbols"] == 1


def test_index_intraday_ends_on_official_close_when_snapshot_missed_1500(market_db, monkeypatch):
    repo = MarketDataRepository(market_db)
    repo.upsert_index("000001.SH", [{
        "timestamp": datetime(2026, 8, 24), "open": 3902.70, "high": 3907.65,
        "low": 3867.47, "close": 3882.01, "change_percent": -0.59,
    }], frequency="1D", source="tushare.index_daily")
    repo.upsert_index("000001.SH", [{
        "timestamp": datetime(2026, 8, 24, 14, 53, 27), "close": 3881.20,
        "volume": 1_000_000, "change_percent": -0.61,
    }], frequency="1SEC", source="tencent.snapshot")
    monkeypatch.setattr(
        "src.services.market_data_service.datetime",
        type("FixedDateTime", (datetime,), {
            "now": classmethod(lambda cls: datetime(2026, 8, 24, 16, 0)),
            "strptime": datetime.strptime,
        }),
    )
    service = MarketDataService(
        repository=repo, fetcher=_NoNetworkFetcher(), tushare=_UnavailableGateway(),
        minute_history_fetcher=lambda _symbol: [],
    )
    assert _latest_completed_a_share_session() == date(2026, 8, 24)
    assert len(repo.index_range(
        "000001.SH", datetime(2026, 8, 24), datetime(2026, 8, 24, 23, 59, 59), frequency="1D",
    )) == 1

    result = service.get_index_series("000001.SH", period="intraday", range_key="1d")

    assert result["latest_at"] == "2026-08-24T15:00:00"
    assert result["data"][-1]["close"] == 3882.01
    assert result["data"][-1]["volume"] == 0.0
    assert "official_close" in result["source"]


def test_large_index_history_is_batched_below_sqlite_variable_limit(market_db):
    repo = MarketDataRepository(market_db)
    rows = [{
        "timestamp": datetime(2010, 1, 1) + timedelta(days=offset),
        "open": 3000 + offset,
        "high": 3001 + offset,
        "low": 2999 + offset,
        "close": 3000.5 + offset,
        "volume": 100,
        "amount": 1000,
    } for offset in range(3000)]

    saved = repo.upsert_index("000001.SH", rows, frequency="1D", source="test.index_daily")

    assert saved == 3000
    assert len(repo.index_range(
        "000001.SH", datetime(2010, 1, 1), datetime(2030, 1, 1), frequency="1D",
    )) == 3000


def test_startup_bootstrap_reuses_fresh_long_range_cache(market_db, monkeypatch):
    repo = MarketDataRepository(market_db)
    with market_db.get_session() as session:
        session.add(StockDaily(
            code="603306", date=date.today(), open=10, high=11, low=9, close=10.5,
            data_source="test.daily",
        ))
        session.commit()
    repo.upsert_index("000688.SH", [{
        "timestamp": datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
        "open": 1000, "high": 1010, "low": 990, "close": 1005,
    }], frequency="1D", source="test.index_daily")
    fetcher = _NoNetworkFetcher()
    monkeypatch.setattr(fetcher, "get_daily_data", lambda *_args, **_kwargs: pytest.fail("fresh stock cache refetched"))
    service = MarketDataService(repository=repo, fetcher=fetcher, tushare=_UnavailableGateway())
    monkeypatch.setattr(service, "refresh_historical_intraday", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(service, "refresh_historical_index_intraday", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(service, "_refresh_index", lambda *_args, **_kwargs: pytest.fail("fresh index cache refetched"))

    result = service.bootstrap_universe(
        ["603306"], index_symbols=["000688.SH"], intraday_sessions=5, daily_days=7300,
    )

    assert result["stock_daily_rows"] == 1
    assert result["index_daily_rows"] == 1
