"""Withdrawn Jev entrypoints fail closed without provider or ledger access."""

import asyncio
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.feature_availability import is_feature_available, unavailable_feature_for_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    from api.app import create_app
    from api.middlewares import auth

    monkeypatch.setenv('USER_ACCESS_ENABLED', 'true')
    monkeypatch.setattr(auth, 'is_auth_enabled', lambda: True)
    monkeypatch.setattr(auth, 'verify_session', lambda value: value == 'offline-test-admin')
    accounts = Mock()
    accounts.account_for_session.return_value = None
    monkeypatch.setattr(auth, 'UserAccountService', lambda: accounts)
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text('shared application')
    (static / 'assets').mkdir()
    (static / 'assets' / 'ordinary.js').write_text('// unrelated public asset')
    (static / 'jev-backtest' / 'assets').mkdir(parents=True)
    (static / 'jev-backtest' / 'jev-backtest.html').write_text('OLD JEV HTML')
    (static / 'jev-backtest' / 'assets' / 'entry.js').write_text('OLD JEV JS')
    (static / 'old-entry.html').symlink_to(static / 'jev-backtest' / 'jev-backtest.html')
    (static / 'other.html').write_text('ordinary document')
    return TestClient(create_app(static_dir=static), follow_redirects=False)


@pytest.mark.parametrize('prefix', ['jev-backtest', 'jev-convertible-t', 'jev-live-t'])
@pytest.mark.parametrize('method', ['GET', 'POST', 'HEAD', 'DELETE'])
def test_all_old_api_entrypoints_keep_auth_then_return_410(client, prefix, method):
    path = f'/api/v1/{prefix}/tasks/old-task'
    assert client.request(method, path).status_code == 401
    from api.middlewares.auth import COOKIE_NAME
    client.cookies.set(COOKIE_NAME, 'offline-test-admin')
    response = client.request(method, path)
    assert response.status_code == 410
    assert 'no-store' in response.headers['cache-control']
    if method != 'HEAD':
        assert response.json()['error'] == 'feature_unavailable'


def test_router_aggregation_does_not_include_jev_lifespan_or_api():
    from api.v1.router import router
    assert all('jev-' not in getattr(route, 'path', '') for route in router.routes)


@pytest.mark.parametrize('path', [
    '/%2e/jev-backtest/jev-backtest.html',
    '/foo/%2e%2e/jev-backtest/jev-backtest.html',
    '/%2e/jev-backtest/assets/entry.js',
    '/jev-backtest//assets///entry.js',
    '/%2fjev-backtest/jev-backtest.html',
    '/jev-backtest%252fjev-backtest.html',
    '/old-entry.html',
])
def test_encoded_dot_segments_and_resolved_aliases_cannot_serve_old_bundle(client, path):
    from api.middlewares.auth import COOKIE_NAME
    assert client.get(path).status_code == 307
    client.cookies.set(COOKIE_NAME, 'offline-test-admin')
    response = client.get(path)
    assert response.status_code == 410
    assert 'OLD JEV' not in response.text
    assert 'no-store' in response.headers['cache-control']


@pytest.mark.parametrize('path', [
    '/%5cjev-backtest/jev-backtest.html',
    '/jev-backtest%255cjev-backtest.html',
    '/jev-backtest%25252fjev-backtest.html',
    '/assets/%2e%2e/jev-backtest/jev-backtest.html',
])
def test_ambiguous_percent_levels_and_backslashes_never_return_jev_bytes(client, path):
    from api.middlewares.auth import COOKIE_NAME
    client.cookies.set(COOKIE_NAME, 'offline-test-admin')
    response = client.get(path)
    assert response.status_code in {200, 404, 410}
    assert 'OLD JEV' not in response.text


