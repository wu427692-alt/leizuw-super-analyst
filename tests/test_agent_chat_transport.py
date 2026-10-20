"""Real-thread chat identity and SSE transport checks; no model or provider I/O."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request
from starlette.requests import ClientDisconnect

import api.v1.endpoints.agent as agent
from src.repositories.portfolio_repo import PortfolioRepository
from src.request_identity import current_owner_id, reset_current_user_id, set_current_user_id
from src.storage import ConversationMessage, PortfolioAccount


def _request(user_id):
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.user_id = user_id
    return request


def _result():
    return SimpleNamespace(success=True, content="offline answer", error=None, total_steps=1)


def _events(chunks):
    return [json.loads(chunk[6:].strip()) for chunk in chunks if chunk.startswith("data: ")]


@contextmanager
def _owner(user_id):
    token = set_current_user_id(user_id)
    try:
        yield
    finally:
        reset_current_user_id(token)


@pytest.fixture
def offline(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("chat transport regression must not call upstream")

    monkeypatch.setattr("requests.sessions.Session.request", no_network)
    monkeypatch.setattr(agent, "get_config", lambda: SimpleNamespace(is_agent_available=lambda: True))
    monkeypatch.setattr(agent, "_enrich_with_unified_stock_context", lambda context, message: context)
    monkeypatch.setattr(agent, "_SSE_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(agent, "_SSE_IDLE_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(agent, "_SSE_CLEANUP_TIMEOUT_SECONDS", 0.02)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    PortfolioAccount.__table__.create(engine)
    ConversationMessage.__table__.create(engine)
    db = SimpleNamespace(get_session=lambda: Session(engine, expire_on_commit=False))
    with db.get_session() as session:
        session.add_all([
            PortfolioAccount(id=11, owner_id="user:11", name="test account A", market="cn", base_currency="CNY", is_active=True),
            PortfolioAccount(id=22, owner_id="user:22", name="test account B", market="cn", base_currency="CNY", is_active=True),
        ])
        session.commit()
    yield db
    engine.dispose()


@pytest.mark.parametrize("stream", [False, True])
def test_both_chat_endpoints_preserve_owner_in_reused_real_worker_threads(offline, monkeypatch, stream):
    seen, hydrated = [], []
    request_thread = threading.get_ident()

    def hydrate(context, message):
        hydrated.append(current_owner_id())
        return context

    def chat(**kwargs):
        # Real repository SQL demonstrates the consequence of identity loss:
        # an unowned repository would see both test accounts.
        accounts = PortfolioRepository(offline).list_accounts()
        seen.append((current_owner_id(), kwargs["session_id"], [a.id for a in accounts], threading.get_ident()))
        return _result()

    monkeypatch.setattr(agent, "_build_executor", lambda *args: SimpleNamespace(chat=chat))
    monkeypatch.setattr(agent, "_enrich_with_unified_stock_context", hydrate)

    async def exercise():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        for owner_id in (11, 22):
            with _owner(owner_id):
                body = agent.ChatRequest(message="offline ownership test", session_id="same-client-id")
                if stream:
                    response = await agent.agent_chat_stream(body, _request(owner_id))
                    chunks = [chunk async for chunk in response.body_iterator]
                    events = _events(chunks)
                    assert all(event["session_id"] == "same-client-id" for event in events)
                    assert events[-1]["type"] == "done"
                else:
                    response = await agent.agent_chat(body, _request(owner_id))
                    assert response.session_id == "same-client-id"
        assert await loop.run_in_executor(None, current_owner_id) is None

    asyncio.run(exercise())
    assert [(owner, sid, ids) for owner, sid, ids, _ in seen] == [
        ("user:11", "web_user_11:same-client-id", [11]),
        ("user:22", "web_user_22:same-client-id", [22]),
    ]
    assert hydrated == ["user:11", "user:22"]
    assert len({thread for _, _, _, thread in seen}) == 1
    assert seen[0][3] != request_thread


def test_sse_announces_generated_session_and_keeps_silent_model_connection_alive(offline, monkeypatch):
    release = threading.Event()

    def chat(**kwargs):
        assert release.wait(timeout=3)
        kwargs["progress_callback"]({"type": "tool_start", "tool": "get_daily_history"})
        return _result()

    monkeypatch.setattr(agent, "_build_executor", lambda *args: SimpleNamespace(chat=chat))

    async def exercise():
        with _owner(11):
            response = await agent.agent_chat_stream(agent.ChatRequest(message="offline heartbeat"), _request(11))
        iterator = response.body_iterator
        try:
            first = _events([await anext(iterator)])[0]
            assert first["type"] == "thinking" and first["session_id"]
            assert not first["session_id"].startswith("web_user_")
            assert await anext(iterator) == ": heartbeat\n\n"
            release.set()
            events = _events([chunk async for chunk in iterator])
            assert events[-1]["type"] == "done"
            assert all(event["session_id"] == first["session_id"] for event in events)
            assert events[0]["display_name"] == "获取历史K线"
        finally:
            release.set()
            await iterator.aclose()

    asyncio.run(exercise())


def test_heartbeat_does_not_reset_idle_deadline_or_claim_to_cancel_model(offline, monkeypatch):
    release, finished = threading.Event(), threading.Event()
    monkeypatch.setattr(agent, "_SSE_IDLE_TIMEOUT_SECONDS", 0.04)

    def chat(**kwargs):
        assert release.wait(timeout=3)
        finished.set()
        return _result()

    monkeypatch.setattr(agent, "_build_executor", lambda *args: SimpleNamespace(chat=chat))

    async def exercise():
        response = await agent.agent_chat_stream(agent.ChatRequest(message="offline timeout", session_id="idle-session"), _request(11))
        try:
            chunks = [chunk async for chunk in response.body_iterator]
            events = _events(chunks)
            assert any(chunk.startswith(": heartbeat") for chunk in chunks)
            assert events[-1]["type"] == "error"
            assert events[-1]["session_id"] == "idle-session"
            assert events[-1]["background_continues"] is True
            assert "后台分析可能仍在继续" in events[-1]["message"]
            assert not finished.is_set()
        finally:
            release.set()

    asyncio.run(exercise())
    assert finished.is_set()


def test_disconnect_stops_transport_events_but_final_answer_is_saved_under_original_owner(offline, monkeypatch):
    release, finished = threading.Event(), threading.Event()
    queues = []
    original_queue = asyncio.Queue

    class TrackedQueue(original_queue):
        puts = 0

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            queues.append(self)

        def put_nowait(self, item):
            self.puts += 1
            super().put_nowait(item)

    monkeypatch.setattr(agent.asyncio, "Queue", TrackedQueue)

    def chat(**kwargs):
        with offline.get_session() as session:
            session.add(ConversationMessage(session_id=kwargs["session_id"], role="user", content="offline question", created_at=datetime.now()))
            session.commit()
        assert release.wait(timeout=3)
        assert current_owner_id() == "user:11"
        kwargs["progress_callback"]({"type": "generating", "message": "late event"})
        with offline.get_session() as session:
            session.add(ConversationMessage(session_id=kwargs["session_id"], role="assistant", content="offline recovered answer", created_at=datetime.now()))
            session.commit()
        finished.set()
        return _result()

    monkeypatch.setattr(agent, "_build_executor", lambda *args: SimpleNamespace(chat=chat))

    async def exercise():
        with _owner(11):
            response = await agent.agent_chat_stream(agent.ChatRequest(message="offline disconnect", session_id="saved-session"), _request(11))
        try:
            assert _events([await anext(response.body_iterator)])[0]["type"] == "thinking"
            await response.body_iterator.aclose()
            assert not finished.is_set()
            puts_at_close = queues[0].puts
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
            await asyncio.sleep(0.02)
            assert queues[0].puts == puts_at_close
        finally:
            release.set()

    asyncio.run(exercise())
    with offline.get_session() as session:
        rows = session.execute(select(ConversationMessage).order_by(ConversationMessage.id)).scalars().all()
        assert [(row.session_id, row.role) for row in rows] == [
            ("web_user_11:saved-session", "user"), ("web_user_11:saved-session", "assistant"),
        ]


def test_executor_error_is_terminal_not_a_background_continuation(offline, monkeypatch):
    monkeypatch.setattr(agent, "_build_executor", lambda *args: SimpleNamespace(chat=Mock(side_effect=RuntimeError("offline execution failure"))))

    async def exercise():
        response = await agent.agent_chat_stream(agent.ChatRequest(message="offline error", session_id="failed-session"), _request(11))
        events = _events([chunk async for chunk in response.body_iterator])
        assert events[-1]["type"] == "error"
        assert events[-1]["session_id"] == "failed-session"
        assert events[-1]["background_continues"] is False

    asyncio.run(exercise())


@pytest.mark.parametrize("spec", ["2.0", "2.4"])
def test_real_asgi_disconnect_closes_generator_and_discards_late_delivery(offline, monkeypatch, spec):
    release, finished = threading.Event(), threading.Event()
    queues = []
    original_queue = asyncio.Queue

    class TrackedQueue(original_queue):
        puts = 0

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            queues.append(self)

        def put_nowait(self, item):
            self.puts += 1
            super().put_nowait(item)

    monkeypatch.setattr(agent.asyncio, "Queue", TrackedQueue)

    def chat(**kwargs):
        assert release.wait(timeout=3)
        kwargs["progress_callback"]({"type": "generating", "message": "late ASGI event"})
        finished.set()
        return _result()

    monkeypatch.setattr(agent, "_build_executor", lambda *args: SimpleNamespace(chat=chat))

    async def exercise():
        disconnected = asyncio.Event()

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and b'"type": "thinking"' in message.get("body", b""):
                if spec == "2.4":
                    raise OSError("offline peer closed transport")
                disconnected.set()

        response = await agent.agent_chat_stream(agent.ChatRequest(message="offline ASGI", session_id="asgi-session"), _request(11))
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": spec}}
        try:
            if spec == "2.4":
                with pytest.raises(ClientDisconnect):
                    await response(scope, receive, send)
            else:
                await response(scope, receive, send)
            # No explicit aclose in the test before this assertion: exercise
            # the actual StreamingResponse transport lifecycle, not a stand-in.
            assert response.body_iterator.ag_frame is None
            puts_at_disconnect = queues[0].puts
            assert not finished.is_set()
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
            await asyncio.sleep(0.02)
            assert queues[0].puts == puts_at_disconnect
        finally:
            release.set()
            await response.body_iterator.aclose()

    asyncio.run(exercise())


def test_queued_executor_emits_heartbeat_then_keeps_terminal_event(offline, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(agent, "_build_executor", lambda *args: SimpleNamespace(chat=lambda **kwargs: _result()))

    async def exercise():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        blocker = loop.run_in_executor(None, release.wait, 3)
        response = await agent.agent_chat_stream(agent.ChatRequest(message="offline queue", session_id="queued-session"), _request(11))
        try:
            assert await anext(response.body_iterator) == ": heartbeat\n\n"
            release.set()
            assert await blocker
            events = _events([chunk async for chunk in response.body_iterator])
            assert [event["type"] for event in events] == ["thinking", "done"]
            assert all(event["session_id"] == "queued-session" for event in events)
        finally:
            release.set()
            await response.body_iterator.aclose()

    asyncio.run(exercise())
