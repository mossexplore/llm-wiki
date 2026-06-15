#!/usr/bin/env python3
"""
server.py — log-wiki 的 Web 后端(FastAPI)

提供三个接口,前端(server/static/index.html)调用:
  POST /api/ingest/preview  上传文件 → LLM 抽取成结构化案例(仅预览,不落库)
  POST /api/ingest/commit   用户复核确认后 → 真正写入 raw/ + wiki/cases/(verified)
  POST /api/query           粘一段报错 → 检索本地知识库

入库的"两步走"对应知识库护栏①:preview 不碰知识库,只有 commit 才真正落库。

启动:
    pip install -r requirements.txt
    uvicorn server.server:app --reload --port 8000
    # 浏览器打开 http://127.0.0.1:8000/
"""
import sys, datetime, pathlib
from typing import List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))  # 复用 scripts/ 下的入库与检索逻辑

import ingest                                # noqa: E402
import query                                 # noqa: E402

from fastapi import FastAPI, HTTPException                    # noqa: E402
from fastapi.staticfiles import StaticFiles                   # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field                         # noqa: E402

app = FastAPI(title="log-wiki")
STATIC = pathlib.Path(__file__).resolve().parent / "static"

SAMPLE_RAW = (
    "大促高峰 order-service 一批接口疯狂 500,日志一直刷 "
    "HikariPool-1 - Connection is not available, request timed out after 30007ms。"
    "DB CPU 不高,但活跃连接数顶满 maximumPoolSize 设为 20。"
    "排查发现慢查询 getOrderDetail 平均 4.2s 长时间占用连接,池一被占满后续请求等待 30s 超时即报错。"
    "最终给 getOrderDetail 涉及字段加复合索引,查询降到 60ms,并把 maximumPoolSize 调到 40、"
    "加上 leakDetectionThreshold 连接泄漏检测,大促期间未再复现。"
)

SAMPLE_CASE = {
    "title": "HikariPool 连接池耗尽致接口批量 500",
    "category": "数据库 / 连接池",
    "signatures": ["HikariPool-1 - Connection is not available, request timed out"],
    "components": ["order-service", "HikariCP", "MySQL"],
    "background": "大促高峰期 order-service 接口批量返回 500,DB CPU 不高但活跃连接顶满 maximumPoolSize 设为 20。",
    "diagnosis": "慢查询 getOrderDetail 平均 4.2s 长时间占用连接,连接池耗尽后续请求等待 30s 超时,HikariCP 抛 Connection is not available。",
    "solution": "为 getOrderDetail 涉及字段加复合索引,查询从 4.2s 降至 60ms;maximumPoolSize 由 20 调至 40,并启用 HikariCP leakDetectionThreshold 连接泄漏检测,大促期间未再出现连接池耗尽。",
}


# ---------------- 1) 入库:流式预览(不落库) ----------------
class PreviewReq(BaseModel):
    raw: str   # 用户在前端文本框粘贴的原始排查记录


@app.post("/api/ingest/preview")
def ingest_preview(req: PreviewReq):
    """流式返回模型抽取的 JSON 文本(逐段 chunk),前端实时显示、结束后解析成表单。

    此步不写任何文件;原文(raw)留在前端,确认入库时再随 commit 一并提交。
    """
    raw = req.raw
    if not raw.strip():
        raise HTTPException(400, "内容为空")
    prompt = ingest.EXTRACT_PROMPT.format(raw=raw)

    def gen():
        try:
            for delta in ingest.stream_llm(prompt):
                yield delta
        except Exception as e:                 # 凭证缺失 / 网络等,以标记结尾让前端识别
            yield f"\n[ERROR] {e}"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


# ---------------- 2) 入库:确认落库 ----------------
class CommitReq(BaseModel):
    raw: str
    title: str
    category: str = "未分类"
    signatures: List[str] = Field(default_factory=list)
    components: List[str] = Field(default_factory=list)
    background: str = ""
    diagnosis: str = ""
    solution: str = ""
    ident: Optional[str] = None   # 可选工单号;缺省用时间戳


@app.post("/api/ingest/commit")
def ingest_commit(req: CommitReq):
    if not req.title.strip():
        raise HTTPException(400, "title 不能为空")
    if not req.signatures:
        raise HTTPException(400, "signatures 不能为空(检索全靠它命中)")

    ident = req.ident or datetime.datetime.now().strftime("%H%M%S")
    raw_path = ingest.archive_raw(req.raw, ident)          # ① 原文存档不可变层
    raw_rel = str(raw_path.relative_to(ROOT))

    case = req.model_dump()
    # 用户已在前端复核确认 → 直接 verified 落正式案例层
    md = ingest.to_markdown(case, raw_rel, status="verified", confidence="high")
    slug = ingest.slugify(req.title)
    case_path = ROOT / "wiki" / "cases" / f"{slug}.md"
    case_path.write_text(md, encoding="utf-8")

    return {
        "ok": True,
        "raw_file": raw_rel,
        "case_file": str(case_path.relative_to(ROOT)),
        "slug": slug,
    }


# ---------------- 3) 检索 ----------------
class QueryReq(BaseModel):
    log: str


@app.post("/api/query")
def query_kb(req: QueryReq):
    if not req.log.strip():
        raise HTTPException(400, "请输入报错信息")
    return query.search(req.log)


# ---------------- 4) 首页辅助数据 ----------------
@app.get("/api/examples/ingest")
def ingest_example():
    return {"raw": SAMPLE_RAW, "case": SAMPLE_CASE}


@app.get("/api/kb/stats")
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


# ---------------- 静态前端 ----------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
