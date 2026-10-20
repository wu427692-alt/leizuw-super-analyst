"""Loopback-only adapter for the in-memory Jev runtime relay.

``endpoint`` remains the real upstream identity for paid-cache compatibility.
No upstream credential is accepted, retrieved, or returned by this adapter.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Mapping
from urllib.parse import urlsplit

import requests

from src.services.jev_decisions_client import (
    JevClientError, JevDecisionsClient, _finite, _tokens, _unique_object, _validate_state, no_retry_session,
)

PROTOCOL = "jev-runtime-relay-v1"
MAX_BODY_BYTES = 96000
MAX_RESPONSE_BYTES = 65536
SAFE_ERROR_CODES = frozenset({
    "invalid_numeric_value", "invalid_usage", "invalid_response", "invalid_answer_keys",
    "invalid_choice", "invalid_probability_keys", "invalid_probabilities", "invalid_http_response",
    "non_json_response", "response_too_large", "timeout", "transport_or_response_error",
    "credential_in_response", "request_too_large", "credential_in_state", "duplicate_json_key",
    "invalid_price_volume_state", "missing_or_invalid_credential", "invalid_configuration",
    "relay_busy", "relay_internal_error", "relay_invalid_request", "relay_unavailable",
    "invalid_live_context_state",
})


def safe_error_code(code: Any) -> str:
    if isinstance(code, str) and (code in SAFE_ERROR_CODES or re.fullmatch(r"http_[1-5]\d{2}", code)):
        return code
    return "relay_unavailable"


def validate_usage(raw: Any) -> dict:
    required = {"input_tokens", "output_tokens", "cost", "cost_is_estimate"}
    if (not isinstance(raw, Mapping) or not required <= set(raw)
            or not set(raw) <= required | {"total_tokens", "cost_basis", "actual_cost"}
            or not isinstance(raw["cost_is_estimate"], bool)):
        raise JevClientError("invalid_usage")
    result = {"input_tokens": _tokens(raw["input_tokens"]), "output_tokens": _tokens(raw["output_tokens"]),
              "cost": _finite(raw["cost"]), "cost_is_estimate": raw["cost_is_estimate"]}
    if "actual_cost" in raw:
        value = raw["actual_cost"]
        if raw["cost_is_estimate"] and value is not None:
            raise JevClientError("invalid_usage")
        result["actual_cost"] = None if value is None else _finite(value)
    if "cost_basis" in raw:
        expected = "unverified_local_budget" if raw["cost_is_estimate"] else "provider_reported"
        if raw["cost_basis"] != expected:
            raise JevClientError("invalid_usage")
        result["cost_basis"] = raw["cost_basis"]
    if "total_tokens" in raw:
        result["total_tokens"] = _tokens(raw["total_tokens"])
        if result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
            raise JevClientError("invalid_usage")
    return result


def validate_relay_result(raw: Any, model: str, *, label="price_volume_only") -> dict:
    required = {"action", "status", "confidence", "probabilities", "labels", "usage", "model", "latency_ms"}
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise JevClientError("invalid_response")
    usage = validate_usage(raw["usage"])
    if (raw["status"] != "ok" or not isinstance(raw["model"], str)
            or not re.fullmatch(re.escape(model) + r"(?:-\d{8})?", raw["model"])
            or label not in ("price_volume_only", "price_volume_factors", "live_context") or raw["labels"] != [label]):
        raise JevClientError("invalid_response", usage)
    answer = raw["action"]
    probabilities = raw["probabilities"]
    if (answer not in ("BUY", "SELL", "HOLD") or not isinstance(probabilities, Mapping)
            or set(probabilities) != {"BUY", "SELL", "HOLD"}):
        raise JevClientError("invalid_choice", usage)
    probabilities = {key: _finite(value, 0, 1) for key, value in probabilities.items()}
    if (abs(sum(probabilities.values()) - 1) > .02 + 1e-9
            or probabilities[answer] + 1e-6 < max(probabilities.values())):
        raise JevClientError("invalid_probabilities", usage)
    return {"action": answer, "status": "ok", "confidence": _finite(raw["confidence"], 0, 1),
            "probabilities": probabilities, "labels": [label], "usage": usage,
            "model": raw["model"], "latency_ms": _tokens(raw["latency_ms"])}


def decode_json(data: bytes) -> Any:
    def reject_constant(_):
        raise JevClientError("invalid_numeric_value")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=reject_constant)
    except JevClientError:
        raise
    except Exception:
        raise JevClientError("invalid_response") from None


class JevRelayClient:
    def __init__(self, relay_url: str | None = None, *, transport=None, model=None, endpoint=None,
                 timeout_seconds=None, input_price_per_million=None):
        url = relay_url if relay_url is not None else os.getenv("JEV_RELAY_URL", "")
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                     and parsed.port is not None and 1 <= parsed.port <= 65535
                     and not parsed.username and not parsed.password and not parsed.query
                     and not parsed.fragment and parsed.path in ("", "/"))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise JevClientError("invalid_configuration")
        self.relay_url = url.rstrip("/")
        # Reuse configuration validation only. Empty injected key prevents any
        # credential lookup, and this client is never used to send a request.
        config = JevDecisionsClient(api_key="", model=model, endpoint=endpoint,
                                    timeout_seconds=timeout_seconds,
                                    input_price_per_million=input_price_per_million)
        self.model = config.model
        self.endpoint = config.endpoint
        self.input_price_per_million = config.input_price_per_million
        self.timeout_seconds = config.timeout_seconds + 5
        self._session = None
        if transport is None:
            self._session = no_retry_session()
            self._transport = self._session.request
        else:
            self._transport = transport

    def close(self):
        if self._session is not None:
            self._session.close()

    def _request(self, method: str, path: str, *, payload=None, timeout=None) -> dict:
        response = None
        deadline_seconds = timeout or self.timeout_seconds
        started = time.monotonic()
        try:
            response = self._transport(method, self.relay_url + path, json=payload,
                                       headers={"Accept": "application/json"},
                                       timeout=(min(2, deadline_seconds), deadline_seconds),
                                       allow_redirects=False, stream=True)
            status = response.status_code
            if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
                raise JevClientError("invalid_http_response")
            if getattr(response, "history", None) or 300 <= status < 400:
                raise JevClientError("relay_unavailable")
            content_type = response.headers.get("Content-Type", response.headers.get("content-type", ""))
            if not isinstance(content_type, str) or not re.match(r"application/json\b", content_type, re.I):
                raise JevClientError("non_json_response")
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=1024):
                if time.monotonic() - started > deadline_seconds:
                    raise JevClientError("timeout")
                if not isinstance(chunk, bytes):
                    raise JevClientError("invalid_response")
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise JevClientError("response_too_large")
                chunks.append(chunk)
            raw = decode_json(b"".join(chunks))
            if time.monotonic() - started > deadline_seconds:
                raise JevClientError("timeout")
            if not 200 <= status < 300:
                if (isinstance(raw, dict) and set(raw) == {"error"} and isinstance(raw["error"], dict)
                        and set(raw["error"]) <= {"code", "usage"} and "code" in raw["error"]):
                    error = raw["error"]
                    usage = validate_usage(error["usage"]) if error.get("usage") is not None else None
                    raise JevClientError(safe_error_code(error["code"]), usage)
                raise JevClientError("relay_unavailable")
            if not isinstance(raw, dict):
                raise JevClientError("invalid_response")
            return raw
        except JevClientError:
            raise
        except requests.Timeout:
            raise JevClientError("timeout") from None
        except Exception:
            raise JevClientError("relay_unavailable") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def health(self) -> dict:
        raw = self._request("GET", "/health", timeout=2)
        if (set(raw) != {"ready", "protocol", "model", "endpoint", "input_price_per_million"}
                or raw["ready"] is not True or raw["protocol"] != PROTOCOL
                or raw["model"] != self.model or raw["endpoint"] != self.endpoint
                or _finite(raw["input_price_per_million"], 0, 1000) != self.input_price_per_million):
            raise JevClientError("invalid_configuration")
        return raw

    @property
    def configured(self) -> bool:
        try:
            self.health()
            return True
        except JevClientError:
            return False

    def decide(self, state: Mapping[str, Any]) -> dict:
        payload = {"state": _validate_state(state)}
        if len(json.dumps(payload, allow_nan=False).encode("utf-8")) > MAX_BODY_BYTES:
            raise JevClientError("request_too_large")
        return validate_relay_result(self._request("POST", "/decide", payload=payload), self.model)

    __call__ = decide


class JevEventRelayClient(JevRelayClient):
    cache_version = "cb-t0-factors-v1"

    def decide(self, state):
        from src.services.jev_cb_decisions_client import validate_cb_state
        payload = {"state": validate_cb_state(state)}
        if len(json.dumps(payload, allow_nan=False).encode("utf-8")) > MAX_BODY_BYTES:
            raise JevClientError("request_too_large")
        return validate_relay_result(self._request("POST", "/decide", payload=payload),
                                     self.model, label="price_volume_factors")

    __call__ = decide


class JevLiveContextRelayClient(JevRelayClient):
    cache_version = "jev-live-context-v1"

    def decide(self, state):
        from src.services.jev_live_context_client import LABEL, validate_live_context_state
        payload = {"state": validate_live_context_state(state)}
        if len(json.dumps(payload, allow_nan=False).encode("utf-8")) > MAX_BODY_BYTES:
            raise JevClientError("request_too_large")
        return validate_relay_result(self._request("POST", "/decide", payload=payload),
                                     self.model, label=LABEL)

    __call__ = decide
