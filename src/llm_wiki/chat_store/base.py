#!/usr/bin/env python3
"""对话存储的共享 CRUD 编排层。

SQLite 与 MySQL 两个后端的会话/消息/反馈增删改查 SQL 实质一致(都用 :name 命名参数,
stdlib sqlite3 也支持),只有连接管理、建表与迁移随方言不同。本基类承载全部编排,
方言子类只需提供:
  - ``_tx()``        返回一个事务上下文,产出带 all/one/run 三方法的连接适配器;
  - ``_ensure_schema()``  首次使用时建表 + 轻量迁移;
  - ``BACKEND`` / ``label()``  后端标识与无密码展示串。
"""
from __future__ import annotations

import json

from .common import MessageMetrics, new_id, now

_INSERT_MESSAGE = """INSERT INTO t_chat_messages
   (id, session_id, user_id, seq, role, content, answer_source, retrieval_mode, refs,
    elapsed_ms, retrieval_ms, model_wait_ms, first_delta_ms, total_ms,
    message_count, prompt_chars, history_messages, created_at)
   VALUES (:id,:session_id,:user_id,:seq,:role,:content,:answer_source,:retrieval_mode,:refs,
           :elapsed_ms,:retrieval_ms,:model_wait_ms,:first_delta_ms,:total_ms,
           :message_count,:prompt_chars,:history_messages,:created_at)"""


