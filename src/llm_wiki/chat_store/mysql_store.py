#!/usr/bin/env python3
"""MySQL 对话存储实现。由 storage.backend=mysql 启用。"""
from __future__ import annotations

import json

from llm_wiki.common.mysql_client import (
    _sql_text,
    get_mysql_client,
    get_mysql_label,
    run_mysql_schema,
)
from .common import MYSQL_SCHEMA_PATH, logger, new_id, now

_initialized = False
CHAT_COLUMN_DEFINITIONS = {
    "t_chat_sessions": {
        "user_id": "VARCHAR(64) COMMENT '用户 id, 标识该会话归属的用户'",
        "source_code": "VARCHAR(64) NOT NULL DEFAULT 'web' COMMENT '会话来源编码, 关联 t_session_sources.code'",
    },
    "t_chat_messages": {
        "user_id": "VARCHAR(64) COMMENT '用户 id, 标识该消息归属的用户'",
    },
    "t_chat_feedbacks": {
        "user_id": "VARCHAR(64) COMMENT '用户 id, 标识该反馈归属的用户'",
    },
}


def _ensure_schema() -> None:
    """首次使用 MySQL 时初始化对话表。"""
    global _initialized
    if not _initialized:
        try:
            with get_mysql_client().begin() as conn:
                _init_schema(conn)
                _initialized = True
        except Exception:
            logger.exception("chat_store mysql schema init failed db=%s", get_mysql_label())
            raise


def _connection():
    _ensure_schema()
    return get_mysql_client().begin()


def _init_schema(conn) -> None:
    """执行 MySQL 对话表初始化脚本。"""
    run_mysql_schema(conn, MYSQL_SCHEMA_PATH)
    _migrate_chat_columns(conn)
    _migrate_feedback_table(conn)
    _ensure_default_session_source(conn)


def _migrate_chat_columns(conn) -> None:
    for table, columns in CHAT_COLUMN_DEFINITIONS.items():
        for column, definition in columns.items():
            if not _column_exists(conn, table, column):
                conn.execute(_sql_text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
            elif _column_varchar_length(conn, table, column) != 64:
                conn.execute(_sql_text(f"ALTER TABLE {table} MODIFY COLUMN {column} {definition}"))
    _add_index_if_missing(conn, "t_chat_sessions", "idx_chat_sessions_user", "user_id")
    _add_index_if_missing(conn, "t_chat_sessions", "idx_chat_sessions_source", "source_code")
    _add_index_if_missing(conn, "t_chat_messages", "idx_chat_messages_user", "user_id")
    _add_index_if_missing(conn, "t_chat_feedbacks", "idx_chat_feedbacks_user", "user_id")


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        _sql_text(
            """SELECT COUNT(*) AS n
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = :table_name
                 AND COLUMN_NAME = :column_name"""
        ),
        {"table_name": table, "column_name": column},
    ).mappings().one()
    return row["n"] > 0


def _column_varchar_length(conn, table: str, column: str) -> int | None:
    row = conn.execute(
        _sql_text(
            """SELECT CHARACTER_MAXIMUM_LENGTH AS length
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = :table_name
                 AND COLUMN_NAME = :column_name"""
        ),
        {"table_name": table, "column_name": column},
    ).mappings().first()
    return row["length"] if row else None


def _add_index_if_missing(conn, table: str, index: str, column: str) -> None:
    row = conn.execute(
        _sql_text(
            """SELECT COUNT(*) AS n
               FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = :table_name
                 AND INDEX_NAME = :index_name"""
        ),
        {"table_name": table, "index_name": index},
    ).mappings().one()
    if row["n"] == 0:
        conn.execute(_sql_text(f"ALTER TABLE {table} ADD INDEX {index} ({column})"))


