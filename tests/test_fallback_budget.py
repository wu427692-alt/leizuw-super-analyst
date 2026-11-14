# -*- coding: utf-8 -*-
"""降级链整体预算 + 数据源能力声明的回归测试。

背景：`GET /api/v1/stocks/{code}/quote` 离线单请求实测 35s、`/history` 87s，
根因是 DataFetcherManager 的串行降级链没有整体时间预算，且假设所有数据源都
实现了 `get_realtime_quote`。本文件覆盖三条修复路径：

1. 降级链预算耗尽时提前返回当前最好结果，并记录跳过了哪些源；
2. 缺少能力实现的数据源（如 Baostock 无实时行情）被优雅跳过，而不是 AttributeError；
3. 正常可用的源在预算内仍然成功，且默认预算足够保守。

全部用 mock fetcher，不访问真实网络。
"""

import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from data_provider.base import (
    DEFAULT_HISTORY_DATA_BUDGET_SECONDS,
    DEFAULT_REALTIME_QUOTE_BUDGET_SECONDS,
    BaseFetcher,
    DataFetcherManager,
    DataFetchError,
    FallbackBudget,
)
from data_provider.baostock_fetcher import BaostockFetcher
from data_provider.pytdx_fetcher import PytdxFetcher
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


def _full_quote(code: str = "600519", **overrides) -> UnifiedRealtimeQuote:
    payload = {
        "code": code,
        "name": "贵州茅台",
        "source": RealtimeSource.AKSHARE_EM,
        "price": 1688.0,
        "volume_ratio": 1.2,
        "turnover_rate": 1.0,
        "pe_ratio": 30.0,
        "pb_ratio": 10.0,
        "total_mv": 1.0,
        "circ_mv": 1.0,
        "amplitude": 1.0,
    }
    payload.update(overrides)
    return UnifiedRealtimeQuote(**payload)


class _SlowQuoteFetcher(BaseFetcher):
    """A source that hangs like an unreachable upstream, then reports no data."""

    def __init__(self, name: str, priority: int, delay: float = 5.0):
        self.name = name
        self.priority = priority
        self.delay = delay
        self.calls = 0

    def get_realtime_quote(self, stock_code, **kwargs):
        self.calls += 1
        time.sleep(self.delay)
        return None

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        raise NotImplementedError

    def _normalize_data(self, df, stock_code):
        return df


class _HealthyQuoteFetcher(BaseFetcher):
    def __init__(self, name: str = "AkshareFetcher", priority: int = 1, quote=None):
        self.name = name
        self.priority = priority
        self._quote = quote if quote is not None else _full_quote()
        self.calls = 0

    def get_realtime_quote(self, stock_code, **kwargs):
        self.calls += 1
        return self._quote

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        raise NotImplementedError

    def _normalize_data(self, df, stock_code):
        return df


class _SlowDailyFetcher(BaseFetcher):
    def __init__(self, name: str, priority: int, delay: float = 5.0):
        self.name = name
        self.priority = priority
        self.delay = delay
        self.calls = 0

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        self.calls += 1
        time.sleep(self.delay)
        raise DataFetchError(f"[{self.name}] mock upstream timeout")

    def _normalize_data(self, df, stock_code):
        return df


class _HealthyDailyFetcher(BaseFetcher):
    def __init__(self, name: str = "AkshareFetcher", priority: int = 1):
        self.name = name
        self.priority = priority
        self.calls = 0

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        self.calls += 1
        return pd.DataFrame(
            {
                "date": ["2026-01-05", "2026-01-06", "2026-01-07"],
                "open": [10.0, 10.2, 10.1],
                "high": [10.5, 10.4, 10.3],
                "low": [9.8, 10.1, 10.0],
                "close": [10.3, 10.35, 10.2],
                "volume": [1000, 1200, 900],
                "amount": [10300, 12420, 9180],
                "pct_chg": [1.0, 0.49, -1.45],
            }
        )

    def _normalize_data(self, df, stock_code):
        return df


