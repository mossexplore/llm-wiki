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
