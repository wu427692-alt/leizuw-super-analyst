"""Deterministic research-workspace ownership and worker-context regressions."""
from contextvars import copy_context
from pathlib import Path
from types import SimpleNamespace
import threading

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from src.request_identity import set_current_user_id, reset_current_user_id, get_current_user_id
from src.services import workspace as ws


@pytest.fixture
def identity():
    token = set_current_user_id(7)
    try:
        yield
    finally:
        reset_current_user_id(token)


def test_same_client_workspace_keeps_accounts_separate(tmp_path, identity):
    a = ws.workspace_directory(tmp_path, 'same-id', create=True)
    (a / 'note.txt').write_text('user seven')
    token = set_current_user_id(8)
    try:
        b = ws.workspace_directory(tmp_path, 'same-id', create=True)
        assert a != b
        with pytest.raises(FileNotFoundError):
            ws.workspace_file(tmp_path, 'same-id', 'note.txt')
        (b / 'note.txt').write_text('user eight')
        assert ws.workspace_file(tmp_path, 'same-id', 'note.txt').read_text() == 'user eight'
    finally:
        reset_current_user_id(token)
    assert ws.workspace_file(tmp_path, 'same-id', 'note.txt').read_text() == 'user seven'


@pytest.mark.parametrize('value', ['..', '.', '../other', 'a/b', 'a\\b', '%2e%2e', '/absolute'])
def test_client_ids_do_not_select_filesystem_paths(tmp_path, identity, value):
    with pytest.raises(ValueError):
        ws.workspace_directory(tmp_path, value)


def test_ownerless_old_files_are_not_assigned_to_regular_user(tmp_path, identity):
    old = tmp_path / 'old-session'
    old.mkdir()
    (old / 'note.txt').write_text('old archive')
    with pytest.raises(FileNotFoundError):
        ws.workspace_file(tmp_path, 'old-session', 'note.txt')
    token = set_current_user_id(0)
    try:
        assert ws.workspace_file(tmp_path, 'old-session', 'note.txt').read_text() == 'old archive'
    finally:
        reset_current_user_id(token)


def test_no_identity_and_symlinks_are_rejected(tmp_path):
    token = set_current_user_id(None)
    try:
        with pytest.raises(PermissionError):
            ws.workspace_directory(tmp_path, 'test')
    finally:
        reset_current_user_id(token)
    token = set_current_user_id(7)
    try:
        folder = ws.workspace_directory(tmp_path, 'test', create=True)
        other = tmp_path / 'private.txt'
        other.write_text('not downloadable')
        (folder / 'link.txt').symlink_to(other)
        with pytest.raises(FileNotFoundError):
            ws.workspace_file(tmp_path, 'test', 'link.txt')
    finally:
        reset_current_user_id(token)


def test_private_file_routes_use_same_owner_and_private_cache(tmp_path, monkeypatch, identity):
    from api.v1.endpoints import financial_data
    from src.services import report_pack_service as packs
    monkeypatch.setattr(packs, 'DOWNLOAD_DIR', tmp_path)
    folder = ws.workspace_directory(tmp_path, 'session1', create=True)
    (folder / 'note.txt').write_text('owned content')
    app = FastAPI()
    app.include_router(financial_data.router)
    client = TestClient(app)
    result = client.get('/workspace-files?workspace=session1')
    assert result.status_code == 200
    assert result.json()['items'][0]['filename'] == 'note.txt'
    result = client.get('/downloads/session1/note.txt')
    assert result.text == 'owned content'
    assert result.headers['cache-control'] == 'private, no-store'
    assert result.headers['cdn-cache-control'] == 'no-store'
    token = set_current_user_id(8)
    try:
        assert client.get('/downloads/session1/note.txt').status_code == 404
        assert client.get('/workspace-files?workspace=session1').json()['items'] == []
    finally:
        reset_current_user_id(token)
    assert client.get('/workspace-files?workspace=..').status_code == 404
    assert client.get('/downloads/%2E%2E/note.txt').status_code == 404


def test_command_restores_context_on_success_and_failure(monkeypatch, identity):
    from api.v1.endpoints import command
    token = ws.set_workspace('outer')
    request = SimpleNamespace(state=SimpleNamespace(user_id=8))
    observed = []
    def handle(*args):
        observed.append((get_current_user_id(), ws.current_workspace()))
        return {'reply':'ok'}
    monkeypatch.setattr(command, '_handle_chat_in_workspace', handle)
    body = command.CommandChatRequest(messages=[{'role':'user','content':'hello'}], workspace_id='inner')
    try:
        assert command._handle_chat(body, request)['reply'] == 'ok'
        assert observed == [(8,'inner')]
        assert (get_current_user_id(), ws.current_workspace()) == (7,'outer')
        def fail(*args):
            raise RuntimeError('expected')
        monkeypatch.setattr(command, '_handle_chat_in_workspace', fail)
        with pytest.raises(RuntimeError):
            command._handle_chat(body, request)
        assert (get_current_user_id(), ws.current_workspace()) == (7,'outer')
    finally:
        ws.reset_workspace(token)


