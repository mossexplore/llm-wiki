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
import sys, datetime, json, logging, pathlib, queue, re, time, uuid
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))  # 复用 scripts/ 下的入库与检索逻辑

import ingest                                # noqa: E402
import graph                                 # noqa: E402
import query                                 # noqa: E402
import search_index                          # noqa: E402  检索索引(SQLite+FTS5)同步
import agent                                 # noqa: E402  对话 Agent:先检索后大模型兜底
import chat_store                            # noqa: E402  对话运营数据持久化(SQLite)
import yaml                                  # noqa: E402

from fastapi import FastAPI, Header, HTTPException            # noqa: E402
from fastapi.staticfiles import StaticFiles                   # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field                         # noqa: E402

app = FastAPI(title="log-wiki")
STATIC = pathlib.Path(__file__).resolve().parent / "static"
logger = logging.getLogger("log_wiki.server")
logger.setLevel(logging.INFO)


def _index_case_file(case_path: pathlib.Path) -> None:
    """把刚写好的案例文件同步进检索索引;失败不影响主流程(文件才是权威源)。"""
    try:
        case = search_index.case_from_file(case_path)
        if case:
            search_index.backend.index_case(case)
    except Exception:
        logger.exception("search_index.index_case failed file=%s", case_path)


def _index_remove(case_path: pathlib.Path) -> None:
    try:
        search_index.backend.remove_case(case_path.stem)
    except Exception:
        logger.exception("search_index.remove_case failed file=%s", case_path)


@app.on_event("startup")
def _build_search_index() -> None:
    """启动时从 wiki/cases/ 整库重建索引,确保与磁盘文件一致(含离线手改的情况)。"""
    try:
        if search_index.backend.available():
            n = search_index.backend.reindex_all()
            logger.info("search_index.reindex_all built=%s db=%s", n, search_index.DB_PATH)
        else:
            logger.warning("FTS5 不可用,检索将回退到文件扫描(功能正常,速度较慢)。")
    except Exception:
        logger.exception("search_index startup reindex failed")

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
    _index_case_file(case_path)

    return {
        "ok": True,
        "raw_file": raw_rel,
        "case_file": str(case_path.relative_to(ROOT)),
        "slug": slug,
    }


# ---------------- 批量入库:上传含多条记录(# 一级标题分隔)的 Markdown ----------------
def _split_records(raw: str) -> List[str]:
    """按 Markdown 一级标题切分多条原始记录,去空白。

    每个 `# 标题` 到下一个 `# 标题` 之前是一条记录。没有一级标题时视为单条。
    不再使用 `---` 分隔,避免和 YAML frontmatter / 水平线 / 日志分隔符冲突。
    """
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(re.finditer(r"(?m)^#[ \t]+.+$", raw))
    if not matches:
        return [raw.strip()] if raw.strip() else []
    parts = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        part = raw[start:end].strip()
        if part:
            parts.append(part)
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
    raw: str   # 整个 Markdown 文件内容(多条记录,# 一级标题分隔)


def _normalize_json_text(text: str) -> str:
    txt = (text or "").strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.I).strip()
    txt = re.sub(r"```\s*$", "", txt).strip()
    if not txt.startswith("{"):
        first = txt.find("{")
        last = txt.rfind("}")
        if first != -1 and last > first:
            txt = txt[first:last + 1].strip()
    return txt


def _batch_case_record(index: int, raw: str, case: dict) -> dict:
    return {
        "index": index, "raw": raw, "ok": True,
        "title": case.get("title", ""), "category": case.get("category", "未分类"),
        "signatures": case.get("signatures", []) or [], "components": case.get("components", []) or [],
        "background": case.get("background", ""), "diagnosis": case.get("diagnosis", ""),
        "solution": case.get("solution", ""),
    }


