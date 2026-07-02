#!/usr/bin/env python3
"""对话(Agent)运营数据持久化入口。

chat 分支:固定使用 MySQL(mysql_store),不提供 SQLite。CRUD 编排在
base.BaseChatStore,方言层只负责连接/建表/迁移。

可单独排障:
    python -m llm_wiki.chat_store stats
"""

from __future__ import annotations

import logging
import sys

from .base import BaseChatStore
from .common import MessageMetrics, logger
from .mysql_store import MySQLChatStore

__all__ = [
    "MessageMetrics",
    "create_session",
    "list_sessions",
    "session_exists",
    "has_messages",
    "rename_session",
    "delete_session",
    "clear_sessions",
    "add_message",
    "get_messages",
    "message_exists",
    "set_feedback",
    "clear_feedback",
    "stats",
]

_stores: dict = {}


def _backend() -> BaseChatStore:
    """返回(并缓存)MySQL store 实例;chat 分支只支持 MySQL。"""
    if "mysql" not in _stores:
        _stores["mysql"] = MySQLChatStore()
    return _stores["mysql"]


def create_session(title: str = "新会话", user_id: str | None = None, source_code: str = "web") -> dict:
    return _backend().create_session(title, user_id, source_code)


def list_sessions(user_id: str | None = None) -> list:
    return _backend().list_sessions(user_id)


def session_exists(session_id: str, user_id: str | None = None) -> bool:
    return _backend().session_exists(session_id, user_id)


def has_messages(session_id: str) -> bool:
    return _backend().has_messages(session_id)


def rename_session(session_id: str, title: str) -> None:
    _backend().rename_session(session_id, title)


def delete_session(session_id: str, user_id: str | None = None) -> bool:
    return _backend().delete_session(session_id, user_id)


def clear_sessions(user_id: str | None = None) -> dict:
    return _backend().clear_sessions(user_id)


def add_message(
    session_id: str,
    role: str,
    content: str,
    metrics: MessageMetrics | None = None,
    *,
    user_id: str | None = None,
) -> dict:
    return _backend().add_message(session_id, role, content, metrics, user_id=user_id)


def get_messages(session_id: str) -> list:
    return _backend().get_messages(session_id)


def message_exists(message_id: str, user_id: str | None = None) -> dict | None:
    return _backend().message_exists(message_id, user_id)


def set_feedback(
    message_id: str, session_id: str, feedback: str, reason: str | None = None, user_id: str | None = None
) -> dict:
    return _backend().set_feedback(message_id, session_id, feedback, reason, user_id)


def clear_feedback(message_id: str) -> bool:
    return _backend().clear_feedback(message_id)


def stats() -> dict:
    return _backend().stats()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        logger.info("chat_store backend=mysql stats=%s", stats())
    else:
        logger.info("用法: python -m llm_wiki.chat_store stats")


if __name__ == "__main__":
    main()