def _migrate_feedback_table(conn) -> None:
    old_feedback = conn.execute(
        _sql_text(
            """SELECT COUNT(*) AS n
               FROM information_schema.TABLES
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name"""
        ),
        {"table_name": "t_chat_feedback"},
    ).mappings().one()["n"]
    if old_feedback:
        conn.execute(_sql_text(
            """INSERT IGNORE INTO t_chat_feedbacks
               (id, message_id, session_id, rating, reason, created_at, updated_at)
               SELECT id, message_id, session_id, rating, reason, created_at, updated_at
               FROM t_chat_feedback"""
        ))
        conn.execute(_sql_text("DROP TABLE t_chat_feedback"))


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


def create_session(title: str = "新会话", user_id: str | None = None,
                   source_code: str = "web") -> dict:
    sid = new_id()
    ts = now()
    source_code = source_code or "web"
    with _connection() as conn:
        conn.execute(
            _sql_text(
                """INSERT INTO t_chat_sessions (id, user_id, source_code, title, created_at, updated_at)
                   VALUES (:id,:user_id,:source_code,:title,:created_at,:updated_at)"""
            ),
            {
                "id": sid,
                "user_id": user_id,
                "source_code": source_code,
                "title": title or "新会话",
                "created_at": ts,
                "updated_at": ts,
            },
        )
    return {
        "id": sid, "user_id": user_id, "source_code": source_code, "title": title or "新会话",
        "created_at": ts, "updated_at": ts, "message_count": 0,
    }


def list_sessions() -> list[dict]:
    with _connection() as conn:
        return list(conn.execute(_sql_text(
            """SELECT s.id, s.user_id, s.source_code, s.title, s.created_at, s.updated_at,
                      (SELECT count(*) FROM t_chat_messages m WHERE m.session_id = s.id) AS message_count
               FROM t_chat_sessions s
               ORDER BY s.updated_at DESC"""
        )).mappings().all())


def session_exists(session_id: str) -> bool:
    with _connection() as conn:
        return conn.execute(
            _sql_text("SELECT 1 FROM t_chat_sessions WHERE id=:session_id"),
            {"session_id": session_id},
        ).first() is not None


def has_messages(session_id: str) -> bool:
    with _connection() as conn:
        return conn.execute(
            _sql_text("SELECT 1 FROM t_chat_messages WHERE session_id=:session_id LIMIT 1"),
            {"session_id": session_id},
        ).first() is not None


def rename_session(session_id: str, title: str) -> None:
    with _connection() as conn:
        conn.execute(
            _sql_text("UPDATE t_chat_sessions SET title=:title, updated_at=:updated_at WHERE id=:session_id"),
            {"title": title, "updated_at": now(), "session_id": session_id},
        )


def delete_session(session_id: str) -> bool:
    with _connection() as conn:
        result = conn.execute(_sql_text("DELETE FROM t_chat_sessions WHERE id=:session_id"), {"session_id": session_id})
        deleted = result.rowcount > 0
        conn.execute(_sql_text("DELETE FROM t_chat_messages WHERE session_id=:session_id"), {"session_id": session_id})
        conn.execute(_sql_text("DELETE FROM t_chat_feedbacks WHERE session_id=:session_id"), {"session_id": session_id})
        return deleted


def clear_sessions() -> dict:
    with _connection() as conn:
        sessions = conn.execute(_sql_text("SELECT count(*) AS n FROM t_chat_sessions")).mappings().one()["n"]
        messages = conn.execute(_sql_text("SELECT count(*) AS n FROM t_chat_messages")).mappings().one()["n"]
        feedback = conn.execute(_sql_text("SELECT count(*) AS n FROM t_chat_feedbacks")).mappings().one()["n"]
        conn.execute(_sql_text("DELETE FROM t_chat_feedbacks"))
        conn.execute(_sql_text("DELETE FROM t_chat_messages"))
        conn.execute(_sql_text("DELETE FROM t_chat_sessions"))
        return {"sessions": sessions, "messages": messages, "feedback": feedback}