def test_normal_static_and_unrelated_spa_paths_keep_existing_behavior(client):
    from api.middlewares.auth import COOKIE_NAME
    response = client.get('/assets/ordinary.js')
    assert response.status_code == 200 and response.text == '// unrelated public asset'
    assert client.get('/other.html').status_code == 307
    client.cookies.set(COOKIE_NAME, 'offline-test-admin')
    assert client.get('/other.html').text == 'ordinary document'
    assert client.get('/some-new-route').text == 'shared application'
    assert client.get('/jev-backtest-unrelated').text == 'shared application'


@pytest.mark.parametrize('path', [
    '/api/v1/%2e/jev-live-t/control',
    '/api/v1/other/%2e%2e/jev-backtest/tasks',
    '/api/v1/jev-convertible-t//tasks',
])
def test_canonical_api_paths_do_not_weaken_auth(client, path):
    from api.middlewares.auth import COOKIE_NAME
    assert client.get(path).status_code == 401
    client.cookies.set(COOKIE_NAME, 'offline-test-admin')
    response = client.get(path)
    assert response.status_code == 410
    assert response.json()['error'] == 'feature_unavailable'


def test_unavailable_guard_matches_segments_not_unrelated_names():
    assert is_feature_available('jev') is False
    for path in ['/jev-backtest', '/jev-backtest/', '/jev-backtest-assets/assets/a.js',
                 '/api/v1/jev-backtest', '/api/v1/jev-live-t/control']:
        assert unavailable_feature_for_path(path) == 'jev'
    for path in ['/app', '/jev-backtest-other', '/api/v1/jev-live-tools', '/api/v1/health']:
        assert unavailable_feature_for_path(path) is None


def test_lifespan_and_recovery_never_load_saved_ledger_or_start_worker(tmp_path, monkeypatch):
    from src.services import jev_live_t_controller as module
    control = module.LiveTController(tmp_path, runner=Mock(side_effect=AssertionError('must not run')))
    monkeypatch.setattr(control, '_state', Mock(side_effect=AssertionError('must not read')))
    control.recover()
    assert control.thread is None and not control.root.exists()
    assert control._stop_requested() is True
    with pytest.raises(ValueError, match='feature_unavailable'):
        control.set_enabled('admin', True)
    monkeypatch.setattr(module, 'controller', Mock(side_effect=AssertionError('must not construct')))

    async def inspect():
        async with module.live_t_lifespan(None):
            pass
    asyncio.run(inspect())
    assert not control.root.exists()


def test_withdrawal_signals_stop_and_keeps_existing_paper_ledger(tmp_path, monkeypatch):
    from copy import deepcopy
    from threading import Event
    from uuid import uuid4
    from src.services import jev_live_t_controller as module

    available = {'value': True}
    monkeypatch.setattr(module, 'is_feature_available', lambda _: available['value'])
    began, stopped = Event(), Event()
    calls = []

    def runner(config, **kwargs):
        calls.append(True)
        run = {'run_id': str(uuid4()), 'status': 'running', 'config': {},
               'usage': {'cost_usd': .01}, 'account': {'cash': 123456},
               'created_at': '2026-10-18T00:00:00+00:00',
               'updated_at': '2026-10-18T00:00:00+00:00'}
        kwargs['checkpoint_sink'](deepcopy(run), {'cash': 123456})
        began.set()
        assert stopped.wait(2)
        assert kwargs['stop_requested']() is True
        run.update(status='stopped', stop_reason='manual_stop')
        kwargs['checkpoint_sink'](run, {'cash': 123456})
        return run

    control = module.LiveTController(tmp_path, runner=runner)
    try:
        control.set_enabled('admin', True)
        assert began.wait(2)
        available['value'] = False
        stopped.set()
        control.thread.join(timeout=2)
        assert not control.thread.is_alive()
        saved = control.checkpoint_path.read_bytes()
        assert control._joint()['run']['account']['cash'] == 123456
        control.recover()
        assert calls == [True] and control.checkpoint_path.read_bytes() == saved
        with pytest.raises(ValueError, match='feature_unavailable'):
            control.set_enabled('admin', True)
        assert control.checkpoint_path.read_bytes() == saved
    finally:
        stopped.set()
        control.close()
