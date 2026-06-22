#!/usr/bin/env python3
"""MySQL 对话存储实现。由 storage.backend=mysql 启用。"""
from __future__ import annotations

import json

from shared import storage_config
from chat_storage.common import MYSQL_SCHEMA_PATH, logger, new_id, now

_initialized = False
_engine = None


def label() -> str:
    cfg = storage_config.mysql_config()
    return f"mysql://{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}"


def _sqlalchemy():
    try:
        from sqlalchemy import create_engine, text
        return create_engine, text
    except ImportError as exc:
        raise RuntimeError("使用 MySQL 存储需安装 SQLAlchemy: pip install SQLAlchemy") from exc


def _engine_instance():
    global _engine
    if _engine is None:
        create_engine, _ = _sqlalchemy()
        _engine = create_engine(storage_config.mysql_sqlalchemy_url(), pool_pre_ping=True)
    return _engine


def _ensure_schema() -> None:
    """首次使用 MySQL 时初始化对话表。"""
    global _initialized
    if not _initialized:
        try:
            with _engine_instance().begin() as conn:
                _init_schema(conn)
                _initialized = True
        except Exception:
            logger.exception("chat_store mysql schema init failed db=%s", label())
            raise


def _connection():
    _ensure_schema()
    return _engine_instance().begin()


def _init_schema(conn) -> None:
    """执行 MySQL 对话表初始化脚本。"""
    _, text = _sqlalchemy()
    for statement in MYSQL_SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(text(statement))


def create_session(title: str = "新会话") -> dict:
    sid = new_id()
    ts = now()
    _, text = _sqlalchemy()
    with _connection() as conn:
        conn.execute(
            text("INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (:id,:title,:created_at,:updated_at)"),
            {"id": sid, "title": title or "新会话", "created_at": ts, "updated_at": ts},
        )
    return {"id": sid, "title": title or "新会话", "created_at": ts, "updated_at": ts, "message_count": 0}


def list_sessions() -> list[dict]:
    _, text = _sqlalchemy()
    with _connection() as conn:
        return list(conn.execute(text(
            """SELECT s.id, s.title, s.created_at, s.updated_at,
                      (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
               FROM chat_sessions s
               ORDER BY s.updated_at DESC"""
        )).mappings().all())


def session_exists(session_id: str) -> bool:
    _, text = _sqlalchemy()
    with _connection() as conn:
        return conn.execute(
            text("SELECT 1 FROM chat_sessions WHERE id=:session_id"),
            {"session_id": session_id},
        ).first() is not None


def has_messages(session_id: str) -> bool:
    _, text = _sqlalchemy()
    with _connection() as conn:
        return conn.execute(
            text("SELECT 1 FROM chat_messages WHERE session_id=:session_id LIMIT 1"),
            {"session_id": session_id},
        ).first() is not None


def rename_session(session_id: str, title: str) -> None:
    _, text = _sqlalchemy()
    with _connection() as conn:
        conn.execute(
            text("UPDATE chat_sessions SET title=:title, updated_at=:updated_at WHERE id=:session_id"),
            {"title": title, "updated_at": now(), "session_id": session_id},
        )


def delete_session(session_id: str) -> bool:
    _, text = _sqlalchemy()
    with _connection() as conn:
        result = conn.execute(text("DELETE FROM chat_sessions WHERE id=:session_id"), {"session_id": session_id})
        deleted = result.rowcount > 0
        conn.execute(text("DELETE FROM chat_messages WHERE session_id=:session_id"), {"session_id": session_id})
        conn.execute(text("DELETE FROM chat_feedback WHERE session_id=:session_id"), {"session_id": session_id})
        return deleted


