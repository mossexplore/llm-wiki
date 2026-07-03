#!/usr/bin/env python3
"""
search_index.py — 检索索引后端入口。

chat 分支:固定使用 MySQL FULLTEXT(mysql_backend),不提供 SQLite。

可单独运行(便于排障):
    python -m llm_wiki.search_index reindex
    python -m llm_wiki.search_index search "报错文本"
    python -m llm_wiki.search_index stats
"""

from __future__ import annotations

import json
import logging
import sys

from llm_wiki.search_index.common import SearchBackend, case_from_file, exact_signatures, logger
from llm_wiki.search_index.mysql_backend import MySQLSearch

# case_from_file / exact_signatures 在此聚合再导出,供 query.py 复用。
__all__ = ["SearchBackend", "case_from_file", "exact_signatures", "logger", "get_backend", "make_backend"]


def make_backend() -> SearchBackend:
    return MySQLSearch()


_backend: SearchBackend | None = None  # 惰性单例:首次用到检索时才按配置构建后端


def get_backend() -> SearchBackend:
    """返回进程内共享的检索后端;首次调用时按配置构建。

    惰性化是为了「import search_index ≠ 立刻建后端读配置」—— 只引用 case_from_file 等
    纯函数(如纯对话路径)时,不会触发后端初始化的副作用。
    """
    global _backend
    if _backend is None:
        _backend = make_backend()
    return _backend


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    backend = get_backend()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    try:
        if cmd == "reindex":
            n = backend.reindex_all()
            logger.info("已从 wiki/cases/ 重建索引: %s 条案例 -> %s", n, backend.label())
        elif cmd == "search":
            if len(sys.argv) < 3:
                sys.exit('用法: python -m llm_wiki.search_index search "报错文本"')
            logger.info(json.dumps(backend.search(sys.argv[2]), ensure_ascii=False, indent=2))
        elif cmd == "stats":
            if not backend.available():
                sys.exit("当前检索索引后端不可用;query.py 会回退到文件扫描。")
            stats = backend.stats()
            logger.info(
                "backend=%s\nDB=%s\ncases=%s signatures=%s",
                stats["backend"],
                stats["db"],
                stats["cases"],
                stats["signatures"],
            )
        else:
            sys.exit("用法: python -m llm_wiki.search_index [reindex|search <text>|stats]")
    except SystemExit:
        raise
    except Exception as exc:
        logger.exception("search_index.cli.error cmd=%s", cmd)
        sys.exit(f"检索索引命令执行失败: {exc.__class__.__name__}")


if __name__ == "__main__":
    main()
