from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.llm.provider_schedule import (
    active_provider_name,
    direct_chat_headers,
    effective_litellm_models,
    prepare_direct_chat_payload,
    route_status,
    scheduled_direct_chat_routes,
)


def _config():
    channels = [
        {
            "name": "kimi_code",
            "enabled": True,
            "protocol": "openai",
            "base_url": "https://api.kimi.com/coding/v1",
            "api_keys": ["kimi-secret"],
            "models": ["openai/kimi-for-coding"],
        },
        {
            "name": "deepseek",
            "enabled": True,
            "protocol": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_keys": ["deepseek-secret"],
            "models": ["deepseek/deepseek-v4-flash"],
        },
    ]
    model_list = [
        {"model_name": model, "litellm_params": {"model": model}}
        for channel in channels
        for model in channel["models"]
    ]
    return SimpleNamespace(
        llm_channels=channels,
        llm_model_list=model_list,
        litellm_model="openai/kimi-for-coding",
        litellm_fallback_models=["deepseek/deepseek-v4-flash"],
        deepseek_api_keys=["legacy-deepseek-secret"],
    )


def test_schedule_uses_kimi_days_9_to_23_and_deepseek_otherwise(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCHEDULE_ENABLED", "true")
    timezone = ZoneInfo("Asia/Shanghai")

    assert active_provider_name(datetime(2026, 9, 8, 23, tzinfo=timezone)) == "deepseek"
    assert active_provider_name(datetime(2026, 9, 9, 0, tzinfo=timezone)) == "kimi_code"
    assert active_provider_name(datetime(2026, 9, 23, 23, tzinfo=timezone)) == "kimi_code"
    assert active_provider_name(datetime(2026, 9, 24, 0, tzinfo=timezone)) == "deepseek"


def test_scheduled_model_order_switches_without_restart(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCHEDULE_ENABLED", "true")
    timezone = ZoneInfo("Asia/Shanghai")
    config = _config()

    kimi_order = effective_litellm_models(config, at=datetime(2026, 9, 9, tzinfo=timezone))
    deepseek_order = effective_litellm_models(config, at=datetime(2026, 9, 24, tzinfo=timezone))

    assert kimi_order == ["openai/kimi-for-coding", "deepseek/deepseek-v4-flash"]
    assert deepseek_order == ["deepseek/deepseek-v4-flash", "openai/kimi-for-coding"]


def test_direct_routes_do_not_expose_keys_and_report_status(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCHEDULE_ENABLED", "true")
    config = _config()
    timezone = ZoneInfo("Asia/Shanghai")
    routes = scheduled_direct_chat_routes(config, at=datetime(2026, 9, 12, tzinfo=timezone))

    assert [route.channel for route in routes] == ["kimi_code", "deepseek"]
    assert "kimi-secret" not in repr(routes[0])
    assert route_status(config, at=datetime(2026, 9, 12, tzinfo=timezone))["models"] == [
        "kimi-for-coding",
        "deepseek-v4-flash",
    ]
    assert route_status(config, at=datetime(2026, 9, 12, tzinfo=timezone))["timezone"] == "Asia/Shanghai"


def test_disabled_schedule_preserves_configured_primary(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER_SCHEDULE_ENABLED", raising=False)
    config = _config()

    assert effective_litellm_models(config) == [
        "openai/kimi-for-coding",
        "deepseek/deepseek-v4-flash",
    ]


def test_kimi_payload_keeps_thinking_disabled_but_removes_sampling_overrides(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SCHEDULE_ENABLED", "true")
    route = scheduled_direct_chat_routes(
        _config(),
        requested_model="kimi-for-coding",
    )[0]

    payload = prepare_direct_chat_payload(route, {
        "model": "old",
        "messages": [],
        "thinking": {"type": "disabled"},
        "temperature": 0.1,
        "top_p": 0.9,
        "n": 1,
    })

    assert payload == {
        "model": "kimi-for-coding",
        "messages": [],
        "thinking": {"type": "disabled"},
    }
    assert direct_chat_headers(route)["User-Agent"] == "daily-stock-analysis/1.0"
