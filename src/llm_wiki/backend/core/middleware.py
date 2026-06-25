import time
import uuid

from fastapi import Request

from .app_logging import access_logger, logger
from .response import set_request_id


async def request_logging_middleware(request: Request, call_next):
    """统一接口日志:每个 HTTP 请求都有开始/结束/异常记录与 request_id。"""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    set_request_id(request_id)  # 供响应信封 meta.uuid 与异常处理器取用
    started = time.perf_counter()
    client = request.client.host if request.client else "-"
    method = request.method
    path = request.url.path
    query_string = request.url.query
    path_qs = f"{path}?{query_string}" if query_string else path
    start_log = f"http.request.start request_id={request_id} method={method} path={path_qs} client={client}"
    access_logger.info(start_log)
    logger.info(start_log)
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        error_log = (
            f"http.request.error request_id={request_id} method={method} path={path_qs} "
            f"client={client} elapsed_ms={elapsed_ms}"
        )
        logger.exception(error_log)
        access_logger.exception(error_log)
        raise
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    done_log = (
        f"http.request.done request_id={request_id} method={method} path={path_qs} "
        f"status={response.status_code} client={client} elapsed_ms={elapsed_ms}"
    )
    access_logger.info(done_log)
    logger.info(done_log)
    return response
