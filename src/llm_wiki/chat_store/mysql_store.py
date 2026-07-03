#!/usr/bin/env python3
"""MySQL 对话存储方言。由 storage.backend=mysql 启用。

只负责 MySQL 特有的连接、建表与迁移;增删改查编排在 base.BaseChatStore。
"""

from __future__ import annotations

import re
from contextlib import contextmanager

from llm_wiki.common.mysql_client import (
    _sql_text,
    get_mysql_client,
    get_mysql_label,
    run_mysql_schema,
)

from .base import BaseChatStore
from .common import MYSQL_SCHEMA_PATH, logger, now

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

CHAT_COLUMN_DEFINITIONS = {
    "t_chat_sessions": {
        "user_id": "VARCHAR(64) COMMENT '用户 id, 标识该会话归属的用户'",
        "source_code": (
            "VARCHAR(64) NOT NULL DEFAULT 'web' COMMENT '会话来源编码, 关联 t_session_sources.code'"
        ),
    },
    "t_chat_messages": {
        "user_id": "VARCHAR(64) COMMENT '用户 id, 标识该消息归属的用户'",
    },
    "t_chat_feedbacks": {
        "user_id": "VARCHAR(64) COMMENT '用户 id, 标识该反馈归属的用户'",
    },
}


def _quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"invalid SQL identifier: {identifier!r}")
    return f"`{identifier}`"


class _MySQLConn:
    """把 SQLAlchemy 连接适配成 base 需要的 all/one/run 三方法(命名参数)。"""

    def __init__(self, conn):
        self._c = conn

    def all(self, sql: str, params: dict | None = None) -> list:
        return [dict(r) for r in self._c.execute(_sql_text(sql), params or {}).mappings().all()]

    def one(self, sql: str, params: dict | None = None) -> dict | None:
        row = self._c.execute(_sql_text(sql), params or {}).mappings().first()
        return dict(row) if row is not None else None

    def run(self, sql: str, params: dict | None = None) -> int:
        return self._c.execute(_sql_text(sql), params or {}).rowcount


class MySQLChatStore(BaseChatStore):
    BACKEND = "mysql"

    def __init__(self):
        self._initialized = False

    def label(self) -> str:
        return get_mysql_label()

    @contextmanager
    def _tx(self):
        self._ensure_schema()
        with get_mysql_client().begin() as conn:
            yield _MySQLConn(conn)

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        try:
            with get_mysql_client().begin() as conn:
                run_mysql_schema(conn, MYSQL_SCHEMA_PATH)
                _migrate_columns(conn)
                _migrate_feedback_table(conn)
                _migrate_feedback_columns(conn)
                _ensure_default_session_source(conn)
            self._initialized = True
        except Exception:
            logger.exception("chat_store mysql schema init failed db=%s", get_mysql_label())
            raise


def _column_exists(conn, table: str, column: str) -> bool:
    row = (
        conn.execute(
            _sql_text(
                """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"""
            ),
            {"t": table, "c": column},
        )
        .mappings()
        .one()
    )
    return row["n"] > 0


def _column_varchar_length(conn, table: str, column: str) -> int | None:
    row = (
        conn.execute(
            _sql_text(
                """SELECT CHARACTER_MAXIMUM_LENGTH AS length FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"""
            ),
            {"t": table, "c": column},
        )
        .mappings()
        .first()
    )
    return row["length"] if row else None


def _add_index_if_missing(conn, table: str, index: str, column: str) -> None:
    row = (
        conn.execute(
            _sql_text(
                """SELECT COUNT(*) AS n FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"""
            ),
            {"t": table, "i": index},
        )
        .mappings()
        .one()
    )
    if row["n"] == 0:
        conn.execute(
            _sql_text(
                f"ALTER TABLE {_quote_identifier(table)} "
                f"ADD INDEX {_quote_identifier(index)} ({_quote_identifier(column)})"
            )
        )


def _migrate_columns(conn) -> None:
    for table, columns in CHAT_COLUMN_DEFINITIONS.items():
        for column, definition in columns.items():
            if not _column_exists(conn, table, column):
                conn.execute(
                    _sql_text(
                        f"ALTER TABLE {_quote_identifier(table)} "
                        f"ADD COLUMN {_quote_identifier(column)} {definition}"
                    )
                )
            elif _column_varchar_length(conn, table, column) != 64:
                conn.execute(
                    _sql_text(
                        f"ALTER TABLE {_quote_identifier(table)} "
                        f"MODIFY COLUMN {_quote_identifier(column)} {definition}"
                    )
                )
    _add_index_if_missing(conn, "t_chat_sessions", "idx_chat_sessions_user", "user_id")
    _add_index_if_missing(conn, "t_chat_sessions", "idx_chat_sessions_source", "source_code")
    _add_index_if_missing(conn, "t_chat_messages", "idx_chat_messages_user", "user_id")
    _add_index_if_missing(conn, "t_chat_feedbacks", "idx_chat_feedbacks_user", "user_id")


def _migrate_feedback_table(conn) -> None:
    old_feedback = (
        conn.execute(
            _sql_text(
                """SELECT COUNT(*) AS n FROM information_schema.TABLES
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"""
            ),
            {"t": "t_chat_feedback"},
        )
        .mappings()
        .one()["n"]
    )
    if old_feedback:
        conn.execute(_sql_text("DROP TABLE t_chat_feedback"))


def _migrate_feedback_columns(conn) -> None:
    if _column_exists(conn, "t_chat_feedbacks", "rating") or not _column_exists(
        conn, "t_chat_feedbacks", "feedback"
    ):
        conn.execute(_sql_text("DROP TABLE t_chat_feedbacks"))
        run_mysql_schema(conn, MYSQL_SCHEMA_PATH)
        return
    _add_index_if_missing(conn, "t_chat_feedbacks", "idx_chat_feedbacks_feedback", "feedback")
    conn.execute(_sql_text("UPDATE t_chat_feedbacks SET feedback='like' WHERE feedback='up'"))
    conn.execute(
        _sql_text("UPDATE t_chat_feedbacks SET feedback='unlike' WHERE feedback IN ('down', 'dislike')")
    )


def _ensure_default_session_source(conn) -> None:
    ts = now()
    conn.execute(
        _sql_text(
            """INSERT IGNORE INTO t_session_sources
               (code, service, scene, description, enabled, created_at, updated_at)
               VALUES (:code,:service,:scene,:description,:enabled,:created_at,:updated_at)"""
        ),
        {
            "code": "web",
            "service": "wiserec-wiki",
            "scene": "chat",
            "description": "Web 页面聊天入口",
            "enabled": 1,
            "created_at": ts,
            "updated_at": ts,
        },
    )
