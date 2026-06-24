#!/usr/bin/env python3
"""SQLite 对话存储方言。默认后端,数据写入 db/chat.db。

只负责 SQLite 特有的连接管理、建表与轻量迁移;增删改查编排在 base.BaseChatStore。
"""

from __future__ import annotations

import pathlib
import sqlite3
from contextlib import contextmanager

from .base import BaseChatStore
from .common import DB_PATH, SCHEMA_PATH, logger

MESSAGE_LATENCY_COLUMNS = {
    "retrieval_ms": "INTEGER",
    "model_wait_ms": "INTEGER",
    "first_delta_ms": "INTEGER",
    "total_ms": "INTEGER",
    "message_count": "INTEGER",
    "prompt_chars": "INTEGER",
}
CHAT_COLUMN_DEFINITIONS = {
    "t_chat_sessions": {"user_id": "TEXT", "source_code": "TEXT NOT NULL DEFAULT 'web'"},
    "t_chat_messages": {"user_id": "TEXT"},
    "t_chat_feedbacks": {"user_id": "TEXT", "feedback": "TEXT"},
}


class _SqliteConn:
    """把 stdlib sqlite3 连接适配成 base 需要的 all/one/run 三方法(命名参数)。"""

    def __init__(self, conn: sqlite3.Connection):
        self._c = conn

    def all(self, sql: str, params: dict | None = None) -> list:
        return [dict(r) for r in self._c.execute(sql, params or {}).fetchall()]

    def one(self, sql: str, params: dict | None = None) -> dict | None:
        row = self._c.execute(sql, params or {}).fetchone()
        return dict(row) if row is not None else None

    def run(self, sql: str, params: dict | None = None) -> int:
        return self._c.execute(sql, params or {}).rowcount


class SqliteChatStore(BaseChatStore):
    BACKEND = "sqlite"

    def __init__(self, db_path: pathlib.Path = DB_PATH):
        self.db_path = pathlib.Path(db_path)
        self._initialized = False

    def label(self) -> str:
        return str(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")  # 写并发下等待 5s 而非立即 'database is locked'
        conn.execute("PRAGMA journal_mode = WAL")  # 读写并发更友好(WAL 一经设置即持久)
        return conn

    @contextmanager
    def _tx(self):
        self._ensure_schema()
        conn = self._connect()
        try:
            yield _SqliteConn(conn)
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        """首次连接时建表 + 迁移;失败只记日志(与历史行为一致,不阻断)。"""
        if self._initialized:
            return
        conn = self._connect()
        try:
            _rebuild_feedbacks_if_needed(conn)
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            _migrate(conn)
            conn.commit()
            self._initialized = True
        except Exception:
            logger.exception("chat_store sqlite schema init failed db=%s", self.db_path)
        finally:
            conn.close()


def _add_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, typ in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移运行库:补齐新增运营字段,保留已有对话数据。"""
    _rebuild_feedbacks_if_needed(conn)
    for table, columns in CHAT_COLUMN_DEFINITIONS.items():
        _add_columns(conn, table, columns)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON t_chat_sessions(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_source ON t_chat_sessions(source_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON t_chat_messages(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_feedbacks_user ON t_chat_feedbacks(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_feedbacks_feedback ON t_chat_feedbacks(feedback)")
    conn.execute("UPDATE t_chat_feedbacks SET feedback='like' WHERE feedback='up'")
    conn.execute("UPDATE t_chat_feedbacks SET feedback='dislike' WHERE feedback='down'")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(t_chat_messages)").fetchall()}
    for name, typ in MESSAGE_LATENCY_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE t_chat_messages ADD COLUMN {name} {typ}")
    old_feedback = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='t_chat_feedback'").fetchone()
    if old_feedback:
        conn.execute("DROP TABLE t_chat_feedback")


def _rebuild_feedbacks_if_needed(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS t_chat_feedback")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(t_chat_feedbacks)").fetchall()}
    if "rating" not in cols and "feedback" in cols:
        return
    conn.execute("DROP TABLE IF EXISTS t_chat_feedbacks")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS t_chat_feedbacks (
          id          TEXT PRIMARY KEY,
          message_id  TEXT NOT NULL UNIQUE,
          session_id  TEXT NOT NULL,
          user_id     TEXT,
          feedback    TEXT NOT NULL,
          reason      TEXT,
          created_at  TEXT NOT NULL,
          updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chat_feedbacks_feedback ON t_chat_feedbacks(feedback);
        CREATE INDEX IF NOT EXISTS idx_chat_feedbacks_session ON t_chat_feedbacks(session_id);
        CREATE INDEX IF NOT EXISTS idx_chat_feedbacks_user ON t_chat_feedbacks(user_id);
        """
    )
