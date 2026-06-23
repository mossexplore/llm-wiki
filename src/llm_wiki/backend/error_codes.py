"""API error codes and response helpers."""
from __future__ import annotations

from enum import Enum

from fastapi import HTTPException


class ErrorCode(Enum):
    SUCCESS = (0, 200, "成功")

    PARAM_INVALID = (10000, 400, "请求参数无效")
    INTERNAL_ERROR = (10001, 500, "服务内部错误")

    CHAT_SESSION_NOT_FOUND = (20001, 404, "会话不存在")
    CHAT_MESSAGE_EMPTY = (20002, 400, "内容为空")
    CHAT_FEEDBACK_INVALID_RATING = (20003, 400, "rating 必须为 up 或 down")
    CHAT_MESSAGE_NOT_FOUND = (20004, 404, "消息不存在")
    CHAT_FEEDBACK_ASSISTANT_ONLY = (20005, 400, "只能对 Agent 回复反馈")
    CHAT_FEEDBACK_REASON_REQUIRED = (20006, 400, "点踩请填写原因")

    INGEST_CONTENT_EMPTY = (30001, 400, "内容为空")
    INGEST_TITLE_EMPTY = (30002, 400, "title 不能为空")
    INGEST_SIGNATURES_EMPTY = (30003, 400, "signatures 不能为空(检索全靠它命中)")
    INGEST_BATCH_PARSE_EMPTY = (30004, 400, "未解析到任何记录;请用 Markdown 一级标题 # 分隔多条")
    INGEST_COMMIT_BATCH_EMPTY = (30005, 400, "没有要入库的记录")

    KNOWLEDGE_PATH_INVALID = (40001, 400, "非法知识路径")
    KNOWLEDGE_FILE_INVALID = (40002, 400, "非法知识文件")
    KNOWLEDGE_NOT_FOUND = (40003, 404, "知识不存在")
    KNOWLEDGE_TITLE_EMPTY = (40004, 400, "title 不能为空")
    KNOWLEDGE_SIGNATURES_EMPTY = (40005, 400, "signatures 不能为空(检索全靠它命中)")

    SEARCH_QUERY_EMPTY = (50001, 400, "请输入报错信息")

    def __init__(self, code: int, http_status: int, description: str):
        self.code = code
        self.http_status = http_status
        self.description = description


def api_error_detail(error: ErrorCode, description: str | None = None) -> dict:
    return {
        "code": error.code,
        "description": description or error.description,
    }


def raise_api_error(error: ErrorCode, description: str | None = None) -> None:
    raise HTTPException(
        status_code=error.http_status,
        detail=api_error_detail(error, description),
    )


def stream_error_text(request_id: str | None = None) -> str:
    """流式接口对外的通用错误文案。

    原始异常只进日志(logger.exception),绝不回传给客户端,避免泄漏 base_url、
    网关报文等内部信息;带上 request_id 便于用户报障时定位。
    """
    base = ErrorCode.INTERNAL_ERROR.description
    return f"{base}(request_id={request_id})" if request_id else base