class _NoRealtimeFetcher(BaseFetcher):
    """Like Baostock: daily data only, no get_realtime_quote implementation."""

    def __init__(self, name: str = "EfinanceFetcher", priority: int = 0):
        self.name = name
        self.priority = priority

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        raise NotImplementedError

    def _normalize_data(self, df, stock_code):
        return df


def _realtime_config(**overrides):
    config = SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="efinance,akshare_em,akshare_sina",
        realtime_quote_budget_seconds=0.4,
        realtime_cache_ttl=600,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _history_config(budget: float = 0.4):
    return SimpleNamespace(history_data_budget_seconds=budget)


# ---------------------------------------------------------------------------
# FallbackBudget
# ---------------------------------------------------------------------------


def test_fallback_budget_tracks_remaining_and_zero_disables_limit():
    budget = FallbackBudget(0.05, chain="realtime_quote")
    assert budget.enabled is True
    time.sleep(0.08)
    assert budget.exhausted() is True
    assert budget.remaining() == 0.0

    unlimited = FallbackBudget(0, chain="realtime_quote")
    assert unlimited.enabled is False
    assert unlimited.exhausted() is False
    assert unlimited.remaining() == float("inf")


# ---------------------------------------------------------------------------
# 根因 1：实时行情降级链整体预算
# ---------------------------------------------------------------------------


@patch("src.config.get_config")
def test_realtime_budget_returns_early_and_logs_skipped_sources(mock_get_config, caplog):
    mock_get_config.return_value = _realtime_config()
    efinance = _SlowQuoteFetcher("EfinanceFetcher", 0, delay=5.0)
    akshare = _SlowQuoteFetcher("AkshareFetcher", 1, delay=5.0)
    tushare = _SlowQuoteFetcher("TushareFetcher", 2, delay=5.0)
    manager = DataFetcherManager(fetchers=[efinance, akshare, tushare])

    started = time.monotonic()
    with caplog.at_level("INFO"):
        quote = manager.get_realtime_quote("600519")
    elapsed = time.monotonic() - started

    assert quote is None
    # 串行各等 5s 至少 15s；加上预算后应该在 0.4s 量级返回。
    assert elapsed < 2.0, f"降级链耗时 {elapsed:.2f}s，预算未生效"
    assert efinance.calls == 1
    assert akshare.calls == 0 and tushare.calls == 0
    assert "降级链预算耗尽" in caplog.text
    assert "skipped=akshare_em, akshare_sina" in caplog.text


@patch("src.config.get_config")
def test_realtime_budget_keeps_primary_quote_when_supplement_is_cut(mock_get_config, caplog):
    """预算耗尽时返回第一个成功结果（可能字段不全），不要丢掉已有数据。"""
    mock_get_config.return_value = _realtime_config(realtime_quote_budget_seconds=0.3)
    primary_quote = _full_quote(volume_ratio=None)  # 需要后续源补充
    primary = _HealthyQuoteFetcher(name="EfinanceFetcher", priority=0, quote=primary_quote)
    supplement = _SlowQuoteFetcher("AkshareFetcher", 1, delay=5.0)
    manager = DataFetcherManager(fetchers=[primary, supplement])

    started = time.monotonic()
    with caplog.at_level("INFO"):
        quote = manager.get_realtime_quote("600519")
    elapsed = time.monotonic() - started

    assert quote is primary_quote
    assert elapsed < 2.0
    assert primary.calls == 1
    assert "降级链预算耗尽" in caplog.text


@patch("src.config.get_config")
def test_realtime_budget_does_not_block_healthy_source(mock_get_config):
    mock_get_config.return_value = _realtime_config()
    healthy = _HealthyQuoteFetcher(name="EfinanceFetcher", priority=0)
    manager = DataFetcherManager(fetchers=[healthy])

    started = time.monotonic()
    quote = manager.get_realtime_quote("600519")
    elapsed = time.monotonic() - started

    assert quote is not None
    assert quote.price == 1688.0
    assert healthy.calls == 1
    assert elapsed < 1.0


# ---------------------------------------------------------------------------
# 根因 3：能力声明，跳过不支持的数据源
# ---------------------------------------------------------------------------


