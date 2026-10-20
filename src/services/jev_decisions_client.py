"""Strict WorldSeek SystemOne transport for anonymous, normalized OHLCV states.

This is the typed Decisions API, not chat completions. It never falls back to
another model/provider, retries automatically, or accesses a desktop keychain.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import threading
import weakref
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_ENDPOINT = "https://jev.worldseek-ai.com/v1/systemone"
DEFAULT_MODEL = "Qwen/Qwen3.5-9B"
ACTIONS = ("BUY", "SELL", "HOLD")
INDICATORS = frozenset({"return_1", "return_5", "return_10", "return_20",
                        "close_to_ma5", "close_to_ma20", "close_to_ma60", "rsi14",
                        "atr14_to_close", "volatility20", "close_to_trailing_high", "volume_to_ma20"})


class JevClientError(RuntimeError):
    """Public error with a fixed safe code; never retains upstream exception text."""

    def __init__(self, code: str, usage: dict | None = None):
        self.code = code
        self.usage = usage
        super().__init__(f"Jev decision unavailable ({code})")


def _finite(value: Any, low: float = 0, high: float = math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevClientError("invalid_numeric_value")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise JevClientError("invalid_numeric_value") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise JevClientError("invalid_numeric_value")
    return result


def _tokens(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53 - 1:
        raise JevClientError("invalid_usage")
    return value


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        raise JevClientError("invalid_configuration") from None


def _validate_state(features: Mapping[str, Any]) -> dict:
    if not isinstance(features, Mapping) or set(features) != {
            "timeframe", "lookback_bars", "normalized_bars", "indicators"}:
        raise JevClientError("invalid_price_volume_state")
    if features["timeframe"] not in ("1min", "5min", "15min", "1d"):
        raise JevClientError("invalid_price_volume_state")
    count = _tokens(features["lookback_bars"])
    rows = features["normalized_bars"]
    indicators = features["indicators"]
    if (not 20 <= count <= 252 or not isinstance(rows, (list, tuple)) or len(rows) != count
            or not isinstance(indicators, Mapping) or not indicators or not set(indicators) <= INDICATORS):
        raise JevClientError("invalid_price_volume_state")
    clean_rows = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"open", "high", "low", "close", "volume"}:
            raise JevClientError("invalid_price_volume_state")
        clean = {name: _finite(row[name], 0 if name == "volume" else 1e-12, 1e12)
                 for name in ("open", "high", "low", "close", "volume")}
        if not clean["low"] <= min(clean["open"], clean["close"]) <= max(
                clean["open"], clean["close"]) <= clean["high"]:
            raise JevClientError("invalid_price_volume_state")
        clean_rows.append(clean)
    return {"timeframe": features["timeframe"], "lookback_bars": count,
            "normalized_bars": clean_rows,
            "indicators": {name: _finite(value, -1e12, 1e12) for name, value in indicators.items()}}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JevClientError("duplicate_json_key")
        result[key] = value
    return result


def no_retry_session():
    """Private keep-alive pool; paid requests are never automatically retried."""
    session = requests.Session()
    session.trust_env = False
    retry = Retry(total=0, connect=0, read=0, status=0, redirect=0, other=0)
    for scheme in ("http://", "https://"):
        session.mount(scheme, HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2))
    return session


class JevDecisionsClient:
    def __init__(self, *, api_key: str | None = None, transport: Callable | None = None,
                 fetch: Callable | None = None, endpoint: str | None = None,
                 model: str | None = None, timeout_seconds: float | None = None,
                 max_response_bytes: int = 65536, input_price_per_million: float | None = None):
        self.endpoint = endpoint or os.getenv("JEV_DECISIONS_ENDPOINT", DEFAULT_ENDPOINT)
        self.model = model or os.getenv("JEV_MODEL", DEFAULT_MODEL)
        self._api_key = api_key if api_key is not None else os.getenv("JEV_API_KEY", "")
        self.timeout_seconds = _finite(timeout_seconds if timeout_seconds is not None
                                       else _env_number("JEV_TIMEOUT_SECONDS", 20), 0.1, 120)
        # A local budgeting coefficient, not verified WorldSeek pricing.
        self.input_price_per_million = _finite(input_price_per_million if input_price_per_million is not None
                                               else _env_number("JEV_LOCAL_BUDGET_RATE_PER_MILLION", 1.0), 1e-9, 1000)
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or not 1024 <= max_response_bytes <= 1048576:
            raise JevClientError("invalid_configuration")
        self.max_response_bytes = max_response_bytes
        parsed = urlsplit(self.endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != "/v1/systemone"):
            raise JevClientError("invalid_configuration")
        if not isinstance(self.model, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,160}", self.model):
            raise JevClientError("invalid_configuration")
        if transport is not None and fetch is not None:
            raise JevClientError("invalid_configuration")
        self._session_local = threading.local()
        self._session_lock = threading.Lock()
        self._sessions = weakref.WeakSet()
        self._closed = False
        self._transport = transport or fetch or self._pooled_post

    def _pooled_post(self, *args, **kwargs):
        # Relay handler threads do not share mutable requests.Session state.
        with self._session_lock:
            if self._closed:
                raise JevClientError("invalid_configuration")
            session = getattr(self._session_local, "session", None)
            if session is None:
                session = no_retry_session()
                self._session_local.session = session
                self._sessions.add(session)
        return session.post(*args, **kwargs)

    def close(self):
        with self._session_lock:
            self._closed = True
            sessions = list(self._sessions)
            self._sessions.clear()
        for session in sessions:
            session.close()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _usage(self, raw: Any) -> dict:
        if not isinstance(raw, Mapping):
            raise JevClientError("invalid_usage")
        result = {"input_tokens": _tokens(raw.get("input_tokens")),
                  "output_tokens": _tokens(raw.get("output_tokens"))}
        if "total_tokens" in raw:
            result["total_tokens"] = _tokens(raw["total_tokens"])
            if result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
                raise JevClientError("invalid_usage")
        if "cost" in raw:
            result.update(cost=_finite(raw["cost"]), cost_is_estimate=False, cost_basis="provider_reported", actual_cost=_finite(raw["cost"]))
        else:
            result.update(cost=result["input_tokens"] * self.input_price_per_million / 1000000,
                          cost_is_estimate=True, cost_basis="unverified_local_budget", actual_cost=None)
        return result

    def _normalize(self, raw: Any) -> dict:
        if not isinstance(raw, Mapping):
            raise JevClientError("invalid_response")
        usage = self._usage(raw.get("usage"))
        model = raw.get("model")
        if (not isinstance(model, str) or not re.fullmatch(re.escape(self.model) + r"(?:-\d{8})?", model)
                or any(key in raw for key in ("error", "errors", "error_type")) or raw.get("success") is False):
            raise JevClientError("invalid_response", usage)
        answers = raw.get("answers")
        if not isinstance(answers, Mapping) or set(answers) != {"action"}:
            raise JevClientError("invalid_answer_keys", usage)
        answer = answers["action"]
        if not isinstance(answer, Mapping) or answer.get("type") != "choice" or answer.get("choice") not in ACTIONS:
            raise JevClientError("invalid_choice", usage)
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, Mapping) or set(probabilities) != set(ACTIONS):
            raise JevClientError("invalid_probability_keys", usage)
        probabilities = {key: _finite(probabilities[key], 0, 1) for key in ACTIONS}
        if (abs(sum(probabilities.values()) - 1) > 0.02 + 1e-9
                or probabilities[answer["choice"]] + 1e-6 < max(probabilities.values())):
            raise JevClientError("invalid_probabilities", usage)
        confidence = _finite(answer.get("confidence"), 0, 1)
        return {"action": answer["choice"], "status": "ok", "confidence": confidence,
                "probabilities": probabilities, "labels": ["price_volume_only"],
                "usage": usage, "model": model}

    def _prepare_state(self, features):
        return _validate_state(features)

    def _build_questions(self):
        return {"action": {
            "type": "choice",
            "instructions": (
                "Classify the trailing price-volume pattern for a short-horizon long-only research signal. "
                "Use only the normalized OHLCV bars and indicators supplied. BUY means positive direction, "
                "SELL means negative direction, and HOLD means no clear directional edge. "
                "The state has no account or identity. Do not infer news, company facts, dates, fees, "
                "or positions. Choice probabilities are model classifications, not calibrated return probabilities."),
            "criteria": {"BUY": "Positive price-volume direction supports entry or remaining long",
                         "SELL": "Negative price-volume direction supports exit or remaining flat",
                         "HOLD": "No sufficiently clear directional price-volume pattern"}}}

    def decide(self, features: Mapping[str, Any]) -> dict:
        state = self._prepare_state(features)
        key = self._api_key
        if not isinstance(key, str) or not key or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise JevClientError("missing_or_invalid_credential")
        payload = {"state": state, "questions": self._build_questions()}
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"))
        if len(body.encode("utf-8")) > 96000:
            raise JevClientError("request_too_large")
        if key in body:
            raise JevClientError("credential_in_state")
        response = None
        started = time.monotonic()
        try:
            response = self._transport(
                self.endpoint, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload, timeout=(min(5, self.timeout_seconds), self.timeout_seconds),
                allow_redirects=False, stream=True)
            status = getattr(response, "status_code", None)
            if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
                raise JevClientError("invalid_http_response")
            if not 200 <= status < 300 or getattr(response, "history", None):
                raise JevClientError(f"http_{status}")
            headers = response.headers
            content_type = headers.get("Content-Type", headers.get("content-type", ""))
            if not isinstance(content_type, str) or not re.match(r"application/json\b", content_type, re.I):
                raise JevClientError("non_json_response")
            content_length = headers.get("Content-Length", headers.get("content-length"))
            if content_length is not None:
                try:
                    if int(content_length) > self.max_response_bytes:
                        raise JevClientError("response_too_large")
                except (TypeError, ValueError):
                    raise JevClientError("invalid_http_response") from None
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=1024):
                if time.monotonic() - started > self.timeout_seconds:
                    raise JevClientError("timeout")
                if not isinstance(chunk, bytes):
                    raise JevClientError("invalid_http_response")
                size += len(chunk)
                if size > self.max_response_bytes:
                    raise JevClientError("response_too_large")
                chunks.append(chunk)
            if time.monotonic() - started > self.timeout_seconds:
                raise JevClientError("timeout")
            raw_body = b"".join(chunks).decode("utf-8")
            if key in raw_body:
                raise JevClientError("credential_in_response")
            raw = json.loads(raw_body, object_pairs_hook=_unique_object, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            result = self._normalize(raw)
            result["latency_ms"] = round((time.monotonic() - started) * 1000)
            return result
        except JevClientError:
            raise
        except requests.Timeout:
            raise JevClientError("timeout") from None
        except Exception:
            raise JevClientError("transport_or_response_error") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    __call__ = decide
