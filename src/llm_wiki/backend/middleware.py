import time
import uuid

from fastapi import Request

from .app_logging import access_logger, logger
from .response import set_request_id


async def request_logging_middleware(request: Request, call_next):
    """统一接口日志:每个 HTTP 请求都有开始/结束/异常记录与 request_id。"""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    set_request_id(request_id)   # 供响应信封 meta.uuid 与异常处理器取用
    started = time.perf_counter()
    client = request.client.host if request.client else "-"
    method = request.method
    path = request.url.path
    query_string = request.url.query
    path_qs = f"{path}?{query_string}" if query_string else path
    access_logger.info(
        "http.request.start request_id=%s method=%s path=%s client=%s",
        request_id, method, path_qs, client,
    )
    logger.info(
        "http.request.start request_id=%s method=%s path=%s client=%s",
        request_id, method, path_qs, client,
    )
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "http.request.error request_id=%s method=%s path=%s client=%s elapsed_ms=%s",
            request_id, method, path_qs, client, elapsed_ms,
        )
        access_logger.exception(
            "http.request.error request_id=%s method=%s path=%s client=%s elapsed_ms=%s",
            request_id, method, path_qs, client, elapsed_ms,
        )
        raise
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    access_logger.info(
        "http.request.done request_id=%s method=%s path=%s status=%s client=%s elapsed_ms=%s",
        request_id, method, path_qs, response.status_code, client, elapsed_ms,
    )
    logger.info(
        "http.request.done request_id=%s method=%s path=%s status=%s client=%s elapsed_ms=%s",
        request_id, method, path_qs, response.status_code, client, elapsed_ms,
    )
    return response
