#!/usr/bin/env python3
"""MySQL 对话存储实现。由 storage.backend=mysql 启用。"""
from __future__ import annotations

import json

from shared import storage_config
from chat_storage.common import MYSQL_SCHEMA_PATH, logger, new_id, now

_initialized = False


def label() -> str:
    cfg = storage_config.mysql_config()
    return f"mysql://{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}"


def _pymysql():
    try:
        import pymysql
        import pymysql.cursors
        return pymysql
    except ImportError as exc:
        raise RuntimeError("使用 MySQL 存储需安装 PyMySQL: pip install PyMySQL") from exc


def _connect():
    """打开 MySQL 连接并在首次使用时初始化对话表。"""
    global _initialized
    pymysql = _pymysql()
    conn = pymysql.connect(
        **storage_config.mysql_connection_kwargs(),
        cursorclass=pymysql.cursors.DictCursor,
    )
    if not _initialized:
        try:
            _init_schema(conn)
            _initialized = True
        except Exception:
            conn.close()
            logger.exception("chat_store mysql schema init failed db=%s", label())
            raise
    return conn


def _init_schema(conn) -> None:
    """执行 MySQL 对话表初始化脚本。"""
    with conn.cursor() as cur:
        for statement in MYSQL_SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
            statement = statement.strip()
            if statement:
                cur.execute(statement)
    conn.commit()


def create_session(title: str = "新会话") -> dict:
    sid = new_id()
    ts = now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (%s,%s,%s,%s)",
                (sid, title or "新会话", ts, ts),
            )
        conn.commit()
    finally:
        conn.close()
    return {"id": sid, "title": title or "新会话", "created_at": ts, "updated_at": ts, "message_count": 0}


def list_sessions() -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.id, s.title, s.created_at, s.updated_at,
                          (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
                   FROM chat_sessions s
                   ORDER BY s.updated_at DESC"""
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def session_exists(session_id: str) -> bool:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM chat_sessions WHERE id=%s", (session_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def has_messages(session_id: str) -> bool:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM chat_messages WHERE session_id=%s LIMIT 1", (session_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def rename_session(session_id: str, title: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE chat_sessions SET title=%s, updated_at=%s WHERE id=%s",
                (title, now(), session_id),
            )
        conn.commit()
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_sessions WHERE id=%s", (session_id,))
            deleted = cur.rowcount > 0
            cur.execute("DELETE FROM chat_messages WHERE session_id=%s", (session_id,))
            cur.execute("DELETE FROM chat_feedback WHERE session_id=%s", (session_id,))
        conn.commit()
        return deleted
    finally:
        conn.close()


def clear_sessions() -> dict:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM chat_sessions")
            sessions = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM chat_messages")
            messages = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM chat_feedback")
            feedback = cur.fetchone()["n"]
            cur.execute("DELETE FROM chat_feedback")
            cur.execute("DELETE FROM chat_messages")
            cur.execute("DELETE FROM chat_sessions")
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
                history_messages: int | None = None) -> dict:
    mid = new_id()
    ts = now()
    refs_json = json.dumps(refs, ensure_ascii=False) if refs else None
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM chat_messages WHERE session_id=%s",
                (session_id,),
            )
            seq = cur.fetchone()["next"]
            cur.execute(
                """INSERT INTO chat_messages
                   (id, session_id, seq, role, content, answer_source, retrieval_mode, refs,
                    elapsed_ms, retrieval_ms, model_wait_ms, first_delta_ms, total_ms,
                    message_count, prompt_chars, history_messages, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (mid, session_id, seq, role, content, answer_source, retrieval_mode, refs_json,
                 elapsed_ms, retrieval_ms, model_wait_ms, first_delta_ms, total_ms,
                 message_count, prompt_chars, history_messages, ts),
            )
            cur.execute("UPDATE chat_sessions SET updated_at=%s WHERE id=%s", (ts, session_id))
        conn.commit()
    finally:
        conn.close()
    return {
        "id": mid, "session_id": session_id, "seq": seq, "role": role, "content": content,
        "answer_source": answer_source, "retrieval_mode": retrieval_mode,
        "refs": refs or [], "elapsed_ms": elapsed_ms, "retrieval_ms": retrieval_ms,
        "model_wait_ms": model_wait_ms, "first_delta_ms": first_delta_ms, "total_ms": total_ms,
        "message_count": message_count, "prompt_chars": prompt_chars,
        "history_messages": history_messages, "created_at": ts,
    }


def get_messages(session_id: str) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.*, f.rating AS feedback_rating, f.reason AS feedback_reason
                   FROM chat_messages m
                   LEFT JOIN chat_feedback f ON f.message_id = m.id
                   WHERE m.session_id=%s ORDER BY m.seq ASC""",
                (session_id,),
            )
            out = []
            for d in cur.fetchall():
                d["refs"] = json.loads(d["refs"]) if d.get("refs") else []
                out.append(d)
            return out
    finally:
        conn.close()


def message_exists(message_id: str) -> dict | None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, session_id, role FROM chat_messages WHERE id=%s", (message_id,))
            return cur.fetchone()
    finally:
        conn.close()


def set_feedback(message_id: str, session_id: str, rating: str, reason: str | None = None) -> dict:
    ts = now()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM chat_feedback WHERE message_id=%s", (message_id,))
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    "UPDATE chat_feedback SET rating=%s, reason=%s, updated_at=%s WHERE message_id=%s",
                    (rating, reason, ts, message_id),
                )
                fid = existing["id"]
            else:
                fid = new_id()
                cur.execute(
                    """INSERT INTO chat_feedback
                       (id, message_id, session_id, rating, reason, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (fid, message_id, session_id, rating, reason, ts, ts),
                )
        conn.commit()
    finally:
        conn.close()
    return {"id": fid, "message_id": message_id, "rating": rating, "reason": reason}


def stats() -> dict:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            def one(q: str) -> int:
                cur.execute(q)
                return cur.fetchone()["n"]

            return {
                "backend": "mysql",
                "db": label(),
                "sessions": one("SELECT count(*) AS n FROM chat_sessions"),
                "messages": one("SELECT count(*) AS n FROM chat_messages"),
                "up": one("SELECT count(*) AS n FROM chat_feedback WHERE rating='up'"),
                "down": one("SELECT count(*) AS n FROM chat_feedback WHERE rating='down'"),
            }
    finally:
        conn.close()
