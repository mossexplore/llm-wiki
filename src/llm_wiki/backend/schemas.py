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
    user_id: Optional[str] = None


class SessionCreateReq(BaseModel):
    session_id: Optional[str] = None
    title: Optional[str] = None
    user_id: Optional[str] = None
    source_code: Optional[str] = None


class ChatMessageReq(BaseModel):
    content: str
    user_id: Optional[str] = None
    message_format: Optional[str] = None


class ChatStopReq(BaseModel):
    content: str
    message_id: Optional[str] = None
    user_id: Optional[str] = None
    answer_source: Optional[str] = None
    retrieval_mode: Optional[str] = None
    refs: list[dict] = Field(default_factory=list)
    elapsed_ms: Optional[int] = Field(default=None, gt=0)
    retrieval_ms: Optional[int] = Field(default=None, gt=0)
    model_wait_ms: Optional[int] = Field(default=None, gt=0)
    first_delta_ms: Optional[int] = Field(default=None, gt=0)
    total_ms: Optional[int] = Field(default=None, gt=0)
    message_count: Optional[int] = None
    prompt_chars: Optional[int] = None


class FeedbackReasonReq(BaseModel):
    feedback_info: str = ""
    feedback_info_types: list[str] = Field(default_factory=list)


class FeedbackReq(BaseModel):
    feedback: Optional[str] = None
    reason: Optional[Union[str, FeedbackReasonReq]] = None
    user_id: Optional[str] = None
