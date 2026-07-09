from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_wiki import chat_store
from llm_wiki.backend.api import chat as chat_api


@pytest.fixture(autouse=True)
def _clear_chat_store():
    chat_api.ACTIVE_CHAT_STREAMS.clear()
    chat_store.clear_sessions()
    yield
    chat_api.ACTIVE_CHAT_STREAMS.clear()
    chat_store.clear_sessions()


@pytest.fixture()
def client(monkeypatch):
    captured = {}

    class FakeRetriever:
        def retrieve(self, _text):
            return {"source": "llm", "mode": "none", "elapsed_ms": 1, "refs": [], "context": []}

    def fake_stream_messages_compatible(messages, message_format):
        captured["messages"] = messages
        captured["stream_message_format"] = message_format
        yield "ok"

    monkeypatch.setattr(chat_api, "RETRIEVER", FakeRetriever())
    monkeypatch.setattr(chat_api.agent, "stream_messages_compatible", fake_stream_messages_compatible)

    app = FastAPI()
    app.include_router(chat_api.router)
    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.captured = captured
    return test_client


def _events(response):
    text = response.text
    if "data:" in text:
        events = []
        for block in text.split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
            if data:
                events.append(json.loads(data))
        return events
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_chat_send_message_defaults_to_openai_dict_messages(client):
    session = chat_store.create_session("s")

    response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"content": "你好"})

    assert response.status_code == 200
    assert client.captured["messages"][1] == {"role": "user", "content": "你好"}
    assert client.captured["stream_message_format"] == "dict"
    done = [event for event in _events(response) if event["type"] == "done"][0]
    assert done["message_format"] == "dict"
    events = _events(response)
    message_ids = {event.get("message_id") for event in events}
    assert len(message_ids) == 1
    assert next(iter(message_ids))


def test_chat_send_message_accepts_langchain_tuple_messages(client):
    session = chat_store.create_session("s")

    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages",
        json={"content": "你好", "message_format": "langchain"},
    )

    assert response.status_code == 200
    assert client.captured["messages"][1] == ("user", "你好")
    assert client.captured["stream_message_format"] == "tuple"
    done = [event for event in _events(response) if event["type"] == "done"][0]
    assert done["message_format"] == "tuple"
    events = _events(response)
    message_ids = {event.get("message_id") for event in events}
    assert len(message_ids) == 1
    assert next(iter(message_ids))


def test_chat_send_message_renames_default_session_title(client):
    session = chat_store.create_session("新会话")

    response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"content": "第一条用户问题"})

    assert response.status_code == 200
    [stored] = [item for item in chat_store.list_sessions() if item["id"] == session["id"]]
    assert stored["title"] == "第一条用户问题"


def test_chat_send_message_keeps_custom_session_title(client):
    session = chat_store.create_session("我的自定义标题")

    response = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"content": "第一条用户问题"})

    assert response.status_code == 200
    [stored] = [item for item in chat_store.list_sessions() if item["id"] == session["id"]]
    assert stored["title"] == "我的自定义标题"


def test_stop_message_is_idempotent_by_message_id(client):
    session = chat_store.create_session("s")
    active = chat_api.ActiveChatStream(
        session_id=session["id"],
        user_id=None,
        message_id="assistant-1",
        request_id="req",
        started=0,
        acc="partial",
    )
    chat_api.register_active_stream(active)

    first = client.post(
        f"/api/chat/sessions/{session['id']}/messages/stop",
        json={"message_id": "assistant-1"},
    )
    second = client.post(
        f"/api/chat/sessions/{session['id']}/messages/stop",
        json={"message_id": "assistant-1"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["result"]["data"]["message"]["id"] == "assistant-1"
    assert second.json()["result"]["data"]["deduped"] is True
    messages = [m for m in chat_store.get_messages(session["id"]) if m["role"] == "assistant"]
    assert len(messages) == 1
    chat_api.unregister_active_stream("assistant-1", active)


def test_stop_message_requires_message_id(client):
    session = chat_store.create_session("s")

    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages/stop",
        json={"content": "partial", "elapsed_ms": 1},
    )

    assert response.status_code == 422


def test_stop_message_ignores_redundant_fields(client):
    session = chat_store.create_session("s", user_id="user-1")
    active = chat_api.ActiveChatStream(
        session_id=session["id"],
        user_id="user-1",
        message_id="assistant-full",
        request_id="req",
        started=0,
        acc="server partial",
        source="wiki",
        mode="exact",
        refs=[{"id": "case-1", "title": "Case 1"}],
        retrieval_ms=20,
        model_wait_ms=30,
        first_delta_ms=40,
        total_ms=120,
        message_count=6,
        prompt_chars=512,
    )
    chat_api.register_active_stream(active)
    request_body = {
        "content": "client partial",
        "message_id": "assistant-full",
        "user_id": "user-1",
        "answer_source": "llm",
        "retrieval_mode": "none",
        "refs": [],
        "elapsed_ms": 1,
        "retrieval_ms": 1,
        "model_wait_ms": 1,
        "first_delta_ms": 1,
        "total_ms": 1,
        "message_count": 1,
        "prompt_chars": 1,
    }

    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages/stop",
        json=request_body,
    )

    assert response.status_code == 200
    message = response.json()["result"]["data"]["message"]
    assert message["content"] == "server partial"
    assert message["answer_source"] == "wiki"
    assert message["retrieval_mode"] == "exact"
    assert message["refs"] == [{"id": "case-1", "title": "Case 1"}]
    assert message["elapsed_ms"] == 120
    assert message["retrieval_ms"] == 20
    assert message["model_wait_ms"] == 30
    assert message["first_delta_ms"] == 40
    assert message["total_ms"] == 120
    assert message["message_count"] == 6
    assert message["prompt_chars"] == 512
    chat_api.unregister_active_stream("assistant-full", active)


