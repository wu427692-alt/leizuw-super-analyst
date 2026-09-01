# -*- coding: utf-8 -*-
"""Calendar-based routing between the configured low-cost LLM channels.

The scheduler deliberately works from the existing ``LLM_CHANNELS`` registry.
It never reads or persists a second copy of an API key, and it returns only
models that are actually present in the parsed runtime configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_DEEPSEEK_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_KIMI_MODEL = "openai/kimi-for-coding"
DEFAULT_DEEPSEEK_CHANNEL = "deepseek"
DEFAULT_KIMI_CHANNEL = "kimi_code"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_day(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 31))


def _timezone() -> ZoneInfo:
    name = (os.getenv("LLM_PROVIDER_SCHEDULE_TIMEZONE") or "Asia/Shanghai").strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def provider_schedule_enabled() -> bool:
    return _env_bool("LLM_PROVIDER_SCHEDULE_ENABLED", False)


def active_provider_name(at: Optional[datetime] = None) -> str:
    """Return the active logical provider for the configured calendar window.

    With the production defaults, Kimi is active on days 9-23 inclusive and
    DeepSeek is active from day 24 through day 8 of the following month.
    The implementation also supports a wrapping Kimi window for completeness.
    """

    current = at or datetime.now(_timezone())
    if current.tzinfo is None:
        current = current.replace(tzinfo=_timezone())
    else:
        current = current.astimezone(_timezone())
    day = current.day
    kimi_start = _bounded_day("LLM_PROVIDER_SCHEDULE_KIMI_START_DAY", 9)
    deepseek_start = _bounded_day("LLM_PROVIDER_SCHEDULE_DEEPSEEK_START_DAY", 24)
    if kimi_start == deepseek_start:
        return DEFAULT_DEEPSEEK_CHANNEL
    if kimi_start < deepseek_start:
        kimi_active = kimi_start <= day < deepseek_start
    else:
        kimi_active = day >= kimi_start or day < deepseek_start
    return DEFAULT_KIMI_CHANNEL if kimi_active else DEFAULT_DEEPSEEK_CHANNEL


def _configured_model_names(config: Any) -> List[str]:
    names: List[str] = []
    seen = set()
    for entry in getattr(config, "llm_model_list", []) or []:
        if not isinstance(entry, dict):
            continue
        params = entry.get("litellm_params") or {}
        name = str(entry.get("model_name") or params.get("model") or "").strip()
        if not name or name.startswith("__legacy_") or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _preferred_model(provider_name: str) -> str:
    if provider_name == DEFAULT_KIMI_CHANNEL:
        return (os.getenv("LLM_PROVIDER_SCHEDULE_KIMI_MODEL") or DEFAULT_KIMI_MODEL).strip()
    return (os.getenv("LLM_PROVIDER_SCHEDULE_DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL).strip()


def _matches_model(configured: Sequence[str], preferred: str) -> Optional[str]:
    if preferred in configured:
        return preferred
    preferred_wire = preferred.split("/", 1)[-1]
    for candidate in configured:
        if candidate.split("/", 1)[-1] == preferred_wire:
            return candidate
    return None


def scheduled_litellm_models(config: Any, *, at: Optional[datetime] = None) -> List[str]:
    """Return the active low-cost model followed by its cross-provider standby."""

    if not provider_schedule_enabled():
        return []
    configured = _configured_model_names(config)
    if not configured:
        return []
    active = active_provider_name(at)
    standby = DEFAULT_DEEPSEEK_CHANNEL if active == DEFAULT_KIMI_CHANNEL else DEFAULT_KIMI_CHANNEL
    ordered: List[str] = []
    for provider_name in (active, standby):
        matched = _matches_model(configured, _preferred_model(provider_name))
        if matched and matched not in ordered:
            ordered.append(matched)
    return ordered


def effective_litellm_models(config: Any, *, at: Optional[datetime] = None) -> List[str]:
    """Resolve runtime model order while keeping legacy behavior when disabled."""

    scheduled = scheduled_litellm_models(config, at=at)
    if scheduled:
        return scheduled
    raw = [getattr(config, "litellm_model", "")] + list(
        getattr(config, "litellm_fallback_models", []) or []
    )
    result: List[str] = []
    for model in raw:
        normalized = str(model or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


@dataclass(frozen=True)
class DirectChatRoute:
    """One OpenAI-compatible direct chat route without exposing its secret."""

    channel: str
    provider: str
    litellm_model: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    extra_headers: Dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model and self.base_url)


def _route_from_channel(
    config: Any,
    *,
    channel_name: str,
    preferred_model: str,
) -> Optional[DirectChatRoute]:
    for channel in getattr(config, "llm_channels", []) or []:
        if not isinstance(channel, dict) or not channel.get("enabled", True):
            continue
        name = str(channel.get("name") or "").strip().lower()
        if name != channel_name:
            continue
        models = [str(value or "").strip() for value in (channel.get("models") or []) if str(value or "").strip()]
        selected = _matches_model(models, preferred_model)
        keys = [str(value or "").strip() for value in (channel.get("api_keys") or []) if str(value or "").strip()]
        base_url = str(channel.get("base_url") or "").strip().rstrip("/")
        if not selected or not keys or not base_url:
            return None
        provider = selected.split("/", 1)[0] if "/" in selected else str(channel.get("protocol") or "openai")
        return DirectChatRoute(
            channel=name,
            provider=provider,
            litellm_model=selected,
            model=selected.split("/", 1)[-1],
            base_url=base_url,
            api_key=keys[0],
            extra_headers=dict(channel.get("extra_headers") or {}),
        )
    return None


def scheduled_direct_chat_routes(
    config: Any,
    *,
    requested_model: Optional[str] = None,
    at: Optional[datetime] = None,
) -> List[DirectChatRoute]:
    """Return an active direct route plus the other low-cost provider fallback."""

    if requested_model:
        raw = requested_model.strip()
        requested_wire = raw.split("/", 1)[-1]
        if "kimi" in requested_wire.lower():
            names = [DEFAULT_KIMI_CHANNEL, DEFAULT_DEEPSEEK_CHANNEL]
        elif "deepseek" in requested_wire.lower():
            names = [DEFAULT_DEEPSEEK_CHANNEL, DEFAULT_KIMI_CHANNEL]
        else:
            names = [active_provider_name(at), DEFAULT_DEEPSEEK_CHANNEL, DEFAULT_KIMI_CHANNEL]
    elif provider_schedule_enabled():
        active = active_provider_name(at)
        standby = DEFAULT_DEEPSEEK_CHANNEL if active == DEFAULT_KIMI_CHANNEL else DEFAULT_KIMI_CHANNEL
        names = [active, standby]
    else:
        names = [DEFAULT_DEEPSEEK_CHANNEL]

    routes: List[DirectChatRoute] = []
    for name in names:
        preferred = requested_model.strip() if requested_model and (
            (name == DEFAULT_KIMI_CHANNEL and "kimi" in requested_model.lower())
            or (name == DEFAULT_DEEPSEEK_CHANNEL and "deepseek" in requested_model.lower())
        ) else _preferred_model(name)
        route = _route_from_channel(config, channel_name=name, preferred_model=preferred)
        if route and route.litellm_model not in {item.litellm_model for item in routes}:
            routes.append(route)

    if routes:
        return routes

    # Backward-compatible DeepSeek-only path for tests and simple deployments
    # that have not migrated to LLM_CHANNELS yet.
    keys = list(getattr(config, "deepseek_api_keys", None) or [])
    if not keys:
        single = str(getattr(config, "deepseek_api_key", "") or "").strip()
        keys = [single] if single else []
    if not keys:
        return []
    model = (requested_model or os.getenv("ESSAY_ANALYSIS_MODEL") or DEFAULT_DEEPSEEK_MODEL).strip()
    wire_model = model.split("/", 1)[-1]
    return [DirectChatRoute(
        channel=DEFAULT_DEEPSEEK_CHANNEL,
        provider="deepseek",
        litellm_model=model if "/" in model else f"deepseek/{model}",
        model=wire_model,
        base_url=(os.getenv("ESSAY_ANALYSIS_DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/"),
        api_key=str(keys[0]),
    )]


def prepare_direct_chat_payload(
    route: DirectChatRoute,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a provider-compatible copy of one Chat Completions payload.

    Kimi Code requires its service-side sampling defaults and rejects clients
    that override temperature/top_p/n. It does accept ``thinking=disabled``;
    retaining that flag avoids paying for reasoning tokens on extraction jobs.
    """

    outbound = dict(payload)
    outbound["model"] = route.model
    if route.channel == DEFAULT_KIMI_CHANNEL:
        for key in ("temperature", "top_p", "n"):
            outbound.pop(key, None)
    return outbound


