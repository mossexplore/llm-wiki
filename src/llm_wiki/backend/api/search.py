import datetime

from fastapi import APIRouter

from llm_wiki.knowledge import query

from ..error_codes import ErrorCode, raise_api_error
from ..response import success
from ..schemas import QueryReq

router = APIRouter()


@router.post("/api/query")
def query_kb(req: QueryReq):
    if not req.log.strip():
        raise_api_error(ErrorCode.SEARCH_QUERY_EMPTY)
    return success(query.search(req.log))


@router.get("/api/kb/stats")
def kb_stats():
    # 样例:统一信封返回。其余 endpoint 暂未迁移,沿用裸 dict。
    cases = query.load_cases()
    verified = sum(1 for c in cases if c["status"] == "verified")
    drafts = sum(1 for c in cases if c["status"] == "draft")
    signatures = sum(len(c["signatures"]) for c in cases)
    latest = max((c["path"].stat().st_mtime for c in cases), default=None)
    return success(
        {
            "cases": len(cases),
            "verified": verified,
            "drafts": drafts,
            "signatures": signatures,
            "updated": datetime.datetime.fromtimestamp(latest).isoformat(timespec="seconds") if latest else None,
        }
    )
