"""Durable paper-control authorization, idempotency and resume boundaries."""
from copy import deepcopy
import json
import time
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.jev_live_t_controller import LiveTController, _atomic


@pytest.fixture(autouse=True)
def archived_controller_algorithm_only(monkeypatch):
    """Keep algorithm tests synthetic; the production availability is tested separately."""
    monkeypatch.setattr('src.services.jev_live_t_controller.is_feature_available', lambda _: True)


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.005)


def fake_runner(calls):
    def runner(config, **kwargs):
        calls.append(kwargs)
        run = deepcopy(kwargs['resume_run']) or {
            'run_id': str(uuid4()), 'created_at': '2026-09-21T05:00:00+00:00',
            'updated_at': '2026-09-21T05:00:00+00:00', 'config': {},
            'usage': {'cost_usd': 0, 'cost_is_estimate': False}, 'account': {'cash': 4000000},
        }
        run.update(status='running', stop_reason=None, message='synthetic controlled worker')
        run['usage']['cost_usd'] += .01
        checkpoint = {'saved_cash': run['account']['cash']}
        kwargs['checkpoint_sink'](run, checkpoint)
        while not kwargs['stop_requested']():
            time.sleep(.005)
        run.update(status='stopped', stop_reason='manual_stop')
        kwargs['checkpoint_sink'](run, checkpoint)
        return run
    return runner


def test_get_never_creates_account_or_starts_paid_work_and_other_owner_cannot_control(tmp_path):
    calls = []
    control = LiveTController(tmp_path, runner=fake_runner(calls))
    assert control.view('admin')['phase'] == 'stopped'
    assert control.view('user:1')['can_control'] is False
    with pytest.raises(PermissionError):
        control.set_enabled('user:1', True)
    assert calls == [] and not control.root.exists()


def test_duplicate_enable_is_one_worker_and_stop_resume_keeps_account_budget_and_run(tmp_path):
    calls = []
    control = LiveTController(tmp_path, runner=fake_runner(calls))
    control.configure_prior_cost(.1)
    try:
        control.set_enabled('admin', True)
        control.set_enabled('admin', True)
        wait_for(lambda: control.view('admin')['run_id'] is not None)
        first = control.view('admin')
        assert len(calls) == 1
        assert calls[0]['max_cost_usd'] == 9.9
        assert calls[0]['duration_seconds'] is None and calls[0]['max_model_requests'] is None
        assert first['usage']['cost_usd'] == pytest.approx(.11)
        control.set_enabled('admin', False)
        wait_for(lambda: not control.thread.is_alive())
        assert control.view('admin')['phase'] == 'stopped'
        control.set_enabled('admin', True)
        wait_for(lambda: len(calls) == 2 and control.view('admin')['usage']['cost_usd'] > .11)
        second = control.view('admin')
        assert second['run_id'] == first['run_id']
        assert calls[1]['resume_run']['account']['cash'] == 4000000
        assert calls[1]['engine_checkpoint'] == {'saved_cash': 4000000}
        assert second['usage']['cost_usd'] == pytest.approx(.12)
        with pytest.raises(ValueError, match='already_started'):
            control.configure_prior_cost(0)
    finally:
        control.set_enabled('admin', False)
        control.close()
    assert control.checkpoint_path.stat().st_mode & 0o777 == 0o600


def test_lifespan_recovery_only_restarts_explicitly_enabled_account(tmp_path):
    calls = []
    original = LiveTController(tmp_path, runner=fake_runner(calls))
    original.set_enabled('admin', True)
    wait_for(lambda: original.view('admin')['run_id'] is not None)
    run_id = original.view('admin')['run_id']
    original.close()  # service shutdown preserves the user's enabled intent
    assert original.view('admin')['enabled'] is True
    recovered = LiveTController(tmp_path, runner=fake_runner(calls))
    try:
        assert len(calls) == 1
        recovered.view('admin')
        assert len(calls) == 1
        recovered.recover()
        wait_for(lambda: len(calls) == 2)
        assert recovered.view('admin')['run_id'] == run_id
    finally:
        recovered.set_enabled('admin', False)
        recovered.close()


