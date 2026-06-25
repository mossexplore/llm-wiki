"""会话读取/删除端点改 POST 后的契约测试(部署环境强制 POST)。

覆盖 list / messages-list / delete / clear 四个端点:无请求体可用、
请求体 user_id 能正确按用户隔离。
"""

from __future__ import annotations

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
def client():
    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app, raise_server_exceptions=False)


def _data(response):
    return response.json()["result"]["data"]


def test_list_sessions_post_without_body(client):
    chat_store.create_session("s1", user_id="u1")

    response = client.post("/api/chat/sessions/list")

    assert response.status_code == 200
    assert len(_data(response)["items"]) == 1


def test_list_sessions_post_scopes_by_user_id_in_body(client):
    chat_store.create_session("s1", user_id="u1")

    assert _data(client.post("/api/chat/sessions/list", json={"user_id": "u1"}))["items"]
    assert _data(client.post("/api/chat/sessions/list", json={"user_id": "other"}))["items"] == []


def test_messages_list_post(client):
    session = chat_store.create_session("s1", user_id="u1")

    response = client.post(f"/api/chat/sessions/{session['id']}/messages/list")

    assert response.status_code == 200
    assert _data(response)["items"] == []


def test_delete_session_post_enforces_user_scope(client):
    session = chat_store.create_session("s1", user_id="u1")
    sid = session["id"]

    # 归属不符 → 按不存在处理
    assert client.post(f"/api/chat/sessions/{sid}/delete", json={"user_id": "other"}).status_code == 404
    # 归属正确 → 删除成功
    ok = client.post(f"/api/chat/sessions/{sid}/delete", json={"user_id": "u1"})
    assert ok.status_code == 200
    assert _data(ok)["ok"] is True


def test_clear_sessions_post(client):
    chat_store.create_session("s1", user_id="u1")
    chat_store.create_session("s2", user_id="u2")

    response = client.post("/api/chat/sessions/clear")

    assert response.status_code == 200
    assert _data(response)["ok"] is True
    assert _data(client.post("/api/chat/sessions/list"))["items"] == []