@patch("src.config.get_config")
def test_realtime_skips_source_without_capability_instead_of_attribute_error(
    mock_get_config, caplog
):
    mock_get_config.return_value = _realtime_config()
    unsupported = _NoRealtimeFetcher(name="EfinanceFetcher", priority=0)
    healthy = _HealthyQuoteFetcher(name="AkshareFetcher", priority=1)
    manager = DataFetcherManager(fetchers=[unsupported, healthy])

    with caplog.at_level("INFO"):
        quote = manager.get_realtime_quote("600519")

    assert quote is not None
    assert healthy.calls == 1
    assert "不支持 realtime_quote" in caplog.text


def test_baostock_declares_no_realtime_capability():
    fetcher = BaostockFetcher()
    assert fetcher.supports_capability("realtime_quote") is False
    assert fetcher.supports_capability("daily_data") is True
    assert "realtime_quote" not in fetcher.supported_capabilities()
    assert "daily_data" in fetcher.supported_capabilities()

    manager = DataFetcherManager(fetchers=[fetcher])
    assert manager._get_fetcher_by_name("BaostockFetcher", capability="realtime_quote") is None
    assert manager._get_fetcher_by_name("BaostockFetcher") is fetcher


# ---------------------------------------------------------------------------
# 根因 1（历史行情）：日线降级链共用同一套预算机制
# ---------------------------------------------------------------------------


@patch("src.config.get_config")
def test_history_budget_returns_early_with_skipped_sources(mock_get_config, caplog):
    mock_get_config.return_value = _history_config(0.4)
    efinance = _SlowDailyFetcher("EfinanceFetcher", 0, delay=5.0)
    akshare = _SlowDailyFetcher("AkshareFetcher", 1, delay=5.0)
    yfinance = _SlowDailyFetcher("YfinanceFetcher", 2, delay=5.0)
    manager = DataFetcherManager(fetchers=[efinance, akshare, yfinance])

    started = time.monotonic()
    with caplog.at_level("INFO"):
        with pytest.raises(DataFetchError) as captured:
            manager.get_daily_data("600519", start_date="2026-01-01", end_date="2026-02-01")
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"历史行情降级链耗时 {elapsed:.2f}s，预算未生效"
    assert efinance.calls == 1
    assert akshare.calls == 0 and yfinance.calls == 0
    assert "历史行情降级链预算耗尽" in caplog.text
    assert "BudgetExhausted" in str(captured.value)
    assert "AkshareFetcher" in str(captured.value)


@patch("src.config.get_config")
def test_history_budget_does_not_block_healthy_source(mock_get_config):
    mock_get_config.return_value = _history_config(DEFAULT_HISTORY_DATA_BUDGET_SECONDS)
    healthy = _HealthyDailyFetcher(name="EfinanceFetcher", priority=0)
    manager = DataFetcherManager(fetchers=[healthy])

    started = time.monotonic()
    df, source = manager.get_daily_data(
        "600519", start_date="2026-01-01", end_date="2026-02-01"
    )
    elapsed = time.monotonic() - started

    assert source == "EfinanceFetcher"
    assert not df.empty
    assert healthy.calls == 1
    assert elapsed < 1.0


@patch("src.config.get_config")
def test_history_budget_zero_keeps_legacy_fallback_chain(mock_get_config):
    """预算 <= 0 表示不限制：失败源后面仍然继续尝试下一个源。"""
    mock_get_config.return_value = _history_config(0)
    failing = _SlowDailyFetcher("EfinanceFetcher", 0, delay=0.0)
    healthy = _HealthyDailyFetcher(name="AkshareFetcher", priority=1)
    manager = DataFetcherManager(fetchers=[failing, healthy])

    df, source = manager.get_daily_data(
        "600519", start_date="2026-01-01", end_date="2026-02-01"
    )

    assert source == "AkshareFetcher"
    assert not df.empty


# ---------------------------------------------------------------------------
# 根因 2：pytdx 连接尝试收敛
# ---------------------------------------------------------------------------


