"""Offline contract tests: no network, credentials, or synthetic provider fallback."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from src.services import jev_market_data as market


def candles(day, count=60):
    start = datetime.fromisoformat(day + "T09:30:00")
    return [{"ts_code": "600000.SH", "trade_time": (start + timedelta(minutes=i)).isoformat(" "),
             "open": 10, "high": 12, "low": 9, "close": 11, "vol": i * 100, "amount": i * 1100}
            for i in range(count)]


class Gateway:
    def __init__(self):
        self.calls = []
        self.minutes = {"2026-09-17": candles("2026-09-17"),
                        "2026-09-18": candles("2026-09-18", 3)}
        self.fail = set()
        self.aux = True

    def query(self, api, *, params, fields):
        self.calls.append((api, params, fields))
        if api in self.fail:
            raise RuntimeError("权限不足 private upstream content must not escape")
        start = date.fromisoformat(params["start_date"][:10])
        end = date.fromisoformat(params["end_date"][:10])
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if api == "trade_cal":
            rows = [{"cal_date": day.strftime("%Y%m%d"), "is_open": int(day.weekday() < 5)}
                    for day in days]
        elif api == "stk_mins":
            rows = deepcopy([row for day in days for row in self.minutes.get(day.isoformat(), [])])
        elif api == "adj_factor" and self.aux:
            rows = [{"ts_code": "600000.SH", "trade_date": day.strftime("%Y%m%d"),
                     "adj_factor": 2.5} for day in days]
        elif api == "stk_limit" and self.aux:
            rows = [{"ts_code": "600000.SH", "trade_date": day.strftime("%Y%m%d"),
                     "up_limit": 13, "down_limit": 8} for day in days]
        else:
            rows = []
        return {"rows": rows, "count": len(rows)}


@pytest.fixture
def setup_loader():
    gateway = Gateway()
    now = datetime(2026, 9, 20, 12, tzinfo=market.SHANGHAI)
    with patch.object(market, "_create_gateway", return_value=gateway), patch.object(
            market, "_now", return_value=now), patch.object(market, "_sleep"):
        yield gateway


def config(**values):
    return {"symbol": "600000", "start_date": "2026-09-18", "end_date": "2026-09-18",
            "lookback_bars": 60, "timeframe": "1min", **values}


def test_real_units_sorted_duplicates_and_auxiliary_mapping(setup_loader):
    gateway = setup_loader
    rows = gateway.minutes["2026-09-18"]
    rows.append(deepcopy(rows[0]))
    rows.reverse()
    bars, meta = market.load_minute_bars(config())
    assert len(bars) == 63
    assert [bar["date"] for bar in bars] == sorted({bar["date"] for bar in bars})
    assert bars[-3]["date"] == "2026-09-18T09:30:00+08:00"
    assert bars[-3]["volume"] == 0  # legitimate zero-volume bars are not dropped
    assert bars[-1]["volume"] == 200  # shares, no daily *100 conversion
    assert bars[-1]["close"] == 11
    assert bars[-1]["amount"] == 2200
    assert bars[-1]["adj_factor"] == 2.5
    assert bars[-1]["limit_up"] == 13 and bars[-1]["limit_down"] == 8
    assert meta["coverage"]["warmup_bars"] == 60
    assert meta["coverage"]["evaluation_bars"] == 3
    assert meta["coverage"]["duplicate_rows"] == 1
    assert meta["volume_unit"] == "shares" and meta["price_adjustment"] == "raw"
    assert not meta["warnings"]
    assert {api for api, _, _ in gateway.calls} == {"trade_cal", "stk_mins", "adj_factor", "stk_limit"}
    assert sum(api == "stk_mins" for api, _, _ in gateway.calls) == 1
    for api, params, fields in gateway.calls:
        if api == "stk_mins":
            assert params["start_date"] == "2026-09-11 00:00:00"
            assert params["end_date"] == "2026-09-18 23:59:59"
            assert params["ts_code"] == "600000.SH"
            assert params["freq"] == "1min"
            assert "vol" in fields


def test_missing_auxiliary_is_explicit_not_inferred(setup_loader):
    setup_loader.fail.update(("adj_factor", "stk_limit"))
    bars, meta = market.load_minute_bars(config())
    assert all(bar["adj_factor"] is None and bar["limit_up"] is None
               and bar["limit_down"] is None for bar in bars)
    assert any("adj_factor_missing" in warning for warning in meta["warnings"])
    assert any("limit_up_missing" in warning for warning in meta["warnings"])
    assert "private upstream" not in str(meta)


def test_partial_auxiliary_not_forward_filled(setup_loader):
    original = setup_loader.query

    def query(api, **kwargs):
        response = original(api, **kwargs)
        if api == "adj_factor":
            response["rows"] = response["rows"][:1]
            response["count"] = 1
        return response

    setup_loader.query = query
    bars, meta = market.load_minute_bars(config())
    assert bars[0]["adj_factor"] == 2.5 and bars[-1]["adj_factor"] is None
    assert any("adj_factor_missing_for_3_bars" in item for item in meta["warnings"])


def test_minutes_permission_error_never_falls_back(setup_loader):
    setup_loader.fail.add("stk_mins")
    with pytest.raises(market.MinuteDataError, match=market.ERROR_MESSAGES["minute_permission_denied"]):
        market.load_minute_bars(config())
    assert [api for api, _, _ in setup_loader.calls] == ["trade_cal", "stk_mins"]


def test_unclosed_and_future_bars_excluded(setup_loader):
    now = datetime(2026, 9, 18, 9, 31, 45, tzinfo=market.SHANGHAI)
    with patch.object(market, "_now", return_value=now):
        bars, meta = market.load_minute_bars(config())
    assert bars[-1]["date"] == "2026-09-18T09:30:00+08:00"
    assert meta["coverage"]["evaluation_bars"] == 1
    assert meta["coverage"]["unclosed_rows_excluded"] == 2


def test_timezone_normalizes_to_exchange_time(setup_loader):
    setup_loader.minutes["2026-09-18"][0]["trade_time"] = "2026-09-18T01:30:00+00:00"
    bars, _ = market.load_minute_bars(config())
    assert bars[-3]["date"] == "2026-09-18T09:30:00+08:00"


@pytest.mark.parametrize("field,value,code", [
    ("open", float("nan"), "minute_invalid_numeric_value"),
    ("vol", float("inf"), "minute_invalid_numeric_value"),
    ("vol", -1, "minute_invalid_numeric_value"),
    ("close", True, "minute_invalid_numeric_value"),
    ("high", 10, "minute_invalid_ohlc"),
    ("low", 0, "minute_invalid_numeric_value"),
    ("trade_time", "2026-09-19 09:30:00", "minute_invalid_timestamp"),
    ("trade_time", "2026-09-18 09:30:01", "minute_invalid_timestamp"),
    ("trade_time", "2026-09-18", "minute_invalid_timestamp"),
    ("ts_code", "000001.SZ", "minute_symbol_mismatch"),
])
def test_invalid_provider_rows_fail_closed(setup_loader, field, value, code):
    setup_loader.minutes["2026-09-18"][0][field] = value
    with pytest.raises(market.MinuteDataError, match=market.ERROR_MESSAGES[code]):
        market.load_minute_bars(config())


def test_conflicting_duplicate_rejected(setup_loader):
    duplicate = deepcopy(setup_loader.minutes["2026-09-18"][0])
    duplicate["close"] = 10.5
    setup_loader.minutes["2026-09-18"].append(duplicate)
    with pytest.raises(market.MinuteDataError, match=market.ERROR_MESSAGES["minute_conflicting_duplicate"]):
        market.load_minute_bars(config())


def test_range_response_rejects_rows_on_closed_calendar_days(setup_loader):
    # This Saturday lies inside the warmup range but is explicitly closed in trade_cal.
    setup_loader.minutes["2026-09-12"] = candles("2026-09-12", 1)
    with pytest.raises(market.MinuteDataError) as caught:
        market.load_minute_bars(config())
    assert caught.value.code == "minute_invalid_timestamp"
    assert sum(api == "stk_mins" for api, _, _ in setup_loader.calls) == 1


def test_seven_day_warmup_is_required(setup_loader):
    setup_loader.minutes["2026-09-17"] = candles("2026-09-17", 59)
    with pytest.raises(market.MinuteDataError, match=market.ERROR_MESSAGES["minute_insufficient_warmup_within_7_days"]):
        market.load_minute_bars(config(lookback_bars=20))
    queries = [params for api, params, _ in setup_loader.calls if api == "stk_mins"]
    assert len(queries) == 1
    assert queries[0]["start_date"] == "2026-09-11 00:00:00"


def test_larger_lookback_collects_multiple_days(setup_loader):
    setup_loader.minutes["2026-09-16"] = candles("2026-09-16", 70)
    bars, meta = market.load_minute_bars(config(lookback_bars=100))
    assert len(bars) == 103 and meta["coverage"]["warmup_bars"] == 100
    assert bars[0]["date"].startswith("2026-09-16")


@pytest.mark.parametrize("frequency", ["5min", "15min"])
def test_frequency_passed_to_provider_without_resampling(setup_loader, frequency):
    bars, meta = market.load_minute_bars(config(timeframe=frequency))
    assert len(bars) == 63 and meta["frequency"] == frequency
    assert all(params["freq"] == frequency for api, params, _ in setup_loader.calls if api == "stk_mins")


@pytest.mark.parametrize("overrides,code", [
    ({"symbol": "600000.SZ"}, "minute_invalid_symbol"),
    ({"symbol": "../600000"}, "minute_invalid_symbol"),
    ({"timeframe": "1d"}, "minute_invalid_frequency"),
    ({"start_date": "2026-09-18 00:00:00"}, "minute_invalid_date"),
    ({"start_date": "2026-08-17"}, "minute_date_range_exceeds_31_days"),
    ({"lookback_bars": True}, "minute_invalid_lookback"),
])
def test_invalid_requests_make_no_provider_calls(setup_loader, overrides, code):
    with pytest.raises(market.MinuteDataError, match=market.ERROR_MESSAGES[code]):
        market.load_minute_bars(config(**overrides))
    assert not setup_loader.calls


def test_8000_row_response_is_not_silently_accepted(setup_loader):
    setup_loader.minutes["2026-09-18"] = [setup_loader.minutes["2026-09-18"][0]] * 8000
    with pytest.raises(market.MinuteDataError, match=market.ERROR_MESSAGES["minute_possible_response_truncation"]):
        market.load_minute_bars(config())


def test_evaluation_cap_is_error_not_silent_truncation(setup_loader):
    start = date(2026, 9, 7)
    setup_loader.minutes = {"2026-09-04": candles("2026-09-04")}
    for offset in range(12):
        day = start + timedelta(days=offset)
        if day.weekday() < 5:
            setup_loader.minutes[day.isoformat()] = candles(day.isoformat(), 241)
    with pytest.raises(market.MinuteDataError, match=market.ERROR_MESSAGES["minute_evaluation_exceeds_2000_bars"]):
        market.load_minute_bars(config(start_date="2026-09-07"))


def test_missing_evaluation_day_is_recorded(setup_loader):
    setup_loader.minutes["2026-09-16"] = setup_loader.minutes.pop("2026-09-17")
    setup_loader.minutes["2026-09-16"] = candles("2026-09-16")
    _, meta = market.load_minute_bars(config(start_date="2026-09-17"))
    assert "2026-09-17" in meta["coverage"]["missing_trading_dates"]
    assert any("no_minute_rows" in item for item in meta["warnings"])


def test_no_evaluation_cannot_return_only_warmup(setup_loader):
    setup_loader.minutes["2026-09-18"] = []
    with pytest.raises(market.MinuteDataError, match=market.ERROR_MESSAGES["minute_no_closed_evaluation_bars"]):
        market.load_minute_bars(config())


def test_read_rate_limit_retries_and_then_loads(setup_loader):
    original = setup_loader.query
    attempts = 0

    def query(api, **kwargs):
        nonlocal attempts
        if api == "stk_mins":
            attempts += 1
            if attempts <= 2:
                raise RuntimeError("Tushare API HTTP 429")
        return original(api, **kwargs)

    setup_loader.query = query
    bars, meta = market.load_minute_bars(config())
    assert len(bars) == 63 and meta["coverage"]["evaluation_bars"] == 3
    assert attempts == 3  # two transient failures + one range with warmup/evaluation
    assert market._sleep.call_args_list[2].args == (2.0,)
    assert market._sleep.call_args_list[3].args == (8.0,)


def test_repeated_rate_limit_is_bounded_and_safe(setup_loader):
    calls = 0

    def query(api, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("Tushare API HTTP 429 private response body")

    setup_loader.query = query
    with pytest.raises(market.MinuteDataError) as caught:
        market.load_minute_bars(config())
    assert caught.value.code == "minute_rate_limited"
    assert "private" not in str(caught.value)
    assert calls == 4
    assert [item.args[0] for item in market._sleep.call_args_list] == [1.1, 2.0, 8.0, 30.0]


@pytest.mark.parametrize("message,code", [
    ("每天访问频次已达上限", "minute_quota_exhausted"),
    ("抱歉，您访问接口(stk_mins)频率超限(1次/小时)", "minute_quota_exhausted"),
    ("接口频率超限(20次/天)", "minute_quota_exhausted"),
    ("参数日期格式不正确", "minute_request_rejected"),
    ("权限不足", "minute_permission_denied"),
])
def test_nontransient_provider_errors_are_not_retried(setup_loader, message, code):
    calls = 0

    def query(api, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError(message)

    setup_loader.query = query
    with pytest.raises(market.MinuteDataError) as caught:
        market.load_minute_bars(config())
    assert caught.value.code == code and calls == 1
