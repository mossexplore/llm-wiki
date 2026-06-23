import datetime
import json
import queue
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from ..app_logging import logger
from ..config import ROOT
from ..schemas import CommitBatchReq, CommitReq, PreviewBatchReq, PreviewReq
from ..search_sync import index_case_file
from ..utils import ndjson

from llm_wiki.knowledge import ingest  # noqa: E402

router = APIRouter()

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


@router.post("/api/ingest/preview")
def ingest_preview(req: PreviewReq, x_request_id: Optional[str] = Header(default=None)):
    """流式返回模型抽取的 JSON 文本;前端据此计算字段进度,此步不写任何文件。"""
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
        except Exception as e:
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


@router.post("/api/ingest/commit")
def ingest_commit(req: CommitReq):
    if not req.title.strip():
        raise HTTPException(400, "title 不能为空")
    if not req.signatures:
        raise HTTPException(400, "signatures 不能为空(检索全靠它命中)")

    ident = req.ident or datetime.datetime.now().strftime("%H%M%S")
    raw_path = ingest.archive_raw(req.raw, ident)
    raw_rel = str(raw_path.relative_to(ROOT))

    md = ingest.to_markdown(req.model_dump(), raw_rel, status="verified", confidence="high")
    slug = ingest.slugify(req.title)
    case_path = ROOT / "wiki" / "cases" / f"{slug}.md"
    case_path.write_text(md, encoding="utf-8")
    ingest.update_indexes()
    index_case_file(case_path)

    return {
        "ok": True,
        "raw_file": raw_rel,
        "case_file": str(case_path.relative_to(ROOT)),
        "slug": slug,
    }


def split_records(raw: str) -> List[str]:
    """按 Markdown 一级标题切分多条原始记录,去空白。"""
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


def commit_one(req: CommitReq, used_slugs: set) -> dict:
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


def normalize_json_text(text: str) -> str:
    txt = (text or "").strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.I).strip()
    txt = re.sub(r"```\s*$", "", txt).strip()
    if not txt.startswith("{"):
        first = txt.find("{")
        last = txt.rfind("}")
        if first != -1 and last > first:
            txt = txt[first:last + 1].strip()
    return txt


def batch_case_record(index: int, raw: str, case: dict) -> dict:
    return {
        "index": index, "raw": raw, "ok": True,
        "title": case.get("title", ""), "category": case.get("category", "未分类"),
        "signatures": case.get("signatures", []) or [], "components": case.get("components", []) or [],
        "background": case.get("background", ""), "diagnosis": case.get("diagnosis", ""),
        "solution": case.get("solution", ""),
    }


@router.post("/api/ingest/preview_batch")
def ingest_preview_batch(req: PreviewBatchReq):
    """切分多条原始记录,并行调用 LLM 抽取;以 NDJSON 流式返回。"""
    records = split_records(req.raw)
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
            case = json.loads(normalize_json_text(acc))
            out.put({
                "type": "done",
                "request_id": request_id,
                "index": i,
                "record": batch_case_record(i, rec, case),
            })
            logger.info(
                "ingest.preview_batch.item.done request_id=%s index=%s chunks=%s chars=%s elapsed_ms=%s",
                request_id, i, chunk_count, len(acc), int((time.perf_counter() - started) * 1000),
            )
        except Exception as e:
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
                yield ndjson(event)
            for fut in futures:
                fut.result()
        logger.info(
            "ingest.preview_batch.done request_id=%s records=%s ok=%s failed=%s elapsed_ms=%s",
            request_id, len(records), ok, failed, int((time.perf_counter() - started) * 1000),
        )
        yield ndjson({"type": "summary", "request_id": request_id, "count": len(records), "ok": ok, "failed": failed})

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"X-Request-ID": request_id, "X-Accel-Buffering": "no"},
    )


@router.post("/api/ingest/commit_batch")
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
            rec.ident = f"{stamp}-{i + 1}"
        try:
            res = commit_one(rec, used)
            index_case_file(ROOT / res["case_file"])
            results.append({"index": i, "ok": True, **res})
        except Exception as e:
            results.append({"index": i, "ok": False, "error": str(e)})
    ingest.update_indexes()
    return {"ok": sum(1 for r in results if r["ok"]), "total": len(results), "results": results}


@router.get("/api/examples/ingest")
def ingest_example():
    return {"raw": SAMPLE_RAW, "case": SAMPLE_CASE}
