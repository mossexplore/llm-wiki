#!/usr/bin/env python3
"""
search_index.py — 检索索引后端入口。

默认使用 SQLite + FTS5;配置 storage.backend=mysql 后切到 MySQL FULLTEXT。
具体实现分别在 search_index_sqlite.py 与 search_index_mysql.py。

可单独运行(便于排障):
    python -m llm_wiki.search_index reindex
    python -m llm_wiki.search_index search "报错文本"
    python -m llm_wiki.search_index stats
"""
from __future__ import annotations

import json
import logging
import sys

from .common import SearchBackend, case_from_file, logger
from .mysql_backend import MySQLSearch
from .sqlite_backend import SqliteSearch
from llm_wiki.common import storage_config


def make_backend() -> SearchBackend:
    if storage_config.storage_backend() == "mysql":
        return MySQLSearch()
    return SqliteSearch()


# 模块级单例,供 query.py / llm_wiki.backend.server 复用
backend: SearchBackend = make_backend()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
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
            stats["backend"], stats["db"], stats["cases"], stats["signatures"],
        )
    else:
        sys.exit("用法: python -m llm_wiki.search_index [reindex|search <text>|stats]")


if __name__ == "__main__":
    main()