class _FakeTdxHqApi:
    attempts = []

    def connect(self, host, port, time_out=5):
        type(self).attempts.append((host, port, time_out))
        return False

    def disconnect(self):
        pass


def test_pytdx_connect_attempts_are_capped(monkeypatch):
    monkeypatch.setenv("PYTDX_MAX_CONNECT_ATTEMPTS", "2")
    monkeypatch.setenv("PYTDX_CONNECT_TIMEOUT_SECONDS", "0.2")
    _FakeTdxHqApi.attempts = []
    hosts = [("10.0.0.1", 7709), ("10.0.0.2", 7709), ("10.0.0.3", 7709)]
    fetcher = PytdxFetcher(hosts=hosts)
    monkeypatch.setattr(fetcher, "_get_pytdx", lambda: _FakeTdxHqApi)

    started = time.monotonic()
    quote = fetcher.get_realtime_quote("600519")
    elapsed = time.monotonic() - started

    assert quote is None
    assert len(_FakeTdxHqApi.attempts) == 2
    assert all(timeout <= 0.2 + 1e-6 for _host, _port, timeout in _FakeTdxHqApi.attempts)
    assert elapsed < 2.0
    # 失败后进入冷却期，避免后续请求反复撞同一批服务器。
    assert fetcher.is_available_for_request("realtime_quote") is False


def test_pytdx_connect_timeout_defaults_are_conservative(monkeypatch):
    monkeypatch.delenv("PYTDX_MAX_CONNECT_ATTEMPTS", raising=False)
    monkeypatch.delenv("PYTDX_CONNECT_TIMEOUT_SECONDS", raising=False)
    fetcher = PytdxFetcher(hosts=[("10.0.0.1", 7709)])
    assert fetcher._max_connect_attempts == 3
    assert fetcher._connect_timeout == 3.0


def test_capability_check_accepts_dedicated_prefetch_method():
    """能力声明按方法名映射：批量预取源只需实现 prefetch_daily_klines。"""

    class _PrefetchOnlyFetcher:
        name = "TickFlowFetcher"
        priority = 2

        def __init__(self):
            self.calls = []

        def prefetch_daily_klines(self, stock_codes, days=30):
            self.calls.append((list(stock_codes), days))
            return len(stock_codes)

    fetcher = _PrefetchOnlyFetcher()
    manager = DataFetcherManager(fetchers=[fetcher])

    assert manager.prefetch_daily_klines(["600519", "000001"], days=30) == 2
    assert fetcher.calls == [(["600519", "000001"], 30)]


# ---------------------------------------------------------------------------
# 可配置项
# ---------------------------------------------------------------------------


def test_budget_config_defaults_and_env_overrides():
    from src.config import Config

    with patch("src.config.setup_env"), patch.object(Config, "_parse_litellm_yaml", return_value=[]):
        with patch.dict(
            os.environ,
            {"STOCK_LIST": "600519"},
            clear=True,
        ):
            default_config = Config._load_from_env()
        assert default_config.realtime_quote_budget_seconds == DEFAULT_REALTIME_QUOTE_BUDGET_SECONDS
        assert default_config.history_data_budget_seconds == DEFAULT_HISTORY_DATA_BUDGET_SECONDS

        with patch.dict(
            os.environ,
            {
                "STOCK_LIST": "600519",
                "REALTIME_QUOTE_BUDGET_SECONDS": "12.5",
                "HISTORY_DATA_BUDGET_SECONDS": "0",
            },
            clear=True,
        ):
            overridden = Config._load_from_env()
        assert overridden.realtime_quote_budget_seconds == 12.5
        assert overridden.history_data_budget_seconds == 0.0

        with patch.dict(
            os.environ,
            {
                "STOCK_LIST": "600519",
                "REALTIME_QUOTE_BUDGET_SECONDS": "not-a-number",
            },
            clear=True,
        ):
            invalid = Config._load_from_env()
        assert invalid.realtime_quote_budget_seconds == DEFAULT_REALTIME_QUOTE_BUDGET_SECONDS
