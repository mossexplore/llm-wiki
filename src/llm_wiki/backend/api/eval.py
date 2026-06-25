from typing import Optional

from fastapi import APIRouter

from llm_wiki.search_index.eval import run_eval

from ..core.error_codes import ErrorCode, raise_api_error
from ..core.response import success
from ..schemas import EvalRunReq

router = APIRouter()

_ALLOWED_K = (1, 3, 5, 10)


@router.post("/api/eval/run")
def eval_run(req: Optional[EvalRunReq] = None):
    """在隔离的 SQLite 沙箱里跑检索评测,返回 recall@k / MRR 报告。

    用固定语料评测,绝不触碰生产检索索引;k 仅允许 1/3/5/10,无请求体时默认 3。
    """
    k = req.k if (req and req.k in _ALLOWED_K) else 3
    report = run_eval(k)
    if not report.get("ok"):
        raise_api_error(ErrorCode.INTERNAL_ERROR)
    return success(report)
