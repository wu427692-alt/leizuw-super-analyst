from src.services.home_dashboard_service import HomeDashboardService


class FakeTushare:
    available = True

    def __init__(self):
        self.calls = []

    def query(self, api_name, *, params=None, fields=None):
        self.calls.append((api_name, params or {}))
        code = (params or {}).get("ts_code")
        if api_name in {"index_daily", "index_global"}:
            return {"rows": [
                {"ts_code": code, "trade_date": "20260818", "close": 100, "pct_chg": -1},
                {"ts_code": code, "trade_date": "20260819", "close": 102, "pct_chg": 2,
                 "change": 2, "open": 100, "high": 103, "low": 99, "amount": 1_000_000},
            ]}
        if api_name == "daily" and (params or {}).get("trade_date"):
            return {"rows": [
                {"ts_code": "000001.SZ", "pct_chg": 1.2},
                {"ts_code": "000002.SZ", "pct_chg": -2.5},
                {"ts_code": "000003.SZ", "pct_chg": 10.0},
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


def test_home_dashboard_aggregates_market_and_watchlist_with_cache():
    HomeDashboardService._cache_payload = None
    HomeDashboardService._cache_at = 0
    tushare = FakeTushare()
    service = HomeDashboardService(tushare=tushare, monitor=FakeMonitor(), cache_seconds=300)

    first = service.dashboard()
    call_count = len(tushare.calls)
    second = service.dashboard()

    assert len(first["cn_indices"]) == 4
    assert len(first["global_indices"]) == 6
    assert first["cn_indices"][0]["amount_yi"] == 10.0
    assert first["breadth"]["up"] == 2
    assert first["breadth"]["down"] == 1
    assert first["breadth"]["limit_up"] == 1
    assert first["northbound"]["north_money_yi"] == 32.85
    assert first["watchlist"][0]["latest_catalyst"]["title"] == "目标公司新增订单"
    assert first["watchlist"][0]["latest_institution"]["title"] == "目标公司新增订单"
    assert first["watchlist"][0]["latest_quote"] == {
        "current_price": 42.0, "change": 0.42, "change_percent": 1.0,
        "open": 41.0, "high": 43.0, "low": 40.5, "prev_close": 41.58,
        "volume": 200, "amount": 1_234_500.0, "update_time": "20260819",
    }
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert len(tushare.calls) == call_count
