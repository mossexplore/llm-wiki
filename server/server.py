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
import sys, datetime, logging, pathlib, re, time, uuid
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))  # 复用 scripts/ 下的入库与检索逻辑

import ingest                                # noqa: E402
import graph                                 # noqa: E402
import query                                 # noqa: E402
import yaml                                  # noqa: E402

from fastapi import FastAPI, Header, HTTPException            # noqa: E402
from fastapi.staticfiles import StaticFiles                   # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field                         # noqa: E402

app = FastAPI(title="log-wiki")
STATIC = pathlib.Path(__file__).resolve().parent / "static"
logger = logging.getLogger("log_wiki.server")
logger.setLevel(logging.INFO)

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
def ingest_preview(req: PreviewReq, x_request_id: Optional[str] = Header(default=None)):
    """流式返回模型抽取的 JSON 文本(逐段 chunk),前端实时显示、结束后解析成表单。

    此步不写任何文件;原文(raw)留在前端,确认入库时再随 commit 一并提交。
    """
    raw = req.raw
    if not raw.strip():
        raise HTTPException(400, "内容为空")
    request_id = x_request_id or uuid.uuid4().hex[:12]
    prompt = ingest.EXTRACT_PROMPT.format(raw=raw)
    started = time.perf_counter()
    logger.info(
        "ingest.preview.start request_id=%s raw_len=%s prompt_len=%s",
        request_id, len(raw), len(prompt),
    )

    def gen():
        chunk_count = 0
        char_count = 0
        try:
            for delta in ingest.stream_llm(prompt):
                chunk_count += 1
                char_count += len(delta)
                yield delta
            logger.info(
                "ingest.preview.done request_id=%s chunks=%s chars=%s elapsed_ms=%s",
                request_id, chunk_count, char_count,
                int((time.perf_counter() - started) * 1000),
            )
        except Exception as e:                 # 凭证缺失 / 网络等,以标记结尾让前端识别
            logger.exception(
                "ingest.preview.error request_id=%s chunks=%s chars=%s elapsed_ms=%s",
                request_id, chunk_count, char_count,
                int((time.perf_counter() - started) * 1000),
            )
            yield f"\n[ERROR][request_id={request_id}] {e}"

    return StreamingResponse(
        gen(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Request-ID": request_id},
    )


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


CASES_DIR = ROOT / "wiki" / "cases"


def _split_case(path: pathlib.Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    try:
        _, fm, body = text.split("---", 2)
    except ValueError:
        return {}, text
    return yaml.safe_load(fm) or {}, body


def _section(body: str, title: str) -> str:
    pattern = rf"##\s*{re.escape(title)}\s*\n(.*?)(?=\n##\s|\Z)"
    m = re.search(pattern, body, re.S)
    return m.group(1).strip() if m else ""


def _replace_section(body: str, title: str, content: str) -> str:
    heading = f"## {title}\n"
    next_block = f"{heading}{content.strip()}\n"
    pattern = rf"(##\s*{re.escape(title)}\s*\n)(.*?)(?=\n##\s|\Z)"
    if re.search(pattern, body, re.S):
        return re.sub(pattern, lambda _: next_block, body, count=1, flags=re.S)
    return f"\n{next_block}\n{body.lstrip()}"


def _case_path(case_file: str) -> pathlib.Path:
    raw = pathlib.Path(case_file)
    path = (ROOT / raw).resolve() if raw.parts[:2] == ("wiki", "cases") else (CASES_DIR / raw).resolve()
    cases_root = CASES_DIR.resolve()
    try:
        path.relative_to(cases_root)
    except ValueError:
        raise HTTPException(400, "非法知识路径")
    if path.suffix != ".md" or path.name in ("index.md", "log.md"):
        raise HTTPException(400, "非法知识文件")
    if not path.exists():
        raise HTTPException(404, "知识不存在")
    return path


def _case_detail(path: pathlib.Path) -> dict:
    fm, body = _split_case(path)
    sources = fm.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    raw = ""
    if sources:
        raw_path = (ROOT / str(sources[0]).lstrip("/")).resolve()
        try:
            raw_path.relative_to(ROOT.resolve())
            if raw_path.exists():
                raw = raw_path.read_text(encoding="utf-8")
        except ValueError:
            raw = ""
    stat = path.stat()
    return {
        "file": str(path.relative_to(ROOT)),
        "title": fm.get("title") or path.stem,
        "category": fm.get("category") or "未分类",
        "description": fm.get("description") or "",
        "status": fm.get("status") or "unknown",
        "confidence": fm.get("confidence") or "unknown",
        "signatures": fm.get("signatures") or [],
        "components": fm.get("components") or [],
        "background": _section(body, "问题背景"),
        "diagnosis": _section(body, "定位过程"),
        "solution": _section(body, "解决方案"),
        "ident": path.stem,
        "raw": raw,
        "sources": sources,
        "updated": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def _knowledge_markdown(req: KnowledgeUpdateReq, existing: dict, existing_body: str) -> str:
    sources = existing.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    case = req.model_dump()
    fm = {
        "id": existing.get("id") or ingest.slugify(req.title),
        "type": existing.get("type", "Incident Case"),
        "title": req.title,
        "description": ingest._description(case),
        "category": req.category or "未分类",
        "tags": ingest._tags(case),
        "status": "verified",
        "confidence": existing.get("confidence", "high"),
        "signatures": req.signatures,
        "components": req.components,
        "created": existing.get("created") or datetime.date.today().isoformat(),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
    }
    if existing.get("related"):
        fm["related"] = existing["related"]
    front = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
    citations = "\n".join(f"[{i}] [原始排查记录](/{src})" for i, src in enumerate(sources, 1))
    body = existing_body.strip()
    body = _replace_section(body, "问题背景", req.background)
    body = _replace_section(body, "定位过程", req.diagnosis)
    body = _replace_section(body, "解决方案", req.solution)
    body = _replace_section(body, "Citations", citations)
    return f"---\n{front}---\n\n{body}"


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
    ingest.update_indexes()

    return {
        "ok": True,
        "raw_file": raw_rel,
        "case_file": str(case_path.relative_to(ROOT)),
        "slug": slug,
    }


# ---------------- 批量入库:上传含多条记录(--- 分隔)的 Markdown ----------------
def _split_records(raw: str) -> List[str]:
    """按"独占一行的 ---"切分多条原始记录,去空白。"""
    parts = re.split(r"(?m)^[ \t]*---[ \t]*$", raw)
    return [p.strip() for p in parts if p.strip()]


def _commit_one(req: CommitReq, used_slugs: set) -> dict:
    """写一条 verified 案例;批量时用 used_slugs 去重 slug,避免同标题互相覆盖。"""
    ident = req.ident or datetime.datetime.now().strftime("%H%M%S")
    raw_path = ingest.archive_raw(req.raw, ident)
    raw_rel = str(raw_path.relative_to(ROOT))
    md = ingest.to_markdown(req.model_dump(), raw_rel, status="verified", confidence="high")
    slug = base = ingest.slugify(req.title)
    n = 2
    while slug in used_slugs or (ROOT / "wiki" / "cases" / f"{slug}.md").exists():
        slug = f"{base}-{n}"
        n += 1
    used_slugs.add(slug)
    case_path = ROOT / "wiki" / "cases" / f"{slug}.md"
    case_path.write_text(md, encoding="utf-8")
    return {"case_file": str(case_path.relative_to(ROOT)), "raw_file": raw_rel, "slug": slug}


class PreviewBatchReq(BaseModel):
    raw: str   # 整个 Markdown 文件内容(多条记录,--- 分隔)


@app.post("/api/ingest/preview_batch")
def ingest_preview_batch(req: PreviewBatchReq):
    """切分多条原始记录,并行调用 LLM 抽取;一次性返回每条的抽取结果(仅预览,不落库)。"""
    records = _split_records(req.raw)
    if not records:
        raise HTTPException(400, "未解析到任何记录;请用独占一行的 --- 分隔多条")
    results: List[Optional[dict]] = [None] * len(records)

    def work(i: int, rec: str):
        try:
            case = ingest.extract(rec)
            return i, {
                "index": i, "raw": rec, "ok": True,
                "title": case.get("title", ""), "category": case.get("category", "未分类"),
                "signatures": case.get("signatures", []) or [], "components": case.get("components", []) or [],
                "background": case.get("background", ""), "diagnosis": case.get("diagnosis", ""),
                "solution": case.get("solution", ""),
            }
        except Exception as e:                 # 单条失败不影响其它条
            return i, {"index": i, "raw": rec, "ok": False, "error": str(e)}

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=min(8, len(records))) as ex:
        for fut in as_completed([ex.submit(work, i, r) for i, r in enumerate(records)]):
            i, res = fut.result()
            results[i] = res
    logger.info("ingest.preview_batch records=%s elapsed_ms=%s",
                len(records), int((time.perf_counter() - started) * 1000))
    ok = sum(1 for r in results if r and r["ok"])
    return {"count": len(records), "ok": ok, "records": results}


class CommitBatchReq(BaseModel):
    records: List[CommitReq]


@app.post("/api/ingest/commit_batch")
def ingest_commit_batch(req: CommitBatchReq):
    """一次性把多条已复核记录入库;逐条写,返回每条结果,最后统一刷新索引。"""
    if not req.records:
        raise HTTPException(400, "没有要入库的记录")
    used: set = set()
    stamp = datetime.datetime.now().strftime("%H%M%S")
    results = []
    for i, rec in enumerate(req.records):
        sigs = [s for s in rec.signatures if s and s.strip()]
        if not rec.title.strip() or not sigs:
            results.append({"index": i, "ok": False, "error": "标题或 signatures 为空"})
            continue
        rec.signatures = sigs
        rec.components = [c for c in rec.components if c and c.strip()]
        if not rec.ident:
            rec.ident = f"{stamp}-{i + 1}"           # 批内唯一,避免 raw 文件同名覆盖
        try:
            results.append({"index": i, "ok": True, **_commit_one(rec, used)})
        except Exception as e:
            results.append({"index": i, "ok": False, "error": str(e)})
    ingest.update_indexes()
    return {"ok": sum(1 for r in results if r["ok"]), "total": len(results), "results": results}


@app.get("/api/knowledge")
def knowledge_list():
    items = []
    for path in sorted(CASES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name in ("index.md", "log.md"):
            continue
        fm, body = _split_case(path)
        if fm.get("status", "verified") != "verified":
            continue
        stat = path.stat()
        items.append({
            "file": str(path.relative_to(ROOT)),
            "title": fm.get("title") or path.stem,
            "category": fm.get("category") or "未分类",
            "description": fm.get("description") or _section(body, "问题背景"),
            "status": fm.get("status") or "verified",
            "confidence": fm.get("confidence") or "unknown",
            "signatures": fm.get("signatures") or [],
            "components": fm.get("components") or [],
            "created": fm.get("created") or "",                # 入库日期
            "timestamp": fm.get("timestamp") or "",            # 入库时间(完整 UTC)
            "updated": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return {"items": items}


@app.get("/api/knowledge/{case_file:path}")
def knowledge_detail(case_file: str):
    return _case_detail(_case_path(case_file))


@app.delete("/api/knowledge/{case_file:path}")
def knowledge_delete(case_file: str):
    """删除一条已入库知识(wiki/cases/*.md);raw/ 不可变层原文保留以备溯源。"""
    path = _case_path(case_file)                # 复用校验:限定 cases 目录、.md、非 index
    path.unlink()
    ingest.update_indexes()
    return {"ok": True, "case_file": str(path.relative_to(ROOT))}


@app.put("/api/knowledge/{case_file:path}")
def knowledge_update(case_file: str, req: KnowledgeUpdateReq):
    if not req.title.strip():
        raise HTTPException(400, "title 不能为空")
    signatures = [s for s in req.signatures if s and s.strip()]
    if not signatures:
        raise HTTPException(400, "signatures 不能为空(检索全靠它命中)")
    req.signatures = signatures
    req.components = [c for c in req.components if c and c.strip()]
    path = _case_path(case_file)
    existing, existing_body = _split_case(path)
    path.write_text(_knowledge_markdown(req, existing, existing_body), encoding="utf-8")
    ingest.update_indexes()
    return {"ok": True, "case_file": str(path.relative_to(ROOT))}


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


@app.get("/api/graph")
def knowledge_graph():
    return graph.build_graph()


# ---------------- 静态前端 ----------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