class BaseChatStore:
    BACKEND = "base"

    # --- 方言子类实现 -------------------------------------------------------
    def _tx(self):
        raise NotImplementedError

    def label(self) -> str:
        raise NotImplementedError

    # --- 会话 ---------------------------------------------------------------
    def create_session(self, title: str = "新会话", user_id: str | None = None,
                       source_code: str = "web") -> dict:
        sid = new_id()
        ts = now()
        title = title or "新会话"
        source_code = source_code or "web"
        with self._tx() as c:
            c.run(
                """INSERT INTO t_chat_sessions (id, user_id, source_code, title, created_at, updated_at)
                   VALUES (:id,:user_id,:source_code,:title,:created_at,:updated_at)""",
                {"id": sid, "user_id": user_id, "source_code": source_code,
                 "title": title, "created_at": ts, "updated_at": ts},
            )
        return {"id": sid, "user_id": user_id, "source_code": source_code, "title": title,
                "created_at": ts, "updated_at": ts, "message_count": 0}

    def list_sessions(self, user_id: str | None = None) -> list:
        """列出会话(按活跃倒序);传入 user_id 时只返回该用户的会话。"""
        where = "WHERE s.user_id = :user_id" if user_id else ""
        with self._tx() as c:
            return c.all(
                f"""SELECT s.id, s.user_id, s.source_code, s.title, s.created_at, s.updated_at,
                          (SELECT count(*) FROM t_chat_messages m WHERE m.session_id = s.id) AS message_count
                   FROM t_chat_sessions s
                   {where}
                   ORDER BY s.updated_at DESC""",
                {"user_id": user_id} if user_id else None,
            )

    def session_exists(self, session_id: str, user_id: str | None = None) -> bool:
        """会话是否存在;传 user_id 时还要求归属该用户(否则视为不存在)。"""
        with self._tx() as c:
            return c.one(
                "SELECT 1 AS x FROM t_chat_sessions WHERE id=:sid AND (:uid IS NULL OR user_id=:uid)",
                {"sid": session_id, "uid": user_id},
            ) is not None

    def has_messages(self, session_id: str) -> bool:
        with self._tx() as c:
            return c.one("SELECT 1 AS x FROM t_chat_messages WHERE session_id=:sid LIMIT 1",
                         {"sid": session_id}) is not None

    def rename_session(self, session_id: str, title: str) -> None:
        with self._tx() as c:
            c.run("UPDATE t_chat_sessions SET title=:title, updated_at=:ts WHERE id=:sid",
                  {"title": title, "ts": now(), "sid": session_id})

    def delete_session(self, session_id: str, user_id: str | None = None) -> bool:
        """删会话;传 user_id 时只删归属该用户的会话。仅在会话确被删除时级联消息/反馈。"""
        with self._tx() as c:
            deleted = c.run(
                "DELETE FROM t_chat_sessions WHERE id=:sid AND (:uid IS NULL OR user_id=:uid)",
                {"sid": session_id, "uid": user_id},
            ) > 0
            if deleted:   # 未删到(不存在或不归属)就不动其消息/反馈
                c.run("DELETE FROM t_chat_messages WHERE session_id=:sid", {"sid": session_id})
                c.run("DELETE FROM t_chat_feedbacks WHERE session_id=:sid", {"sid": session_id})
        return deleted

    def clear_sessions(self, user_id: str | None = None) -> dict:
        """清空会话/消息/反馈;传入 user_id 时只清该用户名下的会话及其消息与反馈。

        消息与反馈按「会话归属」级联(session_id 属于该用户的会话),而非按各自的
        user_id —— 删一个用户的会话就要带走该会话里的全部内容。
        """
        owned = "WHERE session_id IN (SELECT id FROM t_chat_sessions WHERE user_id = :user_id)" if user_id else ""
        sess_where = "WHERE user_id = :user_id" if user_id else ""
        params = {"user_id": user_id} if user_id else None
        with self._tx() as c:
            counts = {
                "sessions": c.one(f"SELECT count(*) AS n FROM t_chat_sessions {sess_where}", params)["n"],
                "messages": c.one(f"SELECT count(*) AS n FROM t_chat_messages {owned}", params)["n"],
                "feedback": c.one(f"SELECT count(*) AS n FROM t_chat_feedbacks {owned}", params)["n"],
            }
            # 先删消息/反馈(子查询依赖会话还在),最后删会话。
            c.run(f"DELETE FROM t_chat_feedbacks {owned}", params)
            c.run(f"DELETE FROM t_chat_messages {owned}", params)
            c.run(f"DELETE FROM t_chat_sessions {sess_where}", params)
        return counts

    # --- 消息 ---------------------------------------------------------------
    def add_message(self, session_id: str, role: str, content: str,
                    metrics: MessageMetrics | None = None, *, user_id: str | None = None) -> dict:
        """追加一条消息,返回完整记录(含生成的 id / seq)。"""
        m = metrics or MessageMetrics()
        mid = new_id()
        ts = now()
        refs_json = json.dumps(m.refs, ensure_ascii=False) if m.refs else None
        with self._tx() as c:
            seq = c.one(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM t_chat_messages WHERE session_id=:sid",
                {"sid": session_id},
            )["next"]
            c.run(_INSERT_MESSAGE, {
                "id": mid, "session_id": session_id, "user_id": user_id, "seq": seq,
                "role": role, "content": content,
                "answer_source": m.answer_source, "retrieval_mode": m.retrieval_mode, "refs": refs_json,
                "elapsed_ms": m.elapsed_ms, "retrieval_ms": m.retrieval_ms, "model_wait_ms": m.model_wait_ms,
                "first_delta_ms": m.first_delta_ms, "total_ms": m.total_ms,
                "message_count": m.message_count, "prompt_chars": m.prompt_chars,
                "history_messages": m.history_messages, "created_at": ts,
            })
            c.run("UPDATE t_chat_sessions SET updated_at=:ts WHERE id=:sid", {"ts": ts, "sid": session_id})
        return {
            "id": mid, "session_id": session_id, "user_id": user_id, "seq": seq,
            "role": role, "content": content,
            "answer_source": m.answer_source, "retrieval_mode": m.retrieval_mode,
            "refs": m.refs or [], "elapsed_ms": m.elapsed_ms, "retrieval_ms": m.retrieval_ms,
            "model_wait_ms": m.model_wait_ms, "first_delta_ms": m.first_delta_ms, "total_ms": m.total_ms,
            "message_count": m.message_count, "prompt_chars": m.prompt_chars,
            "history_messages": m.history_messages, "created_at": ts,
        }

    def get_messages(self, session_id: str) -> list:
        with self._tx() as c:
            rows = c.all(
                """SELECT m.*, f.rating AS feedback_rating, f.reason AS feedback_reason
                   FROM t_chat_messages m
                   LEFT JOIN t_chat_feedbacks f ON f.message_id = m.id
                   WHERE m.session_id=:sid ORDER BY m.seq ASC""",
                {"sid": session_id},
            )
        for d in rows:
            d["refs"] = json.loads(d["refs"]) if d.get("refs") else []
        return rows

    def message_exists(self, message_id: str, user_id: str | None = None) -> dict | None:
        """取消息基本信息;传 user_id 时还要求归属该用户(否则视为不存在)。"""
        with self._tx() as c:
            return c.one(
                "SELECT id, session_id, user_id, role FROM t_chat_messages "
                "WHERE id=:mid AND (:uid IS NULL OR user_id=:uid)",
                {"mid": message_id, "uid": user_id},
            )

    # --- 反馈 ---------------------------------------------------------------
    def set_feedback(self, message_id: str, session_id: str, rating: str,
                     reason: str | None = None, user_id: str | None = None) -> dict:
        """记录一条反馈;同一消息重复反馈则覆盖。"""
        ts = now()
        with self._tx() as c:
            existing = c.one("SELECT id FROM t_chat_feedbacks WHERE message_id=:mid", {"mid": message_id})
            if existing:
                c.run(
                    """UPDATE t_chat_feedbacks
                       SET user_id=COALESCE(:user_id, user_id), rating=:rating, reason=:reason, updated_at=:ts
                       WHERE message_id=:mid""",
                    {"user_id": user_id, "rating": rating, "reason": reason, "ts": ts, "mid": message_id},
                )
                fid = existing["id"]
            else:
                fid = new_id()
                c.run(
                    """INSERT INTO t_chat_feedbacks
                       (id, message_id, session_id, user_id, rating, reason, created_at, updated_at)
                       VALUES (:id,:mid,:sid,:user_id,:rating,:reason,:created_at,:updated_at)""",
                    {"id": fid, "mid": message_id, "sid": session_id, "user_id": user_id,
                     "rating": rating, "reason": reason, "created_at": ts, "updated_at": ts},
                )
        return {"id": fid, "message_id": message_id, "user_id": user_id, "rating": rating, "reason": reason}

    def stats(self) -> dict:
        with self._tx() as c:
            return {
                "backend": self.BACKEND,
                "db": self.label(),
                "sessions": c.one("SELECT count(*) AS n FROM t_chat_sessions")["n"],
                "messages": c.one("SELECT count(*) AS n FROM t_chat_messages")["n"],
                "up": c.one("SELECT count(*) AS n FROM t_chat_feedbacks WHERE rating='up'")["n"],
                "down": c.one("SELECT count(*) AS n FROM t_chat_feedbacks WHERE rating='down'")["n"],
            }
