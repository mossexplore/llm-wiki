"""统一响应信封 + 异常处理器的行为测试。

用一个最小 FastAPI 应用单独验证 response.py,不引入真实 server 的配置/DB 副作用。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from llm_wiki.backend.core.error_codes import ErrorCode, raise_api_error, stream_error_text
from llm_wiki.backend.core.response import (
    envelope,
    register_exception_handlers,
    set_request_id,
    success,
)


def test_envelope_shape_and_defaults():
    env = envelope({"x": 1}, request_id="rid-1")
    assert env["version"] == "1.0"
    assert env["meta"]["uuid"] == "rid-1"
    assert env["result"] == {"code": 0, "des": "success", "data": {"x": 1}}


def test_envelope_none_data_becomes_empty_obj():
    assert envelope(None, request_id="r")["result"]["data"] == {}


class _Body(BaseModel):
    name: str


@pytest.fixture()
def client():
    app = FastAPI()
    register_exception_handlers(app)

    @app.middleware("http")
    async def _set_rid(request, call_next):
        set_request_id("req-xyz")
        return await call_next(request)

    @app.get("/ok")
    def ok():
        return success({"items": [1, 2]})

    @app.get("/boom")
    def boom():
        raise_api_error(ErrorCode.CHAT_SESSION_NOT_FOUND)

    @app.get("/empty-description")
    def empty_description():
        raise HTTPException(status_code=400, detail={"code": 49999, "description": ""})

    @app.post("/need-body")
    def need_body(_body: _Body):
        return success()

    @app.get("/crash")
    def crash():
        raise RuntimeError("内部细节不应外泄")

    return TestClient(app, raise_server_exceptions=False)


def test_success_response_envelope(client):
    r = client.get("/ok")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["uuid"] == "req-xyz"
    assert body["result"]["code"] == 0
    assert body["result"]["data"] == {"items": [1, 2]}


def test_api_error_keeps_http_status_and_code(client):
    r = client.get("/boom")
    assert r.status_code == 404  # HTTP 状态码保留
    body = r.json()
    assert body["result"]["code"] == ErrorCode.CHAT_SESSION_NOT_FOUND.code
    assert body["result"]["des"] == ErrorCode.CHAT_SESSION_NOT_FOUND.description
    assert body["result"]["data"] == {}


def test_structured_http_error_preserves_empty_description(client):
    r = client.get("/empty-description")

    assert r.status_code == 400
    assert r.json()["result"]["code"] == 49999
    assert r.json()["result"]["des"] == ""


def test_validation_error_wrapped_as_param_invalid(client):
    r = client.post("/need-body", json={})  # 缺 name 字段
    assert r.status_code == ErrorCode.PARAM_INVALID.http_status
    assert r.json()["result"]["code"] == ErrorCode.PARAM_INVALID.code


def test_unhandled_exception_is_generic(client):
    r = client.get("/crash")
    assert r.status_code == 500
    body = r.json()
    assert body["result"]["code"] == ErrorCode.INTERNAL_ERROR.code
    assert "内部细节" not in body["result"]["des"]  # 原始异常不外泄


def test_stream_error_text_does_not_expose_request_id():
    text = stream_error_text("req-secret")

    assert text == ErrorCode.INTERNAL_ERROR.description
    assert "req-secret" not in text
    assert "request_id" not in text
