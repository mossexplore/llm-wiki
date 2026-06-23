#!/usr/bin/env python3
"""对话(Agent)运营数据持久化入口。

默认 SQLite(db/chat.db);配置 storage.backend=mysql 后切到 MySQL。两后端共享
base.BaseChatStore 的 CRUD 编排,只在连接/建表/迁移上分方言(sqlite_store / mysql_store)。

可单独排障:
    python -m llm_wiki.chat_store stats
"""
from __future__ import annotations

import logging
import sys

from llm_wiki.common import storage_config

from .base import BaseChatStore
from .common import MessageMetrics, logger
from .mysql_store import MySQLChatStore
from .sqlite_store import SqliteChatStore

__all__ = [
    "MessageMetrics", "create_session", "list_sessions", "session_exists", "has_messages",
    "rename_session", "delete_session", "clear_sessions", "add_message", "get_messages",
    "message_exists", "set_feedback", "stats",
]

_stores: dict = {}


def _backend() -> BaseChatStore:
    """按当前配置返回(并缓存)对应方言的 store 实例。"""
    name = storage_config.storage_backend()
    if name not in _stores:
        _stores[name] = MySQLChatStore() if name == "mysql" else SqliteChatStore()
    return _stores[name]


def create_session(title: str = "新会话", user_id: str | None = None,
                   source_code: str = "web") -> dict:
    return _backend().create_session(title, user_id, source_code)


def list_sessions() -> list:
    return _backend().list_sessions()


def session_exists(session_id: str) -> bool:
    return _backend().session_exists(session_id)


def has_messages(session_id: str) -> bool:
    return _backend().has_messages(session_id)


def rename_session(session_id: str, title: str) -> None:
    _backend().rename_session(session_id, title)


def delete_session(session_id: str) -> bool:
    return _backend().delete_session(session_id)


def clear_sessions() -> dict:
    return _backend().clear_sessions()


def add_message(session_id: str, role: str, content: str,
                metrics: MessageMetrics | None = None, *, user_id: str | None = None) -> dict:
    return _backend().add_message(session_id, role, content, metrics, user_id=user_id)


def get_messages(session_id: str) -> list:
    return _backend().get_messages(session_id)


def message_exists(message_id: str) -> dict | None:
    return _backend().message_exists(message_id)


def set_feedback(message_id: str, session_id: str, rating: str, reason: str | None = None,
                 user_id: str | None = None) -> dict:
    return _backend().set_feedback(message_id, session_id, rating, reason, user_id)


def stats() -> dict:
    return _backend().stats()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        logger.info("chat_store backend=%s stats=%s", storage_config.storage_backend(), stats())
    else:
        logger.info("用法: python -m llm_wiki.chat_store stats")


if __name__ == "__main__":
    main()
