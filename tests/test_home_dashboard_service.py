from src.services.home_dashboard_service import HomeDashboardService


class FakeTushare:
    available = True

    def __init__(self):
        self.calls = []

    def query(self, api_name, *, params=None, fields=None):
        self.calls.append((api_name, params or {}))
        code = (params or {}).get("ts_code")
        if api_name == "trade_cal":
            cal_date = (params or {}).get("start_date")
            return {"rows": [{"cal_date": cal_date, "is_open": 1}]}
        if api_name in {"index_daily", "index_global"}:
            return {"rows": [
                {"ts_code": code, "trade_date": "20260818", "close": 100, "pct_chg": -1},
                {"ts_code": code, "trade_date": "20260819", "close": 102, "pct_chg": 2,
                 "change": 2, "open": 100, "high": 103, "low": 99, "amount": 1_000_000},
            ]}
        if api_name == "daily" and (params or {}).get("trade_date"):
            trade_date = (params or {}).get("trade_date")
            return {"rows": [
                {"ts_code": "000001.SZ", "trade_date": trade_date, "pct_chg": 1.2},
                {"ts_code": "000002.SZ", "trade_date": trade_date, "pct_chg": -2.5},
                {"ts_code": "000003.SZ", "trade_date": trade_date, "pct_chg": 10.0},
            ]}
        if api_name == "daily":
            return {"rows": [{"ts_code": code, "trade_date": "20260819", "open": 41.0,
                              "high": 43.0, "low": 40.5, "close": 42.0, "pre_close": 41.58,
                              "change": 0.42, "pct_chg": 1.0, "vol": 200, "amount": 1234.5}]}
        if api_name == "moneyflow_hsgt":
            return {"rows": [{"trade_date": "20260819", "north_money": "328465.07", "south_money": "54830.58"}]}
        raise AssertionError(api_name)


class FakeMonitor:
    def dashboard(self, days=7):
        event = {"id": 1, "title": "目标公司新增订单", "symbols": ["603306.SH"],
                 "sentiment": "bullish", "perspective": "institution", "importance_score": 80}
        return {
            "watchlist": [{"symbol": "603306.SH", "name": "华懋科技", "event_count": 1,
                           "high_priority_count": 1, "opportunity_score": 70, "risk_score": 20,
                           "perspectives": {"institution": 1}, "sentiment": {"bullish": 1},
                           "latest_quote": {"current_price": 42.0, "change_percent": 1.0},
                           "institution_rating_count": 1}],
            "latest_events": [event],
            "summary": {"event_count": 1, "active_source_count": 4},
        }


def test_home_dashboard_aggregates_market_and_watchlist_with_cache(monkeypatch):
    monkeypatch.setattr(
        "src.services.market_data_service.MarketDataService.latest_quotes",
        lambda _self, _symbols, refresh_missing=True: [],
    )
    monkeypatch.setattr(
        HomeDashboardService,
        "_sina_stock_snapshot",
        staticmethod(lambda trade_date, now: {
            "available": True, "trade_date": trade_date, "updated_at": now.isoformat(),
            "source": "akshare.sina_a_spot", "reason": None,
            "up": 2, "down": 1, "flat": 0, "limit_up": 1, "limit_down": 0,
            "total": 3, "distribution": HomeDashboardService._change_distribution([1.2, -2.5, 10.0]),
        }),
    )
    monkeypatch.setattr(
        HomeDashboardService,
        "_sina_sector_snapshot",
        staticmethod(lambda trade_date, now: {
            "available": True, "trade_date": trade_date, "updated_at": now.isoformat(),
            "source": "akshare.sina_sector_spot", "reason": None,
            "up": 2, "down": 1, "flat": 0, "total": 3,
            "distribution": HomeDashboardService._sector_change_distribution([2.4, 0.5, -1.2]),
            "leaders": [{"name": "电子", "change_pct": 2.4}],
            "laggards": [{"name": "煤炭", "change_pct": -1.2}],
        }),
    )
    HomeDashboardService._cache_payload = None
    HomeDashboardService._cache_at = 0
    tushare = FakeTushare()
    service = HomeDashboardService(tushare=tushare, monitor=FakeMonitor(), cache_seconds=300)

    first = service.dashboard()
    call_count = len(tushare.calls)
    second = service.dashboard()

    assert len(first["cn_indices"]) == 8
    assert len(first["global_indices"]) == 6
    assert first["cn_indices"][0]["amount_yi"] == 10.0
    assert first["cn_indices"][0]["source"] == "tushare.index_daily"
    assert first["breadth"]["up"] == 2
    assert first["breadth"]["down"] == 1
    assert first["breadth"]["limit_up"] == 1
    assert first["breadth"]["available"] is True
    assert first["breadth"]["source"] == "akshare.sina_a_spot"
    assert first["sector_distribution"]["available"] is True
    assert first["sector_distribution"]["leaders"][0]["name"] == "电子"
    assert first["northbound"]["north_money_yi"] == 32.85
    assert first["watchlist"][0]["latest_catalyst"]["title"] == "目标公司新增订单"
    assert first["watchlist"][0]["latest_institution"]["title"] == "目标公司新增订单"
    assert first["watchlist"][0]["latest_quote"] == {
        "current_price": 42.0, "change": 0.42, "change_percent": 1.0,
        "open": 41.0, "high": 43.0, "low": 40.5, "prev_close": 41.58,
        "volume": 200, "amount": 1_234_500.0, "update_time": "20260819",
        "source": "tushare.daily",
    }
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert len(tushare.calls) == call_count


def test_cold_start_returns_local_snapshot_before_remote_refresh(tmp_path, monkeypatch):
    HomeDashboardService._cache_payload = None
    HomeDashboardService._cache_at = 0
    HomeDashboardService._refresh_thread = None
    service = HomeDashboardService(
        tushare=FakeTushare(), monitor=FakeMonitor(), background_refresh=True,
    )
    monkeypatch.setattr(service, "_cache_path", lambda: tmp_path / "missing-home-cache.json")
    monkeypatch.setattr(service, "_local_snapshot", lambda: {
        "generated_at": "2026-08-20T14:00:00+08:00",
        "cn_indices": [{"code": "000001.SH", "close": 3888}],
        "global_indices": [], "watchlist": [], "latest_events": [], "warnings": [],
    })
    scheduled = []
    monkeypatch.setattr(service, "_schedule_refresh", lambda: scheduled.append(True))

    result = service.dashboard()

    assert result["cn_indices"][0]["close"] == 3888
    assert result["cache"]["local_snapshot"] is True
    assert result["cache"]["refreshing"] is True
    assert scheduled == [True]


def test_current_day_breadth_rejects_previous_session_rows():
    class StaleTushare(FakeTushare):
        def query(self, api_name, *, params=None, fields=None):
            if api_name == "daily" and (params or {}).get("trade_date"):
                return {"rows": [{"ts_code": "000001.SZ", "trade_date": "20260819", "pct_chg": 3.2}]}
            return super().query(api_name, params=params, fields=fields)

    warnings = []
    result = HomeDashboardService(tushare=StaleTushare(), monitor=FakeMonitor())._breadth(
        "20260820", __import__("datetime").datetime.fromisoformat("2026-08-20T08:30:00+08:00"), False, warnings,
    )

    assert result["available"] is False
    assert result["total"] == 0
    assert result["trade_date"] == "20260820"
    assert "旧交易日" in warnings[0]