def _ndjson(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


@app.post("/api/ingest/preview_batch")
def ingest_preview_batch(req: PreviewBatchReq):
    """切分多条原始记录,并行调用 LLM 抽取;以 NDJSON 流式返回每条的模型输出与结果。"""
    records = _split_records(req.raw)
    if not records:
        raise HTTPException(400, "未解析到任何记录;请用 Markdown 一级标题 # 分隔多条")
    request_id = uuid.uuid4().hex[:12]

    def work(i: int, rec: str, out: queue.Queue):
        chunk_count = 0
        acc = ""
        started = time.perf_counter()
        out.put({"type": "start", "request_id": request_id, "index": i, "raw": rec})
        try:
            prompt = ingest.EXTRACT_PROMPT.format(raw=rec)
            for delta in ingest.stream_llm(prompt):
                chunk_count += 1
                acc += delta
                out.put({"type": "delta", "request_id": request_id, "index": i, "text": delta})
            case = json.loads(_normalize_json_text(acc))
            out.put({
                "type": "done",
                "request_id": request_id,
                "index": i,
                "record": _batch_case_record(i, rec, case),
            })
            logger.info(
                "ingest.preview_batch.item.done request_id=%s index=%s chunks=%s chars=%s elapsed_ms=%s",
                request_id, i, chunk_count, len(acc), int((time.perf_counter() - started) * 1000),
            )
        except Exception as e:                 # 单条失败不影响其它条
            logger.exception(
                "ingest.preview_batch.item.error request_id=%s index=%s chunks=%s chars=%s elapsed_ms=%s",
                request_id, i, chunk_count, len(acc), int((time.perf_counter() - started) * 1000),
            )
            out.put({"type": "error", "request_id": request_id, "index": i, "raw": rec, "error": str(e)})
        finally:
            out.put({"type": "finished", "request_id": request_id, "index": i})

    def gen():
        started = time.perf_counter()
        out: queue.Queue = queue.Queue()
        ok = 0
        failed = 0
        finished = 0
        with ThreadPoolExecutor(max_workers=min(8, len(records))) as ex:
            futures = [ex.submit(work, i, rec, out) for i, rec in enumerate(records)]
            while finished < len(records):
                event = out.get()
                if event["type"] == "finished":
                    finished += 1
                    continue
                if event["type"] == "done":
                    ok += 1
                elif event["type"] == "error":
                    failed += 1
                yield _ndjson(event)
            for fut in futures:
                fut.result()
        logger.info(
            "ingest.preview_batch.done request_id=%s records=%s ok=%s failed=%s elapsed_ms=%s",
            request_id, len(records), ok, failed, int((time.perf_counter() - started) * 1000),
        )
        yield _ndjson({"type": "summary", "request_id": request_id, "count": len(records), "ok": ok, "failed": failed})

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"X-Request-ID": request_id, "X-Accel-Buffering": "no"},
    )


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
            res = _commit_one(rec, used)
            _index_case_file(ROOT / res["case_file"])
            results.append({"index": i, "ok": True, **res})
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


@app.delete("/api/knowledge")
def knowledge_clear():
    """清空全部已入库知识(wiki/cases/ 下的顶层案例)。raw/ 不可变层原文保留以备溯源。"""
    deleted = []
    for path in sorted(CASES_DIR.glob("*.md")):
        if path.name in ("index.md", "log.md"):
            continue
        rel = str(path.relative_to(ROOT))
        path.unlink()
        _index_remove(path)
        deleted.append(rel)
    ingest.update_indexes()
    return {"ok": True, "deleted": len(deleted), "files": deleted}


@app.get("/api/knowledge/{case_file:path}")
def knowledge_detail(case_file: str):
    return _case_detail(_case_path(case_file))


@app.delete("/api/knowledge/{case_file:path}")
def knowledge_delete(case_file: str):
    """删除一条已入库知识(wiki/cases/*.md);raw/ 不可变层原文保留以备溯源。"""
    path = _case_path(case_file)                # 复用校验:限定 cases 目录、.md、非 index
    path.unlink()
    ingest.update_indexes()
    _index_remove(path)
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
    _index_case_file(path)
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


# ---------------- 5) 对话 Agent ----------------
class SessionCreateReq(BaseModel):
    title: Optional[str] = None


class ChatMessageReq(BaseModel):
    content: str


class FeedbackReq(BaseModel):
    rating: str                  # 'up' | 'down'
    reason: Optional[str] = None


def _session_title(text: str) -> str:
    """用首条提问生成会话标题:取首行前 20 字。"""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else "新会话"
    line = line.strip() or "新会话"
    return line[:20] + ("…" if len(line) > 20 else "")


@app.post("/api/chat/sessions")
def chat_create_session(req: SessionCreateReq):
    return chat_store.create_session(req.title or "新会话")


@app.get("/api/chat/sessions")
def chat_list_sessions():
    return {"items": chat_store.list_sessions()}


@app.get("/api/chat/sessions/{session_id}/messages")
def chat_get_messages(session_id: str):
    if not chat_store.session_exists(session_id):
        raise HTTPException(404, "会话不存在")
    return {"items": chat_store.get_messages(session_id)}


