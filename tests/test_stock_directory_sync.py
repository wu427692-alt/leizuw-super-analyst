"""Offline official-directory synchronization, identity retention and locks."""
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.data import stock_index_loader
from src.services import stock_index_remote_service as service


def seed():
    rows = [[f"{100 + i:06d}.SZ", f"{100 + i:06d}", f"股票{i}", "gupiao", "gp", [], "CN", "stock", True, 100]
            for i in range(100)]
    rows.extend([
        ["603306.SH", "603306", "旧华懋名称", "old", "old", ["华懋"], "CN", "stock", True, 120],
        ["00700.HK", "00700", "腾讯控股", "tencent", "tx", [], "HK", "stock", True, 100],
        ["AAPL", "AAPL", "苹果", "apple", "pg", [], "US", "stock", True, 100],
        ["000001.SH", "000001", "上证指数", "shangzheng", "sz", [], "CN", "index", True, 100],
        ["510300.SH", "510300", "沪深300ETF", "", "", [], "CN", "etf", True, 100],
    ])
    return rows


def groups():
    return {"L": [{"ts_code": "603306.SH", "name": "华懋科技", "fullname": "华懋(厦门)新材料科技股份有限公司", "list_status": "L"},
                   {"ts_code": "688828.SH", "name": "国仪公司", "list_status": "L"},
                   {"ts_code": "920976.BJ", "name": "北交新股", "list_status": "L"}],
            "P": [{"ts_code": "688900.SH", "name": "待上市公司", "list_status": "P"}],
            "D": [{"ts_code": "000100.SZ", "name": "退市股票", "list_status": "D"}]}


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "stocks.index.json"
    path.write_text(json.dumps(seed(), ensure_ascii=False), encoding="utf-8")
    return service.RemoteStockIndexSettings(cache_path=path, live_directory_enabled=True)


def test_merge_new_listing_rename_delisting_and_other_markets():
    merged = {row[0]: row for row in service._merge_tushare_directory(seed(), groups(), min_listed_items=1)}
    assert merged["688828.SH"][2] == "国仪公司"
    assert merged["920976.BJ"][6] == "BSE"
    assert merged["688900.SH"][8] is False
    assert merged["000100.SZ"][8] is False
    assert set(merged["603306.SH"][5]) == {"华懋", "旧华懋名称", "华懋(厦门)新材料科技股份有限公司"}
    assert merged["603306.SH"][9] == 120
    for code in ("00700.HK", "AAPL", "000001.SH", "510300.SH"):
        assert merged[code] == next(row for row in seed() if row[0] == code)
    assert merged["000101.SZ"][8] is True


def test_historical_t_prefixed_delisted_identity_never_rewrites_active_code(settings):
    payload = groups()
    payload["L"].append({"ts_code": "600018.SH", "name": "当前公司", "list_status": "L"})
    payload["D"].append({"ts_code": "T600018.SH", "name": "历史退市公司", "list_status": "D"})
    result = service.refresh_live_stock_directory(settings, fetcher=lambda: payload, min_listed_items=1)
    assert result.refreshed
    merged = {row[0]: row for row in json.loads(settings.cache_path.read_text())}
    assert merged["600018.SH"][2] == "当前公司" and merged["600018.SH"][8]
    assert "T600018.SH" not in merged
    assert service.stock_directory_status(settings.cache_path)["historical_nonstandard_count"] == 1


def test_official_success_has_real_timestamp_and_clears_name_cache(settings):
    with patch.object(service, "_clear_backend_stock_index_cache") as clear:
        result = service.refresh_live_stock_directory(settings, fetcher=groups, min_listed_items=1)
    assert result.refreshed
    status = service.stock_directory_status(settings.cache_path)
    assert status["source"] == "tushare.stock_basic"
    assert status["last_success_at"] and status["actual_success_at"]
    assert status["as_of_date"] is None
    assert status["listed_count"] == 3
    assert status["delisted_count"] == status["pending_count"] == 1
    assert not status["stale"]
    clear.assert_called_once_with()


def test_subsequent_calls_within_one_hour_do_not_refetch(settings):
    fetcher = Mock(side_effect=groups)
    first = service.refresh_live_stock_directory(settings, fetcher=fetcher, min_listed_items=1)
    second = service.refresh_live_stock_directory(settings, fetcher=fetcher, min_listed_items=1)
    assert first.refreshed and second.skipped
    assert fetcher.call_count == 1


def test_any_upstream_failure_keeps_old_directory_and_backs_off(settings):
    original = settings.cache_path.read_bytes()
    fetcher = Mock(side_effect=TimeoutError("secret upstream content must not escape"))
    failed = service.refresh_live_stock_directory(settings, fetcher=fetcher, min_listed_items=1)
    retry = service.refresh_live_stock_directory(settings, fetcher=fetcher, min_listed_items=1)
    assert failed.error == "TimeoutError" and retry.skipped
    assert settings.cache_path.read_bytes() == original
    assert service.stock_directory_status(settings.cache_path)["stale"]
    assert fetcher.call_count == 1


@pytest.mark.parametrize("mutation", ["too_small", "duplicate", "invalid_suffix", "missing_status", "wrong_status"])
def test_incomplete_or_bad_payload_cannot_replace_directory(settings, mutation):
    original = settings.cache_path.read_bytes()
    payload = groups()
    minimum = 1
    if mutation == "too_small":
        minimum = 1000
    elif mutation == "duplicate":
        payload["D"].append(dict(payload["L"][0]))
    elif mutation == "invalid_suffix":
        payload["L"][0]["ts_code"] = "603306.XX"
    elif mutation == "missing_status":
        del payload["P"]
    else:
        payload["L"][0]["list_status"] = "D"
    result = service.refresh_live_stock_directory(settings, fetcher=lambda: payload, min_listed_items=minimum)
    assert result.error == "ValueError"
    assert settings.cache_path.read_bytes() == original