def clear_sessions() -> dict:
    _, text = _sqlalchemy()
    with _connection() as conn:
        sessions = conn.execute(text("SELECT count(*) AS n FROM chat_sessions")).mappings().one()["n"]
        messages = conn.execute(text("SELECT count(*) AS n FROM chat_messages")).mappings().one()["n"]
        feedback = conn.execute(text("SELECT count(*) AS n FROM chat_feedback")).mappings().one()["n"]
        conn.execute(text("DELETE FROM chat_feedback"))
        conn.execute(text("DELETE FROM chat_messages"))
        conn.execute(text("DELETE FROM chat_sessions"))
        return {"sessions": sessions, "messages": messages, "feedback": feedback}


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
    _, text = _sqlalchemy()
    with _connection() as conn:
        seq = conn.execute(
            text("SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM chat_messages WHERE session_id=:session_id"),
            {"session_id": session_id},
        ).mappings().one()["next"]
        conn.execute(
            text("""INSERT INTO chat_messages
               (id, session_id, seq, role, content, answer_source, retrieval_mode, refs,
                elapsed_ms, retrieval_ms, model_wait_ms, first_delta_ms, total_ms,
                message_count, prompt_chars, history_messages, created_at)
               VALUES (:id,:session_id,:seq,:role,:content,:answer_source,:retrieval_mode,:refs,
                       :elapsed_ms,:retrieval_ms,:model_wait_ms,:first_delta_ms,:total_ms,
                       :message_count,:prompt_chars,:history_messages,:created_at)"""),
            {
                "id": mid,
                "session_id": session_id,
                "seq": seq,
                "role": role,
                "content": content,
                "answer_source": answer_source,
                "retrieval_mode": retrieval_mode,
                "refs": refs_json,
                "elapsed_ms": elapsed_ms,
                "retrieval_ms": retrieval_ms,
                "model_wait_ms": model_wait_ms,
                "first_delta_ms": first_delta_ms,
                "total_ms": total_ms,
                "message_count": message_count,
                "prompt_chars": prompt_chars,
                "history_messages": history_messages,
                "created_at": ts,
            },
        )
        conn.execute(
            text("UPDATE chat_sessions SET updated_at=:updated_at WHERE id=:session_id"),
            {"updated_at": ts, "session_id": session_id},
        )
    return {
        "id": mid, "session_id": session_id, "seq": seq, "role": role, "content": content,
        "answer_source": answer_source, "retrieval_mode": retrieval_mode,
        "refs": refs or [], "elapsed_ms": elapsed_ms, "retrieval_ms": retrieval_ms,
        "model_wait_ms": model_wait_ms, "first_delta_ms": first_delta_ms, "total_ms": total_ms,
        "message_count": message_count, "prompt_chars": prompt_chars,
        "history_messages": history_messages, "created_at": ts,
    }


def get_messages(session_id: str) -> list[dict]:
    _, text = _sqlalchemy()
    with _connection() as conn:
        rows = conn.execute(
            text("""SELECT m.*, f.rating AS feedback_rating, f.reason AS feedback_reason
               FROM chat_messages m
               LEFT JOIN chat_feedback f ON f.message_id = m.id
               WHERE m.session_id=:session_id ORDER BY m.seq ASC"""),
            {"session_id": session_id},
        ).mappings().all()
        out = []
        for row in rows:
            d = dict(row)
            d["refs"] = json.loads(d["refs"]) if d.get("refs") else []
            out.append(d)
        return out


def message_exists(message_id: str) -> dict | None:
    _, text = _sqlalchemy()
    with _connection() as conn:
        row = conn.execute(
            text("SELECT id, session_id, role FROM chat_messages WHERE id=:message_id"),
            {"message_id": message_id},
        ).mappings().first()
        return dict(row) if row else None


def set_feedback(message_id: str, session_id: str, rating: str, reason: str | None = None) -> dict:
    ts = now()
    _, text = _sqlalchemy()
    with _connection() as conn:
        existing = conn.execute(
            text("SELECT id FROM chat_feedback WHERE message_id=:message_id"),
            {"message_id": message_id},
        ).mappings().first()
        if existing:
            conn.execute(
                text("""UPDATE chat_feedback
                   SET rating=:rating, reason=:reason, updated_at=:updated_at
                   WHERE message_id=:message_id"""),
                {"rating": rating, "reason": reason, "updated_at": ts, "message_id": message_id},
            )
            fid = existing["id"]
        else:
            fid = new_id()
            conn.execute(
                text("""INSERT INTO chat_feedback
                   (id, message_id, session_id, rating, reason, created_at, updated_at)
                   VALUES (:id,:message_id,:session_id,:rating,:reason,:created_at,:updated_at)"""),
                {
                    "id": fid,
                    "message_id": message_id,
                    "session_id": session_id,
                    "rating": rating,
                    "reason": reason,
                    "created_at": ts,
                    "updated_at": ts,
                },
            )
    return {"id": fid, "message_id": message_id, "rating": rating, "reason": reason}


def stats() -> dict:
    _, text = _sqlalchemy()
    with _connection() as conn:
        def one(q: str) -> int:
            return conn.execute(text(q)).mappings().one()["n"]

        return {
            "backend": "mysql",
            "db": label(),
            "sessions": one("SELECT count(*) AS n FROM chat_sessions"),
            "messages": one("SELECT count(*) AS n FROM chat_messages"),
            "up": one("SELECT count(*) AS n FROM chat_feedback WHERE rating='up'"),
            "down": one("SELECT count(*) AS n FROM chat_feedback WHERE rating='down'"),
        }
