from typing import Optional, Union

from pydantic import BaseModel, Field


class PreviewReq(BaseModel):
    raw: str


class CommitReq(BaseModel):
    raw: str
    title: str
    category: str = "未分类"
    signatures: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    background: str = ""
    diagnosis: str = ""
    solution: str = ""
    ident: Optional[str] = None


class KnowledgeUpdateReq(BaseModel):
    raw: str = ""
    title: str
    category: str = "未分类"
    signatures: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    background: str = ""
    diagnosis: str = ""
    solution: str = ""
    ident: Optional[str] = None


class PreviewBatchReq(BaseModel):
    raw: str


class CommitBatchReq(BaseModel):
    records: list[CommitReq]


class QueryReq(BaseModel):
    log: str


class EvalRunReq(BaseModel):
    k: Optional[int] = 3


class SessionScopeReq(BaseModel):
    """读取/删除类操作的统一请求体:可选 user_id 用于按用户隔离。

    部署环境强制 POST,原先用 query 参数 user_id 的 GET/DELETE 端点改用本体。"""

    user_id: Optional[str] = None


class SessionCreateReq(BaseModel):
    title: Optional[str] = None
    user_id: Optional[str] = None
    source_code: Optional[str] = None


class ChatMessageReq(BaseModel):
    content: str
    user_id: Optional[str] = None
    message_format: Optional[str] = None


class FeedbackReasonReq(BaseModel):
    feedback_info: str = ""
    feedback_info_types: list[str] = Field(default_factory=list)


class FeedbackReq(BaseModel):
    feedback: Optional[str] = None
    reason: Optional[Union[str, FeedbackReasonReq]] = None
    user_id: Optional[str] = None
