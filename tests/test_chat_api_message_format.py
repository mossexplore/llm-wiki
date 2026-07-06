from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_wiki import chat_store
from llm_wiki.backend.api import chat as chat_api


@pytest.fixture(autouse=True)
def _clear_chat_store():
    chat_store.clear_sessions()
    yield
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