def test_official_success_survives_later_failure(settings):
    with patch.object(service.time, "time", return_value=1000):
        service.refresh_live_stock_directory(settings, fetcher=groups, min_listed_items=1)
    original = settings.cache_path.read_bytes()
    with patch.object(service.time, "time", return_value=5000):
        service.refresh_live_stock_directory(settings, fetcher=Mock(side_effect=RuntimeError), min_listed_items=1)
    status = service.stock_directory_status(settings.cache_path, now=5000)
    assert status["status"] == "error" and status["stale"]
    assert status["last_success_epoch"] == 1000
    assert settings.cache_path.read_bytes() == original


def test_large_partial_response_is_rejected_even_above_absolute_minimum():
    previous = [[f"{i:06d}.SZ", f"{i:06d}", f"公司{i}", "", "", [], "CN", "stock", True, 100]
                for i in range(5000)]
    partial = {"L": [{"ts_code": f"{i:06d}.SZ", "name": f"公司{i}", "list_status": "L"}
                      for i in range(1500)], "P": [], "D": []}
    with pytest.raises(ValueError, match="unexpectedly shrank"):
        service._merge_tushare_directory(previous, partial)


def test_removing_token_never_overwrites_official_directory_with_github_seed(settings):
    service.refresh_live_stock_directory(settings, fetcher=groups, min_listed_items=1)
    original = settings.cache_path.read_bytes()
    no_token = service.RemoteStockIndexSettings(cache_path=settings.cache_path, ttl_hours=0)
    with patch.object(service.requests, "get") as get:
        result = service.refresh_remote_stock_index_cache(no_token)
    assert result.skipped
    assert settings.cache_path.read_bytes() == original
    get.assert_not_called()


def test_existing_refresh_entry_uses_official_directory_when_configured(settings):
    with patch.object(service, "refresh_live_stock_directory", return_value=Mock()) as live, \
         patch.object(service.requests, "get") as get:
        service.refresh_remote_stock_index_cache(settings)
    live.assert_called_once_with(settings)
    get.assert_not_called()
    config = SimpleNamespace(stock_index_remote_update_enabled=True, tushare_token="configured")
    assert service.settings_from_config(config).live_directory_enabled


def test_cross_process_file_lock_skips_without_waiting_or_fetching(settings):
    handle = settings.cache_path.with_name(f".{settings.cache_path.name}.refresh.lock").open("a+b")
    assert service._lock_directory_file(handle)
    try:
        fetcher = Mock(side_effect=groups)
        result = service.refresh_live_stock_directory(settings, fetcher=fetcher, min_listed_items=1)
        assert result.skipped
        fetcher.assert_not_called()
    finally:
        service._unlock_directory_file(handle)
        handle.close()


def test_background_trigger_and_status_do_not_wait_for_slow_upstream(settings):
    started = threading.Event()
    release = threading.Event()

    def slow_refresh(_settings):
        started.set()
        release.wait(3)

    with patch.object(service, "refresh_remote_stock_index_cache", side_effect=slow_refresh) as refresh:
        try:
            before = time.perf_counter()
            assert service.ensure_stock_directory_refresh(settings)
            assert time.perf_counter() - before < .5
            assert started.wait(1)
            assert service.ensure_stock_directory_refresh(settings) is False
            before = time.perf_counter()
            service.stock_directory_status(settings.cache_path)
            assert time.perf_counter() - before < .5
            assert refresh.call_count == 1
        finally:
            release.set()
            service._DIRECTORY_REFRESH_THREAD.join(2)


def test_cache_clear_updates_existing_company_resolution(settings):
    with patch.object(stock_index_loader, "get_stock_index_candidate_paths", return_value=(settings.cache_path,)), \
         patch.object(stock_index_loader, "get_remote_stock_index_cache_path", return_value=settings.cache_path):
        stock_index_loader.clear_stock_index_cache()
        assert stock_index_loader.get_index_stock_name("603306.SH") == "旧华懋名称"
        service.refresh_live_stock_directory(settings, fetcher=groups, min_listed_items=1)
        assert stock_index_loader.get_index_stock_name("603306.SH") == "华懋科技"
        stock_index_loader.clear_stock_index_cache()


def test_gateway_fetch_requires_all_three_statuses_without_parallel_api():
    gateway = Mock()
    gateway.query.side_effect = lambda _api, params, fields: {"rows": groups()[params["list_status"]]}
    with patch("src.services.financial_data_service.TushareGatewayService", return_value=gateway):
        assert service._fetch_tushare_directory() == groups()
    assert [call.kwargs["params"]["list_status"] for call in gateway.query.call_args_list] == ["L", "P", "D"]


def test_external_worker_metadata_invalidates_web_process_cache(settings):
    with patch.object(stock_index_loader, "get_stock_index_candidate_paths", return_value=(settings.cache_path,)), \
         patch.object(stock_index_loader, "get_remote_stock_index_cache_path", return_value=settings.cache_path):
        stock_index_loader.clear_stock_index_cache()
        assert stock_index_loader.get_index_stock_name("603306.SH") == "旧华懋名称"
        # Simulate another process: it cannot clear this process's Python cache.
        with patch.object(service, "_clear_backend_stock_index_cache"):
            service.refresh_live_stock_directory(settings, fetcher=groups, min_listed_items=1)
        assert stock_index_loader.get_index_stock_name("603306.SH") == "华懋科技"
        stock_index_loader.clear_stock_index_cache()
