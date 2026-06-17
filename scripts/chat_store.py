#!/usr/bin/env python3
"""
chat_store.py — 对话(Agent)运营数据的持久化层(SQLite)

存什么、为什么:
  所有会话、用户提问、Agent 回复、点赞/点踩(含点踩原因)都落在这里(db/chat.db)。
  这是「权威运营数据」,不是派生索引 —— 用于后续分析对话质量、发现知识盲区
  (点踩原因)、统计答案来源(wiki 命中 vs 大模型兜底)。表结构见 db/schema.chat.sql。

设计要点:
  - 纯标准库 sqlite3,零额外依赖;每次操作开/关一个连接,FastAPI 线程池下安全。
  - id 用 uuid;会话内消息用自增 seq 保证严格有序(同毫秒也不乱)。
  - 首次连接自动建表(执行 schema.chat.sql 的 CREATE TABLE IF NOT EXISTS)。

可单独排障:
    python scripts/chat_store.py stats
"""
from __future__ import annotations
import os, sys, json, uuid, sqlite3, pathlib, datetime, logging

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = pathlib.Path(os.environ.get("CHAT_DB", ROOT / "db" / "chat.db"))
SCHEMA_PATH = ROOT / "db" / "schema.chat.sql"
logger = logging.getLogger("log_wiki.chat_store")

_initialized = False


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


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
            conn.commit()
            _initialized = True
        except Exception:
            logger.exception("chat_store schema init failed db=%s", DB_PATH)
    return conn


# ----------------------------- 会话 -----------------------------
def create_session(title: str = "新会话") -> dict:
    sid = _new_id()
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (sid, title or "新会话", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": sid, "title": title or "新会话", "created_at": now, "updated_at": now, "message_count": 0}


def list_sessions() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT s.id, s.title, s.created_at, s.updated_at,
                      (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
               FROM chat_sessions s
               ORDER BY s.updated_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def session_exists(session_id: str) -> bool:
    conn = _connect()
    try:
        return conn.execute("SELECT 1 FROM chat_sessions WHERE id=?", (session_id,)).fetchone() is not None
    finally:
        conn.close()


def rename_session(session_id: str, title: str) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?",
                     (title, _now(), session_id))
        conn.commit()
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM chat_feedback WHERE session_id=?", (session_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ----------------------------- 消息 -----------------------------
def add_message(session_id: str, role: str, content: str,
                answer_source: str | None = None, retrieval_mode: str | None = None,
                refs: list | None = None, elapsed_ms: int | None = None) -> dict:
    """追加一条消息,返回完整记录(含生成的 id / seq)。会同时把会话 updated_at 顶到现在。"""
    mid = _new_id()
    now = _now()
    refs_json = json.dumps(refs, ensure_ascii=False) if refs else None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM chat_messages WHERE session_id=?",
            (session_id,)).fetchone()
        seq = row["next"]
        conn.execute(
            """INSERT INTO chat_messages
               (id, session_id, seq, role, content, answer_source, retrieval_mode, refs, elapsed_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (mid, session_id, seq, role, content, answer_source, retrieval_mode, refs_json, elapsed_ms, now),
        )
        conn.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, session_id))
        conn.commit()
    finally:
        conn.close()
    return {
        "id": mid, "session_id": session_id, "seq": seq, "role": role, "content": content,
        "answer_source": answer_source, "retrieval_mode": retrieval_mode,
        "refs": refs or [], "elapsed_ms": elapsed_ms, "created_at": now,
    }


def get_messages(session_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT m.*, f.rating AS feedback_rating, f.reason AS feedback_reason
               FROM chat_messages m
               LEFT JOIN chat_feedback f ON f.message_id = m.id
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
        r = conn.execute("SELECT id, session_id, role FROM chat_messages WHERE id=?", (message_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


# ----------------------------- 反馈(点赞/点踩) -----------------------------
def set_feedback(message_id: str, session_id: str, rating: str, reason: str | None = None) -> dict:
    """记录一条反馈;同一消息重复反馈则覆盖(改赞/改踩/改原因)。"""
    now = _now()
    conn = _connect()
    try:
        existing = conn.execute("SELECT id FROM chat_feedback WHERE message_id=?", (message_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE chat_feedback SET rating=?, reason=?, updated_at=? WHERE message_id=?",
                (rating, reason, now, message_id))
            fid = existing["id"]
        else:
            fid = _new_id()
            conn.execute(
                """INSERT INTO chat_feedback (id, message_id, session_id, rating, reason, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (fid, message_id, session_id, rating, reason, now, now))
        conn.commit()
    finally:
        conn.close()
    return {"id": fid, "message_id": message_id, "rating": rating, "reason": reason}


def stats() -> dict:
    conn = _connect()
    try:
        def one(q):
            return conn.execute(q).fetchone()[0]
        return {
            "sessions": one("SELECT count(*) FROM chat_sessions"),
            "messages": one("SELECT count(*) FROM chat_messages"),
            "up": one("SELECT count(*) FROM chat_feedback WHERE rating='up'"),
            "down": one("SELECT count(*) FROM chat_feedback WHERE rating='down'"),
        }
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        logger.info("chat_store db=%s stats=%s", DB_PATH, stats())
    else:
        logger.info("用法: python scripts/chat_store.py stats")


if __name__ == "__main__":
    main()
