"""Offline tests of the real typed request and hostile response boundary."""
import json
from copy import deepcopy

import pytest
import requests

from src.services.jev_decisions_client import JevClientError, JevDecisionsClient


STATE = {"timeframe": "1min", "lookback_bars": 20,
         "normalized_bars": [{"open": 1, "high": 1.02, "low": 0.98, "close": 1, "volume": 1}] * 20,
         "indicators": {"rsi14": 0.5}}
RESPONSE = {"model": "Qwen/Qwen3.5-9B", "answers": {"action": {
    "type": "choice", "choice": "BUY", "confidence": 0.8,
    "probabilities": {"BUY": 0.8, "SELL": 0.1, "HOLD": 0.1}}},
    "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100, "cost": 0.0000042}}


class Response:
    def __init__(self, payload=None, body=None, status=200, content_type="application/json"):
        self.body = body if body is not None else json.dumps(payload or RESPONSE).encode()
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.closed = False
        self.history = []

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


def client(response=None, **kwargs):
    return JevDecisionsClient(api_key="test-secret-key", transport=lambda *args, **kw: response or Response(), **kwargs)


def test_typed_request_and_validated_usage_without_fallback():
    calls = []
    response = Response()

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return response

    result = JevDecisionsClient(api_key="test-secret-key", transport=transport).decide(STATE)
    assert len(calls) == 1
    url, options = calls[0]
    assert url.endswith("/v1/systemone")
    assert options["json"]["questions"]["action"]["type"] == "choice"
    assert set(options["json"]["questions"]["action"]["criteria"]) == {"BUY", "SELL", "HOLD"}
    assert options["json"]["state"] == STATE
    assert set(options["json"]) == {"state", "questions"}
    assert options["allow_redirects"] is False
    assert all(value > 0 for value in options["timeout"])
    assert result["action"] == "BUY" and result["usage"]["cost_is_estimate"] is False
    assert response.closed


@pytest.mark.parametrize("mutate", [
    lambda r: r["answers"]["action"].update(confidence=float("nan")),
    lambda r: r["answers"]["action"].update(confidence=True),
    lambda r: r["answers"]["action"].update(confidence=1.2),
    lambda r: r["answers"]["action"]["probabilities"].update(BUY=-0.1),
    lambda r: r["answers"]["action"]["probabilities"].pop("HOLD"),
    lambda r: r["answers"]["action"]["probabilities"].update(OTHER=0),
    lambda r: r["answers"]["action"]["probabilities"].update(BUY=0.1),
    lambda r: r["answers"]["action"].update(choice="SELL"),
    lambda r: r["answers"].update(extra={}),
    lambda r: r.update(model="other/model"),
    lambda r: r.update(error="SECRET_RESPONSE_BODY"),
    lambda r: r["usage"].update(input_tokens=True),
    lambda r: r["usage"].update(output_tokens=0.5),
    lambda r: r["usage"].update(cost=float("inf")),
    lambda r: r["usage"].update(total_tokens=101),
])
def test_invalid_typed_responses_never_become_signals(mutate):
    raw = deepcopy(RESPONSE)
    mutate(raw)
    with pytest.raises(JevClientError) as caught:
        client(Response(raw)).decide(STATE)
    assert "SECRET_RESPONSE_BODY" not in str(caught.value)
    assert "test-secret-key" not in str(caught.value)


def test_versioned_model_and_explicit_estimated_cost():
    raw = deepcopy(RESPONSE)
    raw["model"] += "-20260918"
    raw["usage"].pop("cost")
    result = client(Response(raw)).decide(STATE)
    assert result["usage"]["cost_is_estimate"] is True
    assert result["usage"]["cost"] == pytest.approx(0.0001)
    assert result["usage"]["cost_basis"] == "unverified_local_budget"
    assert result["usage"]["actual_cost"] is None


@pytest.mark.parametrize("response,code", [
    (Response(status=302), "http_302"),
    (Response(status=429), "http_429"),
    (Response(content_type="text/html"), "non_json_response"),
    (Response(body=b"secret server body"), "transport_or_response_error"),
    (Response(body=b"x" * 70000), "response_too_large"),
    (Response(body=b'{"test-secret-key":true}'), "credential_in_response"),
])
def test_http_size_and_credential_errors_are_sanitized(response, code):
    with pytest.raises(JevClientError) as caught:
        client(response).decide(STATE)
    assert caught.value.code == code
    assert "test-secret-key" not in str(caught.value)
    assert "secret server body" not in str(caught.value)
    assert response.closed


def test_transport_timeout_is_sanitized_without_retry():
    calls = []

    def timeout(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout("Authorization: test-secret-key and SECRET BODY")

    with pytest.raises(JevClientError) as caught:
        JevDecisionsClient(api_key="test-secret-key", transport=timeout).decide(STATE)
    assert calls == [1]
    assert caught.value.code == "timeout"
    assert "SECRET" not in str(caught.value)


def test_state_rejects_news_account_and_nonfinite_fields_before_transport():
    calls = []
    service = JevDecisionsClient(api_key="test-secret-key", transport=lambda *args, **kw: calls.append(1))
    for extra in ({"symbol": "000001"}, {"cash": 10000}, {"news": "buy now"}):
        with pytest.raises(JevClientError):
            service.decide(STATE | extra)
    bad = deepcopy(STATE)
    bad["normalized_bars"][0]["close"] = float("nan")
    with pytest.raises(JevClientError):
        service.decide(bad)
    assert calls == []


def test_env_configuration_is_validated(monkeypatch):
    monkeypatch.setenv("JEV_MODEL", "typesafe/test-model")
    monkeypatch.setenv("JEV_DECISIONS_ENDPOINT", "https://test.example/v1/systemone")
    service = client()
    assert service.model == "typesafe/test-model"
    assert service.endpoint == "https://test.example/v1/systemone"
    for timeout in (float("nan"), 0, float("inf")):
        with pytest.raises(JevClientError):
            client(timeout_seconds=timeout)
    with pytest.raises(JevClientError):
        client(endpoint="http://test.example/v1/systemone")


def test_duplicate_json_keys_are_rejected():
    body = json.dumps(RESPONSE).replace('"choice": "BUY"', '"choice": "SELL", "choice": "BUY"').encode()
    with pytest.raises(JevClientError, match="duplicate_json_key"):
        client(Response(body=body)).decide(STATE)


def test_legacy_provider_key_is_never_used(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "unrelated-legacy-secret")
    service = JevDecisionsClient()
    assert not service.configured
    with pytest.raises(JevClientError, match="missing_or_invalid_credential"):
        service.decide(STATE)


def test_legacy_endpoint_cannot_receive_worldseek_key():
    with pytest.raises(JevClientError, match="invalid_configuration"):
        client(endpoint="https://openrouter.ai/api/alpha/decisions")
