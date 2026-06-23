#!/usr/bin/env python3
"""
chat_store.py — 对话(Agent)运营数据持久化入口。

默认使用 SQLite(db/chat.db);配置 storage.backend=mysql 后切到 MySQL。
具体 SQL 实现分别在 chat_store_sqlite.py 与 chat_store_mysql.py。

可单独排障:
    python -m llm_wiki.chat_store stats
"""
from __future__ import annotations

import logging
import sys

from . import mysql_store, sqlite_store
from .common import logger
from llm_wiki.common import storage_config


def _backend_module():
    if storage_config.storage_backend() == "mysql":
        return mysql_store
    return sqlite_store


def create_session(title: str = "新会话", user_id: str | None = None,
                   source_code: str = "web") -> dict:
    return _backend_module().create_session(title, user_id, source_code)


def list_sessions() -> list[dict]:
    return _backend_module().list_sessions()


def session_exists(session_id: str) -> bool:
    return _backend_module().session_exists(session_id)


def has_messages(session_id: str) -> bool:
    return _backend_module().has_messages(session_id)


def rename_session(session_id: str, title: str) -> None:
    _backend_module().rename_session(session_id, title)


def delete_session(session_id: str) -> bool:
    return _backend_module().delete_session(session_id)


def clear_sessions() -> dict:
    return _backend_module().clear_sessions()


def add_message(session_id: str, role: str, content: str,
                answer_source: str | None = None, retrieval_mode: str | None = None,
                refs: list | None = None, elapsed_ms: int | None = None,
                retrieval_ms: int | None = None, model_wait_ms: int | None = None,
                first_delta_ms: int | None = None, total_ms: int | None = None,
                message_count: int | None = None, prompt_chars: int | None = None,
                history_messages: int | None = None,
                user_id: str | None = None) -> dict:
    return _backend_module().add_message(
        session_id, role, content, answer_source, retrieval_mode, refs, elapsed_ms,
        retrieval_ms, model_wait_ms, first_delta_ms, total_ms, message_count,
        prompt_chars, history_messages, user_id,
    )


def get_messages(session_id: str) -> list[dict]:
    return _backend_module().get_messages(session_id)


def message_exists(message_id: str) -> dict | None:
    return _backend_module().message_exists(message_id)


def set_feedback(message_id: str, session_id: str, rating: str, reason: str | None = None,
                 user_id: str | None = None) -> dict:
    return _backend_module().set_feedback(message_id, session_id, rating, reason, user_id)


def stats() -> dict:
    return _backend_module().stats()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        logger.info("chat_store backend=%s stats=%s", storage_config.storage_backend(), stats())
    else:
        logger.info("用法: python -m llm_wiki.chat_store stats")


if __name__ == "__main__":
    main()
