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

from fastapi import FastAPI, UploadFile, File, HTTPException  # noqa: E402
from fastapi.staticfiles import StaticFiles                   # noqa: E402
from fastapi.responses import FileResponse                    # noqa: E402
from pydantic import BaseModel                                # noqa: E402

app = FastAPI(title="log-wiki")
STATIC = pathlib.Path(__file__).resolve().parent / "static"


# ---------------- 1) 入库:预览(不落库) ----------------
@app.post("/api/ingest/preview")
async def ingest_preview(file: UploadFile = File(...)):
    raw = (await file.read()).decode("utf-8", errors="replace")
    if not raw.strip():
        raise HTTPException(400, "文件内容为空")
    try:
        case = ingest.extract(raw)            # 调 OpenAI 抽取(失败会抛异常)
    except Exception as e:                     # 凭证缺失 / 网络 / JSON 解析等
        raise HTTPException(502, f"LLM 抽取失败:{e}")
    # 统一字段,补默认值,交给前端展示并允许编辑
    return {
        "raw": raw,
        "title": case.get("title", ""),
        "category": case.get("category", "未分类"),
        "signatures": case.get("signatures", []) or [],
        "components": case.get("components", []) or [],
        "background": case.get("background", ""),
        "diagnosis": case.get("diagnosis", ""),
        "solution": case.get("solution", ""),
    }


# ---------------- 2) 入库:确认落库 ----------------
class CommitReq(BaseModel):
    raw: str
    title: str
    category: str = "未分类"
    signatures: List[str] = []
    components: List[str] = []
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


# ---------------- 静态前端 ----------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
