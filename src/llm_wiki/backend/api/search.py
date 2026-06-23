import datetime

from fastapi import APIRouter

from llm_wiki.knowledge import query  # noqa: E402

from ..config import ROOT  # noqa: F401
from ..error_codes import ErrorCode, raise_api_error
from ..schemas import QueryReq

router = APIRouter()


@router.post("/api/query")
def query_kb(req: QueryReq):
    if not req.log.strip():
        raise_api_error(ErrorCode.SEARCH_QUERY_EMPTY)
    return query.search(req.log)


@router.get("/api/kb/stats")
def kb_stats():
    cases = query.load_cases()
    verified = sum(1 for c in cases if c["status"] == "verified")
    drafts = sum(1 for c in cases if c["status"] == "draft")
    signatures = sum(len(c["signatures"]) for c in cases)
    latest = max((c["path"].stat().st_mtime for c in cases), default=None)
    return {
        "cases": len(cases),
        "verified": verified,
        "drafts": drafts,
        "signatures": signatures,
        "updated": datetime.datetime.fromtimestamp(latest).isoformat(timespec="seconds") if latest else None,
    }