def test_failure_disables_without_hidden_retry_and_budget_cannot_reset(tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError('sensitive provider detail')
    control = LiveTController(tmp_path, runner=fail)
    control.set_enabled('admin', True)
    wait_for(lambda: not control.thread.is_alive())
    value = control.view('admin')
    assert value['enabled'] is False and value['phase'] == 'failed'
    assert 'sensitive' not in str(value)
    control.recover()
    assert not control.thread.is_alive()
    state = control._state()
    _atomic(control.checkpoint_path, {'schema': 'jev-continuous-checkpoint-v1', 'owner': 'admin',
        'run': {'run_id': str(uuid4()), 'usage': {'cost_usd': 10}}, 'checkpoint': {}})
    with pytest.raises(ValueError, match='budget_exhausted'):
        control.set_enabled('admin', True)
    assert control._state() == state


def test_api_control_requires_auth_owner_strict_boolean_and_same_origin(tmp_path, monkeypatch):
    from api.v1.endpoints import jev_live_t as endpoint
    calls = []
    control = LiveTController(tmp_path, runner=fake_runner(calls))
    monkeypatch.setattr(endpoint, 'controller', lambda: control)
    app = FastAPI()
    app.include_router(endpoint.router, prefix='/live')
    client = TestClient(app)  # no lifespan or background startup required for GET
    assert client.get('/live/control').status_code == 401
    assert client.post('/live/control', json={'enabled': True}).status_code == 401
    app.dependency_overrides[endpoint.owner] = lambda: 'user:1'
    assert client.get('/live/control').json()['can_control'] is False
    assert client.post('/live/control', json={'enabled': True}).status_code == 403
    app.dependency_overrides[endpoint.owner] = lambda: 'admin'
    assert client.post('/live/control', json={'enabled': 'false'}).status_code == 422
    assert client.post('/live/control', json={'enabled': False, 'cash': 1}).status_code == 422
    assert client.post('/live/control', json={'enabled': True}, headers={'Origin': 'https://foreign.example'}).status_code == 403
    assert calls == []
    assert client.post('/live/control', json={'enabled': False}, headers={'Origin': 'http://testserver'}).status_code == 200
    assert calls == []


def test_missing_checkpoint_or_control_never_reinitializes_existing_account(tmp_path):
    calls = []
    control = LiveTController(tmp_path, runner=fake_runner(calls))
    control.set_enabled('admin', True)
    wait_for(lambda: control.view('admin')['run_id'] is not None)
    control.set_enabled('admin', False)
    wait_for(lambda: not control.thread.is_alive())
    checkpoint = control.checkpoint_path.read_bytes()
    control.checkpoint_path.unlink()
    assert control.view('admin')['phase'] == 'failed'
    with pytest.raises(ValueError, match='checkpoint_missing'):
        control.set_enabled('admin', True)
    with pytest.raises(ValueError, match='checkpoint_missing'):
        control.configure_prior_cost(0)
    control.recover()
    assert len(calls) == 1
    control.checkpoint_path.write_bytes(checkpoint)
    control.state_path.unlink()
    assert control.view('admin')['phase'] == 'failed'
    with pytest.raises(ValueError, match='control_missing'):
        control.set_enabled('admin', True)
    control.recover()
    assert len(calls) == 1


def test_insufficient_reservation_balance_is_exhausted_without_restart(tmp_path):
    def runner(config, **kwargs):
        run={'run_id': str(uuid4()), 'status': 'stopped', 'stop_reason': 'model_budget_reserved',
             'usage': {'cost_usd': 9.999, 'cost_is_estimate': False}, 'config': {},
             'updated_at': '2026-09-21T05:00:00+00:00'}
        kwargs['checkpoint_sink'](run, {})
        return run
    control=LiveTController(tmp_path, runner=runner)
    control.set_enabled('admin', True)
    wait_for(lambda: not control.thread.is_alive())
    assert control.view('admin')['phase']=='budget_exhausted'
    control.set_enabled('admin', False)
    with pytest.raises(ValueError, match='budget_exhausted'):
        control.set_enabled('admin', True)