@app.delete("/api/chat/sessions/{session_id}")
def chat_delete_session(session_id: str):
    ok = chat_store.delete_session(session_id)
    if not ok:
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@app.post("/api/chat/sessions/{session_id}/messages")
def chat_send_message(session_id: str, req: ChatMessageReq):
    """对话主流程:存用户消息 → 检索 → wiki 命中则流式回 wiki 答案,否则流式回大模型答案
    → 存 Agent 回复。以 NDJSON 流式返回 meta/delta/done 事件。"""
    text = (req.content or "").strip()
    if not text:
        raise HTTPException(400, "内容为空")
    if not chat_store.session_exists(session_id):
        raise HTTPException(404, "会话不存在")

    # 既往历史(给大模型多轮上下文用),需在追加本条用户消息之前取
    history = [{"role": m["role"], "content": m["content"]}
               for m in chat_store.get_messages(session_id)]
    chat_store.add_message(session_id, "user", text)
    # 首条提问时,用它自动命名会话
    if not history:
        try:
            chat_store.rename_session(session_id, _session_title(text))
        except Exception:
            logger.exception("chat rename_session failed session_id=%s", session_id)

    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    logger.info("chat.send.start session_id=%s request_id=%s len=%s", session_id, request_id, len(text))

    def gen():
        acc = ""
        source, mode, refs = "llm", "none", []
        retrieval_ms = 0
        first_delta_ms = None
        try:
            yield _ndjson({
                "type": "status", "request_id": request_id, "stage": "retrieving",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            })
            retrieve_started = time.perf_counter()
            decision = agent.retrieve(text)
            retrieval_ms = decision.get("elapsed_ms", int((time.perf_counter() - retrieve_started) * 1000))
            source = decision["source"]
            mode = decision["mode"]
            refs = decision["refs"]
            logger.info(
                "chat.send.retrieved session_id=%s request_id=%s source=%s mode=%s refs=%s retrieval_ms=%s elapsed_ms=%s",
                session_id, request_id, source, mode, len(refs), retrieval_ms,
                int((time.perf_counter() - started) * 1000),
            )
            yield _ndjson({
                "type": "meta", "request_id": request_id, "session_id": session_id,
                "source": source, "mode": mode, "refs": refs,
                "retrieval_ms": retrieval_ms,
            })
            messages = agent.build_answer_messages(text, history, decision)
            prompt_stats = agent.message_stats(messages)
            logger.info(
                "chat.send.prompt session_id=%s request_id=%s message_count=%s char_count=%s history_messages=%s message_lengths=%s",
                session_id, request_id, prompt_stats["message_count"], prompt_stats["char_count"],
                prompt_stats["history_messages"], prompt_stats["message_lengths"],
            )
            stream = agent.stream_messages(messages)
            yield _ndjson({
                "type": "status", "request_id": request_id, "stage": "generating",
                "source": source, "mode": mode, "retrieval_ms": retrieval_ms,
                "message_count": prompt_stats["message_count"],
                "prompt_chars": prompt_stats["char_count"],
                "history_messages": prompt_stats["history_messages"],
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            })
            logger.info(
                "chat.send.model_stream.start session_id=%s request_id=%s source=%s mode=%s retrieval_ms=%s elapsed_ms=%s",
                session_id, request_id, source, mode, retrieval_ms,
                int((time.perf_counter() - started) * 1000),
            )
            for delta in stream:
                if first_delta_ms is None:
                    first_delta_ms = int((time.perf_counter() - started) * 1000)
                    yield _ndjson({
                        "type": "status", "request_id": request_id, "stage": "first_delta",
                        "source": source, "mode": mode, "retrieval_ms": retrieval_ms,
                        "first_delta_ms": first_delta_ms,
                        "elapsed_ms": first_delta_ms,
                    })
                    logger.info(
                        "chat.send.first_delta session_id=%s request_id=%s source=%s mode=%s retrieval_ms=%s first_delta_ms=%s",
                        session_id, request_id, source, mode, retrieval_ms, first_delta_ms,
                    )
                acc += delta
                yield _ndjson({"type": "delta", "request_id": request_id, "text": delta})
            saved = chat_store.add_message(
                session_id, "assistant", acc,
                answer_source=source, retrieval_mode=mode, refs=refs,
                elapsed_ms=retrieval_ms,
            )
            yield _ndjson({
                "type": "done", "request_id": request_id, "message_id": saved["id"],
                "source": source, "mode": mode, "refs": refs,
                "retrieval_ms": retrieval_ms, "first_delta_ms": first_delta_ms,
            })
            logger.info(
                "chat.send.done session_id=%s request_id=%s source=%s mode=%s chars=%s retrieval_ms=%s first_delta_ms=%s elapsed_ms=%s",
                session_id, request_id, source, mode, len(acc), retrieval_ms, first_delta_ms,
                int((time.perf_counter() - started) * 1000),
            )
        except Exception as e:
            logger.exception("chat.send.error session_id=%s request_id=%s", session_id, request_id)
            # 已生成的部分仍尽量落库,便于运营回溯
            if acc.strip():
                try:
                    chat_store.add_message(session_id, "assistant", acc,
                                           answer_source=source, retrieval_mode=mode, refs=refs)
                except Exception:
                    logger.exception("chat persist partial answer failed")
            yield _ndjson({"type": "error", "request_id": request_id, "error": str(e)})

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"X-Request-ID": request_id, "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/messages/{message_id}/feedback")
def chat_feedback(message_id: str, req: FeedbackReq):
    if req.rating not in ("up", "down"):
        raise HTTPException(400, "rating 必须为 up 或 down")
    msg = chat_store.message_exists(message_id)
    if not msg:
        raise HTTPException(404, "消息不存在")
    if msg["role"] != "assistant":
        raise HTTPException(400, "只能对 Agent 回复反馈")
    reason = (req.reason or "").strip() or None
    if req.rating == "down" and not reason:
        raise HTTPException(400, "点踩请填写原因")
    return chat_store.set_feedback(message_id, msg["session_id"], req.rating, reason)


# ---------------- 静态前端 ----------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
