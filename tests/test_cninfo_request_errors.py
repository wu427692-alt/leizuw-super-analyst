"""Exercise real requests error semantics without contacting CNInfo."""

import json
import time
from datetime import datetime, timedelta, timezone
from datetime import date
from email.utils import format_datetime

import pytest
import requests

from src.services.cninfo_announcement_service import (
    CNINFO_QUERY_URL, CninfoAnnouncementError, CninfoAnnouncementService,
)


def response(status, payload=None, retry_after=None):
    result = requests.Response()
    result.status_code = status
    result.url = CNINFO_QUERY_URL
    result._content = json.dumps(payload or {}).encode()
    if retry_after is not None:
        result.headers['Retry-After'] = retry_after
    return result


class Session:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def sleeps(monkeypatch):
    durations = []
    monkeypatch.setattr('src.services.cninfo_announcement_service.time.sleep', durations.append)
    monkeypatch.setattr('src.services.cninfo_announcement_service.random.uniform', lambda *args: 0.1)
    return durations


@pytest.mark.parametrize('status', [400, 401, 403, 404, 422, 501])
def test_non_transient_http_errors_not_retried_or_leaked(status, sleeps):
    result = response(status)
    result._content = b'<html>token=private-cookie-and-upstream-body</html>'
    service = CninfoAnnouncementService(session=Session(result))
    with pytest.raises(CninfoAnnouncementError) as caught:
        service._post_json(CNINFO_QUERY_URL, {'stock': 'private'})
    error = caught.value
    assert error.status_code == status
    assert error.endpoint == '公告查询'
    assert error.attempts == 1
    assert f'HTTP {status}' in str(error)
    assert 'private' not in str(error)
    assert isinstance(error.__cause__, requests.HTTPError)
    assert sleeps == []


@pytest.mark.parametrize('status', [408, 425, 429, 500, 502, 503, 504])
def test_transient_http_retries_can_recover(status, sleeps):
    session = Session(response(status), response(200, {'announcements': []}))
    service = CninfoAnnouncementService(session=session)
    assert service._post_json(CNINFO_QUERY_URL, {}) == {'announcements': []}
    assert session.calls == 2
    assert sleeps == [1.1]


def test_gateway_timeout_preserves_endpoint_status_and_attempts(sleeps):
    session = Session(*[response(504) for _ in range(3)])
    with pytest.raises(CninfoAnnouncementError) as caught:
        CninfoAnnouncementService(session=session)._post_json(CNINFO_QUERY_URL, {})
    assert caught.value.status_code == 504
    assert caught.value.attempts == 3
    assert sleeps == [1.1, 2.1]


def test_long_retry_after_is_deferred_without_early_retry(sleeps):
    session = Session(response(429, retry_after='120'))
    with pytest.raises(CninfoAnnouncementError) as caught:
        CninfoAnnouncementService(session=session)._post_json(CNINFO_QUERY_URL, {})
    assert caught.value.retry_after_seconds == 120
    assert session.calls == 1
    assert sleeps == []


def test_short_retry_after_is_respected(sleeps):
    session = Session(response(503, retry_after='3'), response(200))
    CninfoAnnouncementService(session=session)._post_json(CNINFO_QUERY_URL, {})
    assert sleeps == [3]


def test_retry_after_cannot_be_shortened_to_fit_deadline(sleeps):
    session = Session(response(503, retry_after='3'))
    with pytest.raises(CninfoAnnouncementError) as caught:
        CninfoAnnouncementService(session=session)._post_json(
            CNINFO_QUERY_URL, {}, deadline_monotonic=time.monotonic() + 2,
        )
    assert caught.value.status_code == 503
    assert caught.value.retry_after_seconds == 3
    assert session.calls == 1
    assert sleeps == []


def test_request_budget_exhaustion_retains_last_upstream_error(sleeps):
    session = Session(response(504))
    budget = {'remaining': 1, 'used': 0}
    with pytest.raises(CninfoAnnouncementError) as caught:
        CninfoAnnouncementService(session=session)._post_json(CNINFO_QUERY_URL, {}, request_budget=budget)
    assert caught.value.status_code == 504
    assert budget == {'remaining': 0, 'used': 1}
    assert sleeps == []


@pytest.mark.parametrize('error', [requests.ReadTimeout('sensitive'), requests.ConnectionError('sensitive')])
def test_transport_errors_are_retried_without_leaking_details(error, sleeps):
    session = Session(error, response(200))
    assert CninfoAnnouncementService(session=session)._post_json(CNINFO_QUERY_URL, {}) == {}
    assert sleeps == [1.1]


def test_invalid_json_is_not_a_success_or_retry_loop(sleeps):
    result = response(200)
    result._content = b'<html>not an announcement list</html>'
    with pytest.raises(CninfoAnnouncementError):
        CninfoAnnouncementService(session=Session(result))._post_json(CNINFO_QUERY_URL, {})
    assert sleeps == []


def test_ssl_validation_errors_are_not_retried(sleeps):
    session = Session(requests.exceptions.SSLError('private certificate detail'))
    with pytest.raises(CninfoAnnouncementError) as caught:
        CninfoAnnouncementService(session=session)._post_json(CNINFO_QUERY_URL, {})
    assert caught.value.attempts == 1
    assert 'private' not in str(caught.value)
    assert sleeps == []


@pytest.mark.parametrize('value', [None, '', 'invalid', 'nan', 'inf', '1e309'])
def test_invalid_retry_after_is_ignored(value):
    assert CninfoAnnouncementService._retry_after_seconds(value) is None


def test_http_date_retry_after():
    value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    assert 118 <= CninfoAnnouncementService._retry_after_seconds(value) <= 120


def test_expired_retry_after_is_zero():
    assert CninfoAnnouncementService._retry_after_seconds('-3') == 0


@pytest.mark.parametrize('payload', [{}, {'announcements': 'unavailable'}])
def test_malformed_success_payload_does_not_advance_empty_window(payload):
    service = CninfoAnnouncementService(session=Session(response(200, payload)))
    with pytest.raises(CninfoAnnouncementError, match='格式异常'):
        service.fetch(start_date=date(2026, 9, 1), end_date=date(2026, 9, 8))


def test_explicit_empty_announcement_list_is_valid():
    service = CninfoAnnouncementService(session=Session(response(200, {'announcements': None})))
    assert service.fetch(start_date=date(2026, 9, 1), end_date=date(2026, 9, 8)) == []
