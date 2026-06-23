"""统一 JSON 响应信封(仅非流式接口)。

所有非流式接口返回统一结构::

    {
      "version": "1.0",
      "meta": {"uuid": "<request_id>"},
      "result": {"code": 0, "des": "success", "data": {...}}
    }

- ``meta.uuid``：本次请求的 request_id(与响应头 X-Request-ID 一致),便于前后端对齐链路;
  由中间件经 contextvar 注入,endpoint 无需手动透传。
- ``result.code`` / ``result.des``：业务码与描述,复用 error_codes.ErrorCode。
- ``result.data``：实际响应数据(None 时归一为 {})。

约定:HTTP 状态码照常使用(信封并存),不靠 code 取代状态码;
流式接口(NDJSON / text 流)不套此信封,沿用各自事件协议。
"""
from __future__ import annotations

import contextvars
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .app_logging import logger
from .error_codes import ErrorCode

API_VERSION = "1.0"

_request_id_ctx: contextvars.ContextVar = contextvars.ContextVar("request_id", default="")


def set_request_id(request_id: str) -> None:
    """由中间件在请求入口调用,把 request_id 放进当前请求上下文。"""
    _request_id_ctx.set(request_id or "")


def get_request_id() -> str:
    return _request_id_ctx.get()


def envelope(data: Any = None, *, code: int = 0, des: str = "success",
             request_id: str | None = None) -> dict:
    return {
        "version": API_VERSION,
        "meta": {"uuid": request_id if request_id is not None else get_request_id()},
        "result": {"code": code, "des": des, "data": {} if data is None else data},
    }


def success(data: Any = None) -> dict:
    """成功响应:endpoint 直接 ``return success(data)``。"""
    return envelope(data)


def _error_json(status: int, code: int, des: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=envelope(code=code, des=des, data=None))


def register_exception_handlers(app: FastAPI) -> None:
    """把异常统一裹进信封,同时保留 HTTP 状态码。"""

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_exception(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:        # raise_api_error 抛出的结构化错误
            code = detail["code"]
            des = detail.get("description") or ErrorCode.INTERNAL_ERROR.description
        else:                                                     # 框架/路由抛出的普通 HTTPException
            fallback = ErrorCode.PARAM_INVALID if exc.status_code < 500 else ErrorCode.INTERNAL_ERROR
            code = fallback.code
            des = detail if isinstance(detail, str) else fallback.description
        return _error_json(exc.status_code, code, des)

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request, exc: RequestValidationError):
        err = ErrorCode.PARAM_INVALID
        return _error_json(err.http_status, err.code, err.description)

    @app.exception_handler(Exception)
    async def _on_unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error path=%s request_id=%s", request.url.path, get_request_id())
        err = ErrorCode.INTERNAL_ERROR
        return _error_json(err.http_status, err.code, err.description)
