#!/usr/bin/env python3
"""
query.py — 用一段日志报错,从 wiki/cases/ 里找相似案例

策略:
  1) 精确命中:遍历每个案例的 signatures,任一报错串作为子串出现在你的日志里 → 命中。
     (signatures 是知识库精选的检索锚点,比从日志里猜锚点更稳)
  2) 无精确命中 → 优先用已配置的检索索引后端做模糊召回;索引不可用时回退到
     纯文件 token 重合度召回。模糊结果仅作"可能相关"提示,标注需人工判断。
  3) 仍无 → 按命中门控明确告知"暂无相关案例",绝不编造。

用法:
    python -m llm_wiki.knowledge.query "把整段报错粘进来"
    cat error.log | python -m llm_wiki.knowledge.query -
"""

import logging
import re
import sys
import time

from llm_wiki import search_index
from llm_wiki.common import storage_config
from llm_wiki.common.markdown_case import annotate, read_doc, section
from llm_wiki.common.paths import ROOT

CASES_DIR = ROOT / "wiki" / "cases"
logger = logging.getLogger("log_wiki.query")


def load_cases():
    """读取 wiki/cases/ 下所有案例(含 _drafts/),解析 frontmatter + 正文。"""
    cases = []
    for path in sorted(CASES_DIR.rglob("*.md")):
        fm, body = read_doc(path)
        if not fm:
            continue
        sigs = fm.get("signatures") or []
        if isinstance(sigs, str):
            sigs = [sigs]
        cases.append(
            {
                "path": path,
                "title": fm.get("title") or path.stem,
                "status": fm.get("status") or "unknown",
                "confidence": fm.get("confidence") or "unknown",
                "signatures": [str(s).strip() for s in sigs if str(s).strip()],
                "body": body,
            }
        )
    return cases


def solution_of(body: str) -> str:
    """抽取「解决方案」段落(到下一个 ## 或文末)。"""
    return section(body, "解决方案") or "(该案例无「解决方案」段落)"


def tokenize(s: str):
    """切出英文单词(>=4 字母)与数字错误码,小写,用于模糊重合度。"""
    return {t.lower() for t in re.findall(r"[A-Za-z]{4,}|\b\d{3}\b", s)}


def search(log: str) -> dict:
    """检索核心:返回结构化结果,供 CLI 与 web 后端共用。

    返回 {
      "mode": "exact"|"fuzzy"|"none",
      "source": "mysql"|"sqlite"|"files"|"none",
      "hits": [...],
      "elapsed_ms": int,
    }。
    mode=exact 时 hits 含 solution;mode=fuzzy 时仅候选(需人工判断)。
    elapsed_ms 为本次检索的纯后端耗时(不含网络/序列化),单位毫秒。

    优先走 search_index 当前配置的后端(SQLite 或 MySQL);索引不可用时自动回退到
    下面的纯文件扫描,保证默认 SQLite 场景仍可零外部数据库运行。

    storage.local_search=false 时关闭文件兜底:只认数据库索引结果,后端不可用即判无命中。
    """
    res = search_index.backend.search(log)
    if res is not None:
        return res
    if not storage_config.local_search():
        return {"mode": "none", "source": "none", "hits": [], "elapsed_ms": 0}
    return _search_files(log)


def _search_files(log: str) -> dict:
    """纯文件兜底实现:精确 signature 子串命中 → token 重合度模糊召回 → 无命中门控。"""
    log_low = log.lower()
    started = time.perf_counter()

    def _done(payload: dict) -> dict:
        payload["source"] = "files"
        payload["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return payload

    cases = load_cases()

    # 1) 精确命中:signature 作为子串出现在日志里
    exact = []
    for c in cases:
        matched = [s for s in search_index.exact_signatures(c["signatures"]) if s.lower() in log_low]
        if matched:
            exact.append(
                {
                    "title": c["title"],
                    "file": str(c["path"].relative_to(ROOT)),
                    "matched": matched,
                    "status": c["status"],
                    "confidence": c["confidence"],
                    "note": annotate(c["status"], c["confidence"]),
                    "solution": solution_of(c["body"]),
                }
            )
    if exact:
        return _done({"mode": "exact", "hits": exact})

    # 2) 模糊召回:token 重合度
    log_tokens = tokenize(log)
    scored = []
    for c in cases:
        sig_tokens = set().union(*(tokenize(s) for s in c["signatures"])) if c["signatures"] else set()
        score = len(log_tokens & (sig_tokens | tokenize(c["title"])))
        if score:
            scored.append(
                {
                    "title": c["title"],
                    "file": str(c["path"].relative_to(ROOT)),
                    "score": score,
                    "status": c["status"],
                }
            )
    scored.sort(key=lambda x: -x["score"])
    if scored:
        return _done({"mode": "fuzzy", "hits": scored[:3]})

    # 3) 命中门控
    return _done({"mode": "none", "hits": []})


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    arg = sys.argv[1] if len(sys.argv) > 1 else "-"
    log = sys.stdin.read() if arg == "-" else arg
    if not log.strip():
        sys.exit('用法: python -m llm_wiki.knowledge.query "报错信息"  (或用 - 从 stdin 读)')
    if not load_cases():
        sys.exit("wiki/cases/ 下暂无任何案例。")

    res = search(log)
    if res["mode"] == "exact":
        lines = [f"=== 精确命中 {len(res['hits'])} 个案例 ===", ""]
        for h in res["hits"]:
            lines.extend(
                [
                    f"● {h['title']}",
                    f"  文件: {h['file']}",
                    f"  命中 signature: {h['matched']}",
                ]
            )
            if h["note"]:
                lines.append(f"  可信度: {h['note']}")
            lines.extend(["", "  【解决方案】", _indent(h["solution"]), ""])
        logger.info("\n".join(lines))
    elif res["mode"] == "fuzzy":
        lines = ["=== 未精确命中。以下为可能相关案例(仅供参考,需人工判断,勿直接照搬)===", ""]
        for h in res["hits"]:
            lines.append(f"● {h['title']}  (重合度 {h['score']})  {h['file']}")
        lines.extend(["", "建议:用上面案例的 signatures 反向核对你的报错,或接入 QMD 语义检索。"])
        logger.info("\n".join(lines))
    else:
        logger.info(
            "知识库中暂无相关案例。请勿编造解决方案;"
            "排查后可用 python -m llm_wiki.knowledge.ingest 把本次结论入库。"
        )


def _indent(text: str, n: int = 2) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.splitlines())


if __name__ == "__main__":
    main()
