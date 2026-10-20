"""Event protocol must retain its strict boundary through the loopback relay."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from src.services.jev_cb_t0_engine import CBT0Config, build_cb_state
from src.services.jev_decisions_client import JevClientError
from src.services.jev_relay_client import JevEventRelayClient
from src.services.jev_runtime_relay import RelayApplication
from tests.test_jev_runtime_relay import FakeClient, Response, RESULT


def state():
    start = datetime.fromisoformat("2025-09-16T09:30:00-04:00")
    rows = [{"time": (start + timedelta(seconds=i)).isoformat(), "sequence": i,
             "last_price": 100, "bid_price": 99.99, "ask_price": 100.01,
             "bid_volume": 100, "ask_volume": 200, "trade_volume": 10,
             "quantity_unit": "shares", "volume_kind": "incremental",
             "event_type": "trade", "trading_status": "trading"} for i in range(2)]
    return build_cb_state(rows, CBT0Config(lookback_events=2, instrument_type="us_equity",
                          currency="USD", lot_size=1, exchange_timezone="America/New_York",
                          simulation_account="unlevered_margin", price_tick=.01))


class EventClient(FakeClient):
    def decide(self, value):
        result = super().decide(value)
        result["labels"] = ["price_volume_factors"]
        return result


def test_event_relay_roundtrip_selects_event_client_without_credential_or_minute_fallback():
    minute, event = FakeClient(), EventClient()
    app = RelayApplication(minute, event_client=event)
    calls = []

    def transport(method, url, **kwargs):
        calls.append(kwargs)
        code, result = app.decide(kwargs["json"])
        return Response(result, code)

    client = JevEventRelayClient("http://127.0.0.1:19876", transport=transport)
    answer = client.decide(state())
    assert answer == {**deepcopy(RESULT), "labels": ["price_volume_factors"]}
    assert len(event.calls) == 1 and minute.calls == []
    assert client.cache_version == "cb-t0-factors-v1"
    assert all("Authorization" not in request["headers"] for request in calls)


def test_event_relay_refuses_missing_event_client_and_unknown_or_sensitive_state():
    minute, event = FakeClient(), EventClient()
    assert RelayApplication(minute).decide({"state": state()})[0] == 503
    app = RelayApplication(minute, event_client=event)
    for patch in ({"cash": 200000}, {"schema": "unknown"}, {"symbol": "NVDA"}):
        assert app.decide({"state": {**state(), **patch}})[0] == 400
    assert not minute.calls and not event.calls


def test_event_adapter_rejects_minute_response_label():
    client = JevEventRelayClient("http://127.0.0.1:19876",
                                transport=lambda *args, **kwargs: Response(RESULT))
    with pytest.raises(JevClientError):
        client.decide(state())
