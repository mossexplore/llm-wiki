from typing import Annotated, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


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


class SessionListReq(SessionScopeReq):
    page: int = Field(default=1, gt=0)
    page_size: int = Field(default=10, gt=0)


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
    model_config = ConfigDict(extra="forbid")

    message_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FeedbackReasonReq(BaseModel):
    feedback_info: str = ""
    feedback_info_types: list[str] = Field(default_factory=list)


class FeedbackReq(BaseModel):
    feedback: Optional[str] = None
    reason: Optional[Union[str, FeedbackReasonReq]] = None
    user_id: Optional[str] = None
