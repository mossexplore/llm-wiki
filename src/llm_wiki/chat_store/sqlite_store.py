#!/usr/bin/env python3
"""SQLite 对话存储实现。默认后端,数据写入 db/chat.db。"""
from __future__ import annotations

import json
import sqlite3

from .common import DB_PATH, SCHEMA_PATH, logger, new_id, now

_initialized = False
MESSAGE_LATENCY_COLUMNS = {
    "retrieval_ms": "INTEGER",
    "model_wait_ms": "INTEGER",
    "first_delta_ms": "INTEGER",
    "total_ms": "INTEGER",
    "message_count": "INTEGER",
    "prompt_chars": "INTEGER",
    "history_messages": "INTEGER",
}
CHAT_COLUMN_DEFINITIONS = {
    "t_chat_sessions": {"user_id": "TEXT", "source_code": "TEXT NOT NULL DEFAULT 'web'"},
    "t_chat_messages": {"user_id": "TEXT"},
    "t_chat_feedbacks": {"user_id": "TEXT"},
}


def _connect() -> sqlite3.Connection:
    """打开连接并确保建表;首次调用执行 schema.chat.sql。"""
    global _initialized
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not _initialized:
        try:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            _migrate(conn)
            conn.commit()
            _initialized = True
        except Exception:
            logger.exception("chat_store sqlite schema init failed db=%s", DB_PATH)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移运行库:补齐新增运营字段,保留已有对话数据。"""
    for table, columns in CHAT_COLUMN_DEFINITIONS.items():
        _add_columns(conn, table, columns)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON t_chat_sessions(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_source ON t_chat_sessions(source_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON t_chat_messages(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_feedbacks_user ON t_chat_feedbacks(user_id)")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(t_chat_messages)").fetchall()}
    for name, typ in MESSAGE_LATENCY_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE t_chat_messages ADD COLUMN {name} {typ}")
    old_feedback = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='t_chat_feedback'"
    ).fetchone()
    if old_feedback:
        conn.execute(
            """INSERT OR IGNORE INTO t_chat_feedbacks
               (id, message_id, session_id, rating, reason, created_at, updated_at)
               SELECT id, message_id, session_id, rating, reason, created_at, updated_at
               FROM t_chat_feedback"""
        )
        conn.execute("DROP TABLE t_chat_feedback")


def _add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, typ in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def create_session(title: str = "新会话", user_id: str | None = None,
                   source_code: str = "web") -> dict:
    sid = new_id()
    ts = now()
    source_code = source_code or "web"
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO t_chat_sessions
               (id, user_id, source_code, title, created_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (sid, user_id, source_code, title or "新会话", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": sid, "user_id": user_id, "source_code": source_code, "title": title or "新会话",
        "created_at": ts, "updated_at": ts, "message_count": 0,
    }


def list_sessions() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT s.id, s.user_id, s.source_code, s.title, s.created_at, s.updated_at,
                      (SELECT count(*) FROM t_chat_messages m WHERE m.session_id = s.id) AS message_count
               FROM t_chat_sessions s
               ORDER BY s.updated_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def session_exists(session_id: str) -> bool:
    conn = _connect()
    try:
        return conn.execute("SELECT 1 FROM t_chat_sessions WHERE id=?", (session_id,)).fetchone() is not None
    finally:
        conn.close()


def has_messages(session_id: str) -> bool:
    conn = _connect()
    try:
        return conn.execute("SELECT 1 FROM t_chat_messages WHERE session_id=? LIMIT 1", (session_id,)).fetchone() is not None
    finally:
        conn.close()


def rename_session(session_id: str, title: str) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE t_chat_sessions SET title=?, updated_at=? WHERE id=?",
                     (title, now(), session_id))
        conn.commit()
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM t_chat_sessions WHERE id=?", (session_id,))
        conn.execute("DELETE FROM t_chat_messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM t_chat_feedbacks WHERE session_id=?", (session_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_sessions() -> dict:
    """清空全部对话会话、消息和反馈。"""
    conn = _connect()
    try:
        sessions = conn.execute("SELECT count(*) FROM t_chat_sessions").fetchone()[0]
        messages = conn.execute("SELECT count(*) FROM t_chat_messages").fetchone()[0]
        feedback = conn.execute("SELECT count(*) FROM t_chat_feedbacks").fetchone()[0]
        conn.execute("DELETE FROM t_chat_feedbacks")
        conn.execute("DELETE FROM t_chat_messages")
        conn.execute("DELETE FROM t_chat_sessions")
        conn.commit()
        return {"sessions": sessions, "messages": messages, "feedback": feedback}
    finally:
        conn.close()


def add_message(session_id: str, role: str, content: str,
                answer_source: str | None = None, retrieval_mode: str | None = None,
                refs: list | None = None, elapsed_ms: int | None = None,
                retrieval_ms: int | None = None, model_wait_ms: int | None = None,
                first_delta_ms: int | None = None, total_ms: int | None = None,
                message_count: int | None = None, prompt_chars: int | None = None,
                history_messages: int | None = None,
                user_id: str | None = None) -> dict:
    """追加一条消息,返回完整记录(含生成的 id / seq)。"""
    mid = new_id()
    ts = now()
    refs_json = json.dumps(refs, ensure_ascii=False) if refs else None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM t_chat_messages WHERE session_id=?",
            (session_id,)).fetchone()
        seq = row["next"]
        conn.execute(
            """INSERT INTO t_chat_messages
               (id, session_id, user_id, seq, role, content, answer_source, retrieval_mode, refs,
                elapsed_ms, retrieval_ms, model_wait_ms, first_delta_ms, total_ms,
                message_count, prompt_chars, history_messages, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, session_id, user_id, seq, role, content, answer_source, retrieval_mode, refs_json,
             elapsed_ms, retrieval_ms, model_wait_ms, first_delta_ms, total_ms,
             message_count, prompt_chars, history_messages, ts),
        )
        conn.execute("UPDATE t_chat_sessions SET updated_at=? WHERE id=?", (ts, session_id))
        conn.commit()
    finally:
        conn.close()
    return {
        "id": mid, "session_id": session_id, "user_id": user_id, "seq": seq,
        "role": role, "content": content,
        "answer_source": answer_source, "retrieval_mode": retrieval_mode,
        "refs": refs or [], "elapsed_ms": elapsed_ms, "retrieval_ms": retrieval_ms,
        "model_wait_ms": model_wait_ms, "first_delta_ms": first_delta_ms, "total_ms": total_ms,
        "message_count": message_count, "prompt_chars": prompt_chars,
        "history_messages": history_messages, "created_at": ts,
    }