def direct_chat_headers(route: DirectChatRoute) -> Dict[str, str]:
    """Build honest, non-browser identity headers for a direct LLM request."""

    return {
        "Authorization": f"Bearer {route.api_key}",
        "Content-Type": "application/json",
        "User-Agent": "daily-stock-analysis/1.0",
        **route.extra_headers,
    }


def active_direct_model(config: Any, *, at: Optional[datetime] = None) -> str:
    routes = scheduled_direct_chat_routes(config, at=at)
    if routes:
        return routes[0].model
    return (os.getenv("ESSAY_ANALYSIS_MODEL") or DEFAULT_DEEPSEEK_MODEL).split("/", 1)[-1]


def route_status(config: Any, *, at: Optional[datetime] = None) -> Dict[str, Any]:
    routes = scheduled_direct_chat_routes(config, at=at)
    kimi_start = _bounded_day("LLM_PROVIDER_SCHEDULE_KIMI_START_DAY", 9)
    deepseek_start = _bounded_day("LLM_PROVIDER_SCHEDULE_DEEPSEEK_START_DAY", 24)
    return {
        "enabled": provider_schedule_enabled(),
        "active_provider": active_provider_name(at) if provider_schedule_enabled() else None,
        "models": [route.model for route in routes],
        "channels": [route.channel for route in routes],
        "timezone": str(_timezone()),
        "kimi_days": f"{kimi_start}-{deepseek_start - 1}",
        "deepseek_days": f"{deepseek_start}-月底、1-{kimi_start - 1}",
    }
