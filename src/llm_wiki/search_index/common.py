#!/usr/bin/env python3
"""检索索引后端共享接口、路径和 Markdown 案例解析。"""
from __future__ import annotations

import datetime
import logging
import os
import pathlib
import re

import yaml

from llm_wiki.common.paths import ROOT

CASES_DIR = ROOT / "wiki" / "cases"
DB_PATH = pathlib.Path(os.environ.get("SEARCH_DB", ROOT / "index" / "search.db"))
SCHEMA_PATH = ROOT / "db" / "schema.sqlite.sql"
MYSQL_SCHEMA_PATH = ROOT / "db" / "schema.mysql.sql"
logger = logging.getLogger("log_wiki.search_index")


class SearchBackend:
    """检索后端接口。SQLite 与 MySQL 实现同一组方法。

    规范化的「案例字典」(index_case 的入参)字段:
      id, file, title, category, status, confidence,
      signatures(list), components(list), background, diagnosis, solution, updated_at
    """

    def available(self) -> bool: raise NotImplementedError
    def reindex_all(self) -> int: raise NotImplementedError
    def index_case(self, case: dict) -> None: raise NotImplementedError
    def remove_case(self, case_id: str) -> None: raise NotImplementedError
    def search(self, log: str, limit: int = 3) -> dict | None: raise NotImplementedError
    def stats(self) -> dict: raise NotImplementedError
    def label(self) -> str: raise NotImplementedError


def section(body: str, title: str) -> str:
    m = re.search(rf"##\s*{re.escape(title)}\s*\n(.*?)(?=\n##\s|\Z)", body, re.S)
    return m.group(1).strip() if m else ""


def case_from_file(path: pathlib.Path) -> dict | None:
    """解析单个 wiki/cases/*.md 为规范化案例字典;非案例文件返回 None。"""
    if path.name in ("index.md", "log.md"):
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    try:
        _, fm_text, body = text.split("---", 2)
    except ValueError:
        return None
    fm = yaml.safe_load(fm_text) or {}
    sigs = fm.get("signatures") or []
    if isinstance(sigs, str):
        sigs = [sigs]
    comps = fm.get("components") or []
    if isinstance(comps, str):
        comps = [comps]
    return {
        "id": path.stem,
        "file": str(path.relative_to(ROOT)),
        "title": fm.get("title") or path.stem,
        "category": fm.get("category") or "未分类",
        "status": fm.get("status") or "verified",
        "confidence": fm.get("confidence") or "unknown",
        "signatures": [str(s) for s in sigs if str(s).strip()],
        "components": [str(c) for c in comps if str(c).strip()],
        "background": section(body, "问题背景"),
        "diagnosis": section(body, "定位过程"),
        "solution": section(body, "解决方案"),
        "updated_at": datetime.datetime.fromtimestamp(
            path.stat().st_mtime).isoformat(timespec="seconds"),
    }


def annotate(status: str, confidence: str) -> str:
    notes = []
    if status == "draft":
        notes.append("⚠ 该案例尚未复核(draft),仅供参考")
    elif status == "verified":
        notes.append("✓ 已复核(verified)")
    if confidence in ("low", "medium"):
        notes.append(f"置信度 {confidence},建议结合实际验证")
    return " | ".join(notes)


def done(started, payload: dict) -> dict:
    import time
    payload["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return payload