def get_messages(session_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT m.*, f.rating AS feedback_rating, f.reason AS feedback_reason
               FROM t_chat_messages m
               LEFT JOIN t_chat_feedbacks f ON f.message_id = m.id
               WHERE m.session_id=? ORDER BY m.seq ASC""",
            (session_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["refs"] = json.loads(d["refs"]) if d.get("refs") else []
            out.append(d)
        return out
    finally:
        conn.close()


def message_exists(message_id: str) -> dict | None:
    conn = _connect()
    try:
        r = conn.execute("SELECT id, session_id, user_id, role FROM t_chat_messages WHERE id=?", (message_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def set_feedback(message_id: str, session_id: str, rating: str, reason: str | None = None,
                 user_id: str | None = None) -> dict:
    """记录一条反馈;同一消息重复反馈则覆盖。"""
    ts = now()
    conn = _connect()
    try:
        existing = conn.execute("SELECT id FROM t_chat_feedbacks WHERE message_id=?", (message_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE t_chat_feedbacks SET user_id=COALESCE(?, user_id), rating=?, reason=?, updated_at=? WHERE message_id=?",
                (user_id, rating, reason, ts, message_id))
            fid = existing["id"]
        else:
            fid = new_id()
            conn.execute(
                """INSERT INTO t_chat_feedbacks (id, message_id, session_id, user_id, rating, reason, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (fid, message_id, session_id, user_id, rating, reason, ts, ts))
        conn.commit()
    finally:
        conn.close()
    return {"id": fid, "message_id": message_id, "user_id": user_id, "rating": rating, "reason": reason}


def stats() -> dict:
    conn = _connect()
    try:
        def one(q):
            return conn.execute(q).fetchone()[0]
        return {
            "backend": "sqlite",
            "db": str(DB_PATH),
            "sessions": one("SELECT count(*) FROM t_chat_sessions"),
            "messages": one("SELECT count(*) FROM t_chat_messages"),
            "up": one("SELECT count(*) FROM t_chat_feedbacks WHERE rating='up'"),
            "down": one("SELECT count(*) FROM t_chat_feedbacks WHERE rating='down'"),
        }
    finally:
        conn.close()