def test_pause_sessions_are_not_shared_by_accounts(identity):
    from api.v1.endpoints import command
    first = command._get_pause_event('same-session')
    first.clear()
    token = set_current_user_id(8)
    try:
        second = command._get_pause_event('same-session')
        assert second is not first and second.is_set()
    finally:
        reset_current_user_id(token)
        first.set()


def test_report_background_context_and_task_reads_are_owner_scoped(tmp_path, monkeypatch, identity):
    from src.services import report_pack_service as packs
    monkeypatch.setattr(packs,'DOWNLOAD_DIR',tmp_path/'downloads')
    monkeypatch.setattr(packs,'CACHE_DIR',tmp_path/'cache')
    ready = threading.Event()
    captured = []
    def package(self, **kwargs):
        captured.append((get_current_user_id(),ws.current_workspace(),kwargs['workspace']))
        ready.set()
        return {'ok':True}
    monkeypatch.setattr(packs.ReportPackService,'package',package)
    token = ws.set_workspace('origin')
    try:
        service = packs.ReportPackService()
        task = service.start_package(symbol='603306')
        assert ready.wait(3)
        assert captured == [(7,'origin','origin')]
        assert service.get_task(task['task_id']) is not None
        other = set_current_user_id(8)
        try:
            assert service.get_task(task['task_id']) is None
        finally:
            reset_current_user_id(other)
    finally:
        ws.reset_workspace(token)


def test_cache_never_reuses_another_accounts_response(identity):
    from src.services import ttl_cache
    ttl_cache.invalidate()
    assert ttl_cache.cached('test',300,lambda:'first') == 'first'
    other = set_current_user_id(8)
    try:
        assert ttl_cache.cached('test',300,lambda:'second') == 'second'
    finally:
        reset_current_user_id(other)
    assert ttl_cache.cached('test',300,lambda:'wrong') == 'first'
    ttl_cache.invalidate('test')
    assert ttl_cache.cached('test',300,lambda:'fresh') == 'fresh'


def test_task_status_wrapper_rejects_non_task_routes(identity,monkeypatch):
    from api.v1.endpoints import command
    request = SimpleNamespace(headers={})
    calls=[]
    monkeypatch.setattr(command,'_internal_call',lambda *a,**kw:calls.append(a) or {'ok':True})
    with pytest.raises(HTTPException):
        command.command_task(request,task_id='task-1',poll_path='/api/v1/user-auth/status')
    assert not calls
    assert command.command_task(request,task_id='task-1',poll_path='/api/v1/data-acquisition/tasks/task-1')['ok']


def test_parallel_and_stream_workers_keep_the_request_owner(monkeypatch, identity):
    import asyncio
    import json
    from api.v1.endpoints import command
    token = ws.set_workspace('parallel-origin')
    observed = []
    def agent(*args):
        observed.append((get_current_user_id(),ws.current_workspace()))
        return {'reply':'done'}
    monkeypatch.setattr(command,'_run_agent',agent)
    try:
        command._run_parallel([{'agent':'data','instruction':'one'},{'agent':'research','instruction':'two'}], 'question', '', [])
        assert observed == [(7,'parallel-origin'),(7,'parallel-origin')]
    finally:
        ws.reset_workspace(token)
    monkeypatch.setattr(command,'_consume_quota',lambda request:None)
    monkeypatch.setattr(command,'_handle_chat_in_workspace',lambda *args:{'owner':get_current_user_id(),'workspace':ws.current_workspace()})
    original_event = command._get_pause_event('stream-test')
    original_event.clear()
    try:
        body=command.CommandChatRequest(messages=[{'role':'user','content':'hello'}],session_id='stream-test',workspace_id='stream-origin')
        response=command.command_chat_stream(body,SimpleNamespace(state=SimpleNamespace(user_id=8)))
        async def consume():
            return [json.loads(x) async for x in response.body_iterator]
        events=asyncio.run(consume())
        assert events[-1]['payload'] == {'owner':8,'workspace':'stream-origin'}
        assert not original_event.is_set()
        assert get_current_user_id() == 7
    finally:
        original_event.set()


def test_stream_quota_exhaustion_is_429_before_response_starts(monkeypatch, identity):
    from api.v1.endpoints import command
    def exhausted(*args):
        raise command.QuotaExceeded('quota exhausted')
    monkeypatch.setattr(command,'UserQuotaService',lambda:SimpleNamespace(consume=exhausted))
    body=command.CommandChatRequest(messages=[{'role':'user','content':'hello'}])
    with pytest.raises(HTTPException) as exc:
        command.command_chat_stream(body,SimpleNamespace(state=SimpleNamespace(user_id=7)))
    assert exc.value.status_code == 429
