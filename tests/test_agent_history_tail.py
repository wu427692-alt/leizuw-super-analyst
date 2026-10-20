"""Long chat recovery returns the latest turns, without changing model context."""

import asyncio
from datetime import datetime, timedelta

import pytest
from starlette.requests import Request

from api.v1.endpoints.agent import get_chat_session_messages
from src.storage import ConversationMessage, DatabaseManager


@pytest.fixture
def history_db(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'chat-history.db'}")
    yield db
    DatabaseManager.reset_instance()


def _seed(db, session_id, count, *, same_timestamp=False):
    with db.get_session() as session:
        base = datetime(2026, 1, 1, 9)
        rows = [ConversationMessage(
            session_id=session_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"offline-{index}",
            created_at=base if same_timestamp else base + timedelta(seconds=index),
        ) for index in range(count)]
        session.add_all(rows)
        session.flush()
        ids = [str(row.id) for row in rows]
        session.commit()
        return ids


def test_long_session_recovers_last_answer_with_existing_array_shape(history_db):
    ids = _seed(history_db, "long-session", 102)
    messages = history_db.get_conversation_messages("long-session")

    assert len(messages) == 100
    assert [message["id"] for message in messages] == ids[2:]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "offline-101"
    assert set(messages[-1]) == {"id", "role", "content", "created_at"}
    assert messages[-1]["created_at"] == "2026-01-01T09:01:41"


def test_history_tail_uses_id_as_stable_tie_breaker(history_db):
    ids = _seed(history_db, "tied-session", 10, same_timestamp=True)

    for _ in range(3):
        assert [message["id"] for message in history_db.get_conversation_messages("tied-session", limit=4)] == ids[-4:]


def test_owner_scoped_api_restores_only_requested_users_latest_messages(history_db, monkeypatch):
    own_ids = _seed(history_db, "web_user_11:same-client-id", 102)
    _seed(history_db, "web_user_22:same-client-id", 102)
    monkeypatch.setattr("src.storage.get_db", lambda: history_db)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user_id = 11

    response = asyncio.run(get_chat_session_messages("same-client-id", request))

    assert response.session_id == "same-client-id"
    assert [row["id"] for row in response.messages] == own_ids[-100:]
    assert response.messages[-1]["content"] == "offline-101"


def test_bot_twenty_message_limit_and_separate_model_context_contract(history_db):
    ids = _seed(history_db, "bot-session", 25)
    assert [row["id"] for row in history_db.get_conversation_messages("bot-session", limit=20)] == ids[-20:]
    # LLM assembly has its own APIs; the browser recovery change must not
    # accidentally replace them or start loading only the first N messages.
    expected = [f"offline-{index}" for index in range(5, 25)]
    assert [row["content"] for row in history_db.get_conversation_history("bot-session")] == expected
    assert [row["content"] for row in history_db.get_visible_conversation_messages("bot-session", limit=20)] == expected
    assert history_db.get_conversation_messages("missing-session") == []