def test_stop_message_accepts_message_id_only(client):
    session = chat_store.create_session("s", user_id="user-1")
    active = chat_api.ActiveChatStream(
        session_id=session["id"],
        user_id="user-1",
        message_id="assistant-minimal",
        request_id="req",
        started=0,
        acc="server partial",
        source="wiki",
        mode="exact",
        refs=[{"id": "case-1", "title": "Case 1"}],
        retrieval_ms=20,
        model_wait_ms=30,
        first_delta_ms=40,
        total_ms=120,
        message_count=6,
        prompt_chars=512,
    )
    chat_api.register_active_stream(active)

    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages/stop",
        json={"message_id": "assistant-minimal"},
    )

    assert response.status_code == 200
    message = response.json()["result"]["data"]["message"]
    assert message["id"] == "assistant-minimal"
    assert message["user_id"] == "user-1"
    assert message["content"] == "server partial"
    assert message["answer_source"] == "wiki"
    assert message["retrieval_mode"] == "exact"
    assert message["refs"] == [{"id": "case-1", "title": "Case 1"}]
    assert message["elapsed_ms"] == 120
    assert message["retrieval_ms"] == 20
    assert message["model_wait_ms"] == 30
    assert message["first_delta_ms"] == 40
    assert message["total_ms"] == 120
    assert message["message_count"] == 6
    assert message["prompt_chars"] == 512
    chat_api.unregister_active_stream("assistant-minimal", active)


def test_stop_message_accepts_matching_user_id(client):
    session = chat_store.create_session("s", user_id="user-1")
    active = chat_api.ActiveChatStream(
        session_id=session["id"],
        user_id="user-1",
        message_id="assistant-user",
        request_id="req",
        started=0,
        acc="server partial",
    )
    chat_api.register_active_stream(active)

    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages/stop",
        json={"message_id": "assistant-user", "user_id": "user-1"},
    )

    assert response.status_code == 200
    message = response.json()["result"]["data"]["message"]
    assert message["id"] == "assistant-user"
    assert message["user_id"] == "user-1"
    chat_api.unregister_active_stream("assistant-user", active)


def test_stop_message_rejects_mismatched_user_id(client):
    session = chat_store.create_session("s", user_id="user-1")
    active = chat_api.ActiveChatStream(
        session_id=session["id"],
        user_id="user-1",
        message_id="assistant-user",
        request_id="req",
        started=0,
        acc="server partial",
    )
    chat_api.register_active_stream(active)

    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages/stop",
        json={"message_id": "assistant-user", "user_id": "user-2"},
    )

    assert response.status_code == 404
    chat_api.unregister_active_stream("assistant-user", active)


def test_stop_message_prefers_active_stream_snapshot(client):
    session = chat_store.create_session("s")
    active = chat_api.ActiveChatStream(
        session_id=session["id"],
        user_id=None,
        message_id="assistant-active",
        request_id="req",
        started=0,
        acc="server partial",
        source="wiki",
        mode="exact",
        retrieval_ms=2,
    )
    chat_api.register_active_stream(active)

    response = client.post(
        f"/api/chat/sessions/{session['id']}/messages/stop",
        json={"message_id": "assistant-active"},
    )

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["message"]["content"] == "server partial"
    assert data["message"]["answer_source"] == "wiki"
    assert data["message"]["retrieval_mode"] == "exact"
    assert active.cancel_event.is_set()
    chat_api.unregister_active_stream("assistant-active", active)
