import datetime
import pathlib
import re

from fastapi import APIRouter, HTTPException
import yaml  # noqa: E402

from ..config import CASES_DIR, ROOT
from ..schemas import KnowledgeUpdateReq
from ..search_sync import index_case_file, index_remove

from llm_wiki.knowledge import ingest  # noqa: E402

router = APIRouter()


def split_case(path: pathlib.Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    try:
        _, fm, body = text.split("---", 2)
    except ValueError:
        return {}, text
    return yaml.safe_load(fm) or {}, body


def section(body: str, title: str) -> str:
    pattern = rf"##\s*{re.escape(title)}\s*\n(.*?)(?=\n##\s|\Z)"
    m = re.search(pattern, body, re.S)
    return m.group(1).strip() if m else ""


def replace_section(body: str, title: str, content: str) -> str:
    heading = f"## {title}\n"
    next_block = f"{heading}{content.strip()}\n"
    pattern = rf"(##\s*{re.escape(title)}\s*\n)(.*?)(?=\n##\s|\Z)"
    if re.search(pattern, body, re.S):
        return re.sub(pattern, lambda _: next_block, body, count=1, flags=re.S)
    return f"\n{next_block}\n{body.lstrip()}"


def case_path(case_file: str) -> pathlib.Path:
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


def case_detail(path: pathlib.Path) -> dict:
    fm, body = split_case(path)
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
        "background": section(body, "问题背景"),
        "diagnosis": section(body, "定位过程"),
        "solution": section(body, "解决方案"),
        "ident": path.stem,
        "raw": raw,
        "sources": sources,
        "updated": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def knowledge_markdown(req: KnowledgeUpdateReq, existing: dict, existing_body: str) -> str:
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
    body = replace_section(body, "问题背景", req.background)
    body = replace_section(body, "定位过程", req.diagnosis)
    body = replace_section(body, "解决方案", req.solution)
    body = replace_section(body, "Citations", citations)
    return f"---\n{front}---\n\n{body}"


@router.get("/api/knowledge")
def knowledge_list():
    items = []
    for path in sorted(CASES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name in ("index.md", "log.md"):
            continue
        fm, body = split_case(path)
        if fm.get("status", "verified") != "verified":
            continue
        stat = path.stat()
        items.append({
            "file": str(path.relative_to(ROOT)),
            "title": fm.get("title") or path.stem,
            "category": fm.get("category") or "未分类",
            "description": fm.get("description") or section(body, "问题背景"),
            "status": fm.get("status") or "verified",
            "confidence": fm.get("confidence") or "unknown",
            "signatures": fm.get("signatures") or [],
            "components": fm.get("components") or [],
            "created": fm.get("created") or "",
            "timestamp": fm.get("timestamp") or "",
            "updated": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return {"items": items}


@router.delete("/api/knowledge")
def knowledge_clear():
    """清空全部已入库知识;raw/ 不可变层原文保留以备溯源。"""
    deleted = []
    for path in sorted(CASES_DIR.glob("*.md")):
        if path.name in ("index.md", "log.md"):
            continue
        rel = str(path.relative_to(ROOT))
        path.unlink()
        index_remove(path)
        deleted.append(rel)
    ingest.update_indexes()
    return {"ok": True, "deleted": len(deleted), "files": deleted}


@router.get("/api/knowledge/{case_file:path}")
def knowledge_detail(case_file: str):
    return case_detail(case_path(case_file))


@router.delete("/api/knowledge/{case_file:path}")
def knowledge_delete(case_file: str):
    """删除一条已入库知识;raw/ 不可变层原文保留以备溯源。"""
    path = case_path(case_file)
    path.unlink()
    ingest.update_indexes()
    index_remove(path)
    return {"ok": True, "case_file": str(path.relative_to(ROOT))}


@router.put("/api/knowledge/{case_file:path}")
def knowledge_update(case_file: str, req: KnowledgeUpdateReq):
    if not req.title.strip():
        raise HTTPException(400, "title 不能为空")
    signatures = [s for s in req.signatures if s and s.strip()]
    if not signatures:
        raise HTTPException(400, "signatures 不能为空(检索全靠它命中)")
    req.signatures = signatures
    req.components = [c for c in req.components if c and c.strip()]
    path = case_path(case_file)
    existing, existing_body = split_case(path)
    path.write_text(knowledge_markdown(req, existing, existing_body), encoding="utf-8")
    ingest.update_indexes()
    index_case_file(path)
    return {"ok": True, "case_file": str(path.relative_to(ROOT))}
