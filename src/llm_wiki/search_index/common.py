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

from .aho_corasick import Automaton

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
    "ExactMatcher",
    "order_exact_case_ids",
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

    def warm_exact_index(self) -> None:
        """预热精确命中索引(AC 自动机);默认空实现,启动时由 server 调用。"""
        return None


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


class ExactMatcher:
    """signature 精确命中匹配器:小写建 AC 自动机,命中后展开回 (case_id, 原文 signature)。

    与原先 `LOCATE/instr(signature, log)` 逐条扫描语义一致(忽略大小写的子串命中),
    但匹配耗时与 signature 数量基本无关。t_case_signatures 已按长度门控,这里直接继承。
    两个后端(SQLite / MySQL)共用本类,差别只在「从各自的表里把 (case_id, signature) 读出来」。
    """

    def __init__(self) -> None:
        self._ac = Automaton()
        self._index: dict[str, list] = {}  # signature 小写 -> [(case_id, 原文 signature)]

    def add(self, case_id: str, signature: str) -> None:
        sig = str(signature)
        key = sig.lower()
        if key not in self._index:
            self._ac.add(key)
            self._index[key] = []
        self._index[key].append((case_id, sig))

    def build(self) -> None:
        self._ac.build()

    def __len__(self) -> int:
        """AC 自动机里加载的 signature 总数(= t_case_signatures 行数),用于检索结果标识。"""
        return sum(len(v) for v in self._index.values())

    def match(self, log: str) -> dict:
        """返回 {case_id: [命中的原文 signature, ...]}。"""
        matched: dict[str, list] = {}
        for key in self._ac.iter_matches(log.lower()):
            for cid, sig in self._index.get(key, ()):
                matched.setdefault(cid, []).append(sig)
        return matched

    @classmethod
    def from_rows(cls, rows) -> ExactMatcher:
        """rows: 可迭代的 (case_id, signature);构建并定型自动机。"""
        m = cls()
        for case_id, signature in rows:
            if case_id and signature:
                m.add(case_id, signature)
        m.build()
        return m


def order_exact_case_ids(matched: dict, limit: int) -> list:
    """精确命中按相关性排序并截断:命中 signature 数 > 最长 signature 长度 > case_id。

    否则一条通用 signature 命中多案例时会无序、无界返回,破坏 search(log, limit) 契约。
    """
    return sorted(
        matched.items(),
        key=lambda kv: (len(kv[1]), max(len(s) for s in kv[1]), kv[0]),
        reverse=True,
    )[:limit]
