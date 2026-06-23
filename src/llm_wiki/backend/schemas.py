from typing import List, Optional

from pydantic import BaseModel, Field


class PreviewReq(BaseModel):
    raw: str


class CommitReq(BaseModel):
    raw: str
    title: str
    category: str = "未分类"
    signatures: List[str] = Field(default_factory=list)
    components: List[str] = Field(default_factory=list)
    background: str = ""
    diagnosis: str = ""
    solution: str = ""
    ident: Optional[str] = None


class KnowledgeUpdateReq(BaseModel):
    raw: str = ""
    title: str
    category: str = "未分类"
    signatures: List[str] = Field(default_factory=list)
    components: List[str] = Field(default_factory=list)
    background: str = ""
    diagnosis: str = ""
    solution: str = ""
    ident: Optional[str] = None


class PreviewBatchReq(BaseModel):
    raw: str


class CommitBatchReq(BaseModel):
    records: List[CommitReq]


class QueryReq(BaseModel):
    log: str


class SessionCreateReq(BaseModel):
    title: Optional[str] = None


class ChatMessageReq(BaseModel):
    content: str


class FeedbackReq(BaseModel):
    rating: str
    reason: Optional[str] = None
