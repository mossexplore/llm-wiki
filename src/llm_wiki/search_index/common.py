#!/usr/bin/env python3
"""检索索引后端共享接口、路径和 Markdown 案例解析。"""

from __future__ import annotations

import datetime
import logging
import os
import pathlib
import re
import time

from llm_wiki.common.markdown_case import annotate, section, split_frontmatter
from llm_wiki.common.paths import ROOT

__all__ = [
    "CASES_DIR",
    "DB_PATH",
    "SCHEMA_PATH",
    "MYSQL_SCHEMA_PATH",
    "logger",
    "SearchBackend",
    "case_from_file",
    "annotate",
    "section",
    "done",
    "exact_signatures",
    "EXACT_SIGNATURE_MIN_LEN",
    "iter_search_tokens",
    "is_cjk",
]

# 检索分词:英文词(>=3 字母)、数字错误码(>=3 位)、连续中文。两个后端共用,
# 之后各自再做后处理(SQLite 切 trigram + 引号,MySQL 截断 + 空格拼)。
_SEARCH_TOKEN_RE = re.compile(r"[A-Za-z]{3,}|\d{3,}|[一-鿿]+")


def iter_search_tokens(log: str) -> list:
    """从日志文本切出检索候选 token,保留出现顺序。"""
    return _SEARCH_TOKEN_RE.findall(log)


def is_cjk(token: str) -> bool:
    """token 是否以中日韩统一表意文字开头(用于区分中英文后处理)。"""
    return bool(token) and "一" <= token[0] <= "鿿"


CASES_DIR = ROOT / "wiki" / "cases"
DB_PATH = pathlib.Path(os.environ.get("SEARCH_DB", ROOT / "index" / "search.db"))
SCHEMA_PATH = ROOT / "db" / "schema.sqlite.sql"
MYSQL_SCHEMA_PATH = ROOT / "db" / "schema.mysql.sql"
logger = logging.getLogger("log_wiki.search_index")

# 精确命中是「signature 作为子串出现在用户日志里」。过短的 signature(如 "500"/"OOM")
# 当锚点几乎对任意日志都成立 → 误报精确命中,还会短路掉 fuzzy 兜底。故只把长度达标的
# signature 写入 t_case_signatures(精确命中专用表);signatures_text / FTS 仍保留全部。
EXACT_SIGNATURE_MIN_LEN = 4


def exact_signatures(signatures) -> list:
    """挑出适合做精确子串命中的 signature:去空白、按长度门控、忽略大小写去重。"""
    out, seen = [], set()
    for s in signatures or []:
        s = str(s).strip()
        if len(s) < EXACT_SIGNATURE_MIN_LEN:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


class SearchBackend:
    """检索后端接口。SQLite 与 MySQL 实现同一组方法。

    规范化的「案例字典」(index_case 的入参)字段:
      id, file, title, category, status, confidence,
      signatures(list), components(list), background, diagnosis, solution, updated_at
    """

    def available(self) -> bool:
        raise NotImplementedError

    def reindex_all(self) -> int:
        raise NotImplementedError

    def index_case(self, case: dict) -> None:
        raise NotImplementedError

    def remove_case(self, case_id: str) -> None:
        raise NotImplementedError

    def search(self, log: str, limit: int = 3) -> dict | None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def label(self) -> str:
        raise NotImplementedError


def case_from_file(path: pathlib.Path) -> dict | None:
    """解析单个 wiki/cases/*.md 为规范化案例字典;非案例文件返回 None。"""
    if path.name in ("index.md", "log.md"):
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    fm, body = split_frontmatter(text)
    if not fm:
        return None
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
        "updated_at": datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
    }


def done(started, payload: dict) -> dict:
    payload["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return payload