def add_message(session_id: str, role: str, content: str,
                answer_source: str | None = None, retrieval_mode: str | None = None,
                refs: list | None = None, elapsed_ms: int | None = None,
                retrieval_ms: int | None = None, model_wait_ms: int | None = None,
                first_delta_ms: int | None = None, total_ms: int | None = None,
                message_count: int | None = None, prompt_chars: int | None = None,
                history_messages: int | None = None,
                user_id: str | None = None) -> dict:
    mid = new_id()
    ts = now()
    refs_json = json.dumps(refs, ensure_ascii=False) if refs else None
    with _connection() as conn:
        seq = conn.execute(
            _sql_text("SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM t_chat_messages WHERE session_id=:session_id"),
            {"session_id": session_id},
        ).mappings().one()["next"]
        conn.execute(
            _sql_text("""INSERT INTO t_chat_messages
               (id, session_id, user_id, seq, role, content, answer_source, retrieval_mode, refs,
                elapsed_ms, retrieval_ms, model_wait_ms, first_delta_ms, total_ms,
                message_count, prompt_chars, history_messages, created_at)
               VALUES (:id,:session_id,:user_id,:seq,:role,:content,:answer_source,:retrieval_mode,:refs,
                       :elapsed_ms,:retrieval_ms,:model_wait_ms,:first_delta_ms,:total_ms,
                       :message_count,:prompt_chars,:history_messages,:created_at)"""),
            {
                "id": mid,
                "session_id": session_id,
                "user_id": user_id,
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
            _sql_text("UPDATE t_chat_sessions SET updated_at=:updated_at WHERE id=:session_id"),
            {"updated_at": ts, "session_id": session_id},
        )
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
    with _connection() as conn:
        rows = conn.execute(
            _sql_text("""SELECT m.*, f.rating AS feedback_rating, f.reason AS feedback_reason
               FROM t_chat_messages m
               LEFT JOIN t_chat_feedbacks f ON f.message_id = m.id
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
    with _connection() as conn:
        row = conn.execute(
            _sql_text("SELECT id, session_id, user_id, role FROM t_chat_messages WHERE id=:message_id"),
            {"message_id": message_id},
        ).mappings().first()
        return dict(row) if row else None


def set_feedback(message_id: str, session_id: str, rating: str, reason: str | None = None,
                 user_id: str | None = None) -> dict:
    ts = now()
    with _connection() as conn:
        existing = conn.execute(
            _sql_text("SELECT id FROM t_chat_feedbacks WHERE message_id=:message_id"),
            {"message_id": message_id},
        ).mappings().first()
        if existing:
            conn.execute(
                _sql_text("""UPDATE t_chat_feedbacks
                   SET user_id=COALESCE(:user_id, user_id),
                       rating=:rating, reason=:reason, updated_at=:updated_at
                   WHERE message_id=:message_id"""),
                {
                    "user_id": user_id,
                    "rating": rating,
                    "reason": reason,
                    "updated_at": ts,
                    "message_id": message_id,
                },
            )
            fid = existing["id"]
        else:
            fid = new_id()
            conn.execute(
                _sql_text("""INSERT INTO t_chat_feedbacks
                   (id, message_id, session_id, user_id, rating, reason, created_at, updated_at)
                   VALUES (:id,:message_id,:session_id,:user_id,:rating,:reason,:created_at,:updated_at)"""),
                {
                    "id": fid,
                    "message_id": message_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "rating": rating,
                    "reason": reason,
                    "created_at": ts,
                    "updated_at": ts,
                },
            )
    return {"id": fid, "message_id": message_id, "user_id": user_id, "rating": rating, "reason": reason}


def stats() -> dict:
    with _connection() as conn:
        def one(q: str) -> int:
            return conn.execute(_sql_text(q)).mappings().one()["n"]

        return {
            "backend": "mysql",
            "db": get_mysql_label(),
            "sessions": one("SELECT count(*) AS n FROM t_chat_sessions"),
            "messages": one("SELECT count(*) AS n FROM t_chat_messages"),
            "up": one("SELECT count(*) AS n FROM t_chat_feedbacks WHERE rating='up'"),
            "down": one("SELECT count(*) AS n FROM t_chat_feedbacks WHERE rating='down'"),
        }
