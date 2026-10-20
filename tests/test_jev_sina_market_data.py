"""Offline Sina transport boundaries and saved real-market snapshot acceptance.

Large vendor data stays in outputs, outside source control. Snapshot-dependent
checks skip when that local acceptance artifact is absent; transport checks do not.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from src.services import jev_sina_market_data as market
from src.services.jev_market_data import MinuteDataError

# Two observed rows, used only for transport/decoder tests, not a fabricated history.
SMALL_ROWS = [
    {"day": "2026-09-15 14:53:00", "open": "9.180", "high": "9.190", "low": "9.180",
     "close": "9.190", "volume": "615500", "amount": "5653038.9688"},
    {"day": "2026-09-15 14:54:00", "open": "9.180", "high": "9.190", "low": "9.180",
     "close": "9.190", "volume": "165600", "amount": "1521371.9571"},
]


def jsonp(rows):
    return "/*<script>location.href='//sina.com';</script>*/\n=(" + json.dumps(rows) + ");"


class Response:
    def __init__(self, body, status=200, length=None):
        self.body = body.encode() if isinstance(body, str) else body
        self.status_code = status
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]


@pytest.fixture
def actual_rows():
    path = Path(__file__).resolve().parents[1] / (
        "outputs/quantdinger-evaluation-20260920/integration/alternative-minute-source.json")
    if not path.is_file():
        pytest.skip("local saved Sina acceptance snapshot is not checked into the repository")
    document = json.loads(path.read_text())
    attempt = next(item for item in document["attempts"] if item["source"] == "sina_via_akshare_endpoint")
    return market._parse_jsonp(attempt["raw_response"])


def config(**updates):
    return {"symbol": "600000.SH", "start_date": "2026-09-17", "end_date": "2026-09-18",
            "timeframe": "1min", "lookback_bars": 60, **updates}


@pytest.mark.parametrize("requested,expected", [(None, "sina"), ("sina", "sina"), ("tushare", "tushare")])
def test_public_source_selection_calls_only_selected_loader(requested, expected):
    from uuid import uuid4

    from api.v1.schemas.jev_backtest import JevBacktestRequest
    from src.services.jev_backtest_service import load_market_bars

    payload = config(request_id=uuid4())
    if requested is not None:
        payload["data_source"] = requested
    validated = JevBacktestRequest(**payload).model_dump(mode="json")
    with patch("src.services.jev_sina_market_data.load_sina_minute_bars", return_value=([], {"source": "sina"})) as sina, patch(
            "src.services.jev_market_data.load_minute_bars", return_value=([], {"source": "tushare"})) as tushare:
        assert load_market_bars(validated) == ([], {"source": expected})
    selected, other = (sina, tushare) if expected == "sina" else (tushare, sina)
    selected.assert_called_once_with(validated)
    other.assert_not_called()


@pytest.fixture(autouse=True)
def no_unmocked_network_and_fixed_clock():
    with patch.object(market.requests, "get", side_effect=AssertionError("unexpected live request")), patch.object(
            market, "_now", return_value=datetime(2026, 9, 20, 12, tzinfo=market.SHANGHAI)):
        yield


@pytest.mark.parametrize("scale", [1, 5, 15])
def test_exact_public_request_contract_and_one_request(scale):
    response = Response(jsonp(SMALL_ROWS))
    with patch.object(market.requests, "get", return_value=response) as get:
        assert market._request_rows("sh600000", scale) == SMALL_ROWS
    get.assert_called_once_with(market.SINA_URL, params={"symbol": "sh600000", "scale": str(scale),
                                                      "ma": "no", "datalen": "1970"},
                                timeout=20, stream=True, allow_redirects=False)
    assert response.closed


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_http_failures_do_not_retry_or_follow_redirects(status):
    with patch.object(market.requests, "get", return_value=Response("private error body", status)) as get:
        with pytest.raises(MinuteDataError) as error:
            market._request_rows("sh600000", 1)
    assert error.value.code == "sina_unavailable" and get.call_count == 1
    assert "private" not in str(error.value)


@pytest.mark.parametrize("exception,code", [
    (requests.Timeout("private URL"), "sina_timeout"),
    (requests.ConnectionError("private URL"), "sina_unavailable"),
])
def test_transport_errors_are_safe_and_not_retried(exception, code):
    with patch.object(market.requests, "get", side_effect=exception) as get:
        with pytest.raises(MinuteDataError) as error:
            market._request_rows("sz000001", 1)
    assert error.value.code == code and "private" not in str(error.value)
    assert get.call_count == 1


@pytest.mark.parametrize("announced", [True, False])
def test_body_limit_is_enforced_before_or_during_streaming(announced):
    response = Response(b" " * (market.MAX_RESPONSE_BYTES + 1),
                        length=market.MAX_RESPONSE_BYTES + 1 if announced else None)
    with patch.object(market.requests, "get", return_value=response):
        with pytest.raises(MinuteDataError) as error:
            market._request_rows("sh600000", 1)
    assert error.value.code == "sina_response_too_large" and response.closed


@pytest.mark.parametrize("body", [
    "callback([]);", "=[];", "=([]);", "=(null);", "=([{}]);alert('bad');",
    "<script>alert('bad')</script>=([]);", ' =([{ "day": "a", "day": "b" }]);',
    "=([NaN]);", "=([{}]);/* trailing executable content not accepted */",
])
def test_jsonp_rejects_other_wrappers_scripts_duplicates_and_nonfinite_values(body):
    with pytest.raises(MinuteDataError) as error:
        market._parse_jsonp(body)
    assert error.value.code == "sina_invalid_response"


def test_more_than_requested_snapshot_limit_rejected():
    with pytest.raises(MinuteDataError) as error:
        market._parse_jsonp(jsonp([SMALL_ROWS[0]] * 1971))
    assert error.value.code == "sina_invalid_response"


@pytest.mark.parametrize("symbol", ["https://example.invalid", "sh000001", "sz600000", "sh600000&x=1", "bj920001"])
def test_symbol_whitelist_rejects_invalid_requests_before_network(symbol):
    with pytest.raises(MinuteDataError):
        market.load_sina_minute_bars(config(symbol=symbol))
    market.requests.get.assert_not_called()


@pytest.mark.parametrize("updates", [
    {"timeframe": "1d"}, {"lookback_bars": True}, {"lookback_bars": 10000},
    {"start_date": "2026-09-17T09:30:00"}, {"start_date": "2026-07-01"},
])
def test_invalid_configuration_is_rejected_before_network(updates):
    with pytest.raises(MinuteDataError):
        market.load_sina_minute_bars(config(**updates))
    market.requests.get.assert_not_called()


def test_saved_real_snapshot_preserves_prices_raw_volume_and_gaps(actual_rows):
    with patch.object(market, "_request_rows", return_value=actual_rows) as request:
        bars, metadata = market.load_sina_minute_bars(config())
    request.assert_called_once_with("sh600000", 1)
    assert len(bars) == 536  # 60 prior real rows, 476 observed evaluation rows
    assert metadata["coverage"]["source_rows"] == 720
    assert metadata["coverage"]["warmup_bars"] == 60
    assert metadata["coverage"]["evaluation_bars"] == 476
    assert metadata["coverage"]["evaluation_start"] == "2026-09-17T09:31:00+08:00"
    assert metadata["coverage"]["evaluation_end"] == "2026-09-18T15:00:00+08:00"
    assert metadata["coverage"]["reference_session_gaps"] == [
        {"date": "2026-09-17", "missing_labels": ["14:58", "14:59"]},
        {"date": "2026-09-18", "missing_labels": ["14:58", "14:59"]},
    ]
    assert metadata["volume_unit"] == "provider_raw"
    assert metadata["coverage"]["calendar_verified"] is False
    assert bars[-1]["volume"] == 5397754
    assert bars[-1]["close"] == 9.07
    assert all(item["adj_factor"] is None and item["limit_up"] is None for item in bars)
    assert all("14:58" not in item["date"] and "14:59" not in item["date"] for item in bars)
    assert metadata["warnings"]


@pytest.mark.parametrize("updates,code", [
    ({"start_date": "2026-09-14"}, "sina_date_range_not_covered"),
    ({"end_date": "2026-09-19"}, "sina_date_range_not_covered"),
    ({"start_date": "2026-09-15"}, "sina_insufficient_warmup"),
])
def test_no_silent_shortening_or_invented_warmup(actual_rows, updates, code):
    with patch.object(market, "_request_rows", return_value=actual_rows):
        with pytest.raises(MinuteDataError) as error:
            market.load_sina_minute_bars(config(**updates))
    assert error.value.code == code


def test_incomplete_final_day_is_not_claimed_as_whole_day_coverage(actual_rows):
    rows = [row for row in actual_rows if row["day"] < "2026-09-18 15:00:00"]
    with patch.object(market, "_request_rows", return_value=rows):
        with pytest.raises(MinuteDataError) as error:
            market.load_sina_minute_bars(config())
    assert error.value.code == "sina_date_range_not_covered"


def test_identical_duplicates_sort_and_zero_volume_are_preserved(actual_rows):
    rows = deepcopy(actual_rows)
    rows[-1]["volume"] = "0"
    rows.append(deepcopy(rows[-1]))
    rows.reverse()
    with patch.object(market, "_request_rows", return_value=rows):
        bars, metadata = market.load_sina_minute_bars(config())
    assert bars[-1]["volume"] == 0
    assert metadata["coverage"]["duplicate_rows"] == 1
    assert len(bars) == 536


@pytest.mark.parametrize("field,value,code", [
    ("volume", "nan", "minute_invalid_numeric_value"),
    ("open", "-1", "minute_invalid_numeric_value"),
    ("high", "1", "minute_invalid_ohlc"),
    ("day", "2026-09-18 15:00:01", "minute_invalid_timestamp"),
    ("day", "2026-09-18 16:00:00", "minute_invalid_timestamp"),
])
def test_bad_provider_rows_fail_closed(actual_rows, field, value, code):
    rows = deepcopy(actual_rows)
    rows[-1][field] = value
    with patch.object(market, "_request_rows", return_value=rows):
        with pytest.raises(MinuteDataError) as error:
            market.load_sina_minute_bars(config())
    assert error.value.code == code


def test_conflicting_duplicate_is_not_arbitrarily_selected(actual_rows):
    rows = deepcopy(actual_rows)
    duplicate = deepcopy(rows[-1])
    duplicate["volume"] = "1"
    rows.append(duplicate)
    with patch.object(market, "_request_rows", return_value=rows):
        with pytest.raises(MinuteDataError) as error:
            market.load_sina_minute_bars(config())
    assert error.value.code == "minute_conflicting_duplicate"


def test_future_provider_row_is_excluded_without_affecting_selected_history(actual_rows):
    rows = deepcopy(actual_rows)
    future = deepcopy(rows[-1])
    future["day"] = "2026-09-21 09:31:00"
    rows.append(future)
    with patch.object(market, "_request_rows", return_value=rows):
        bars, metadata = market.load_sina_minute_bars(config())
    assert len(bars) == 536
    assert bars[-1]["date"] == "2026-09-18T15:00:00+08:00"
    assert metadata["coverage"]["unclosed_rows_excluded"] == 1
