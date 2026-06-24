"""SQLite chat store 行为回归测试,锁定重构前后的语义。

通过 SqliteChatStore 实例操作(CHAT_DB 由 conftest 指向临时库),每个用例先清空。
"""

from __future__ import annotations

import pytest

from llm_wiki.chat_store import base
from llm_wiki.chat_store.common import MessageMetrics
from llm_wiki.chat_store.sqlite_store import SqliteChatStore

store = SqliteChatStore()


@pytest.fixture(autouse=True)
def _clean():
    store.clear_sessions()
    yield
    store.clear_sessions()


def test_create_session_defaults():
    s = store.create_session()
    assert s["title"] == "新会话"
    assert s["source_code"] == "web"
    assert s["user_id"] is None
    assert s["message_count"] == 0
    assert store.session_exists(s["id"])


def test_create_session_with_user_and_source():
    s = store.create_session("标题", user_id="u1", source_code="api")
    assert s["user_id"] == "u1"
    assert s["source_code"] == "api"
    s2 = store.create_session("t", source_code="")
    assert s2["source_code"] == "web"


def test_add_message_increments_seq_and_returns_row():
    s = store.create_session()
    m1 = store.add_message(s["id"], "user", "你好", user_id="u1")
    m2 = store.add_message(
        s["id"],
        "assistant",
        "答",
        MessageMetrics(answer_source="wiki", retrieval_mode="exact", refs=[{"file": "a.md", "title": "A"}], total_ms=12),
    )
    assert m1["seq"] == 1
    assert m2["seq"] == 2
    assert m2["answer_source"] == "wiki"
    assert m2["refs"] == [{"file": "a.md", "title": "A"}]
    assert m2["total_ms"] == 12


def test_get_messages_order_and_refs_and_feedback():
    s = store.create_session()
    store.add_message(s["id"], "user", "q")
    a = store.add_message(s["id"], "assistant", "a", MessageMetrics(refs=[{"file": "x.md", "title": "X"}]))
    store.set_feedback(a["id"], s["id"], "like")
    msgs = store.get_messages(s["id"])
    assert [m["seq"] for m in msgs] == [1, 2]
    assert msgs[1]["refs"] == [{"file": "x.md", "title": "X"}]
    assert msgs[1]["feedback"] == "like"


def test_has_messages_and_session_exists():
    s = store.create_session()
    assert not store.has_messages(s["id"])
    store.add_message(s["id"], "user", "q")
    assert store.has_messages(s["id"])
    assert not store.session_exists("nope")


def test_list_sessions_orders_by_updated_desc_with_counts(monkeypatch):
    # now() 是秒级精度,同秒内 updated_at 相同会导致排序不确定;注入递增时间戳使排序可测。
    ticks = iter(f"2026-01-01T00:00:{i:02d}Z" for i in range(1, 20))
    monkeypatch.setattr(base, "now", lambda: next(ticks))
    a = store.create_session("A")
    b = store.create_session("B")
    store.add_message(b["id"], "user", "q")  # b 更新更晚
    items = store.list_sessions()
    assert items[0]["id"] == b["id"]
    by_id = {it["id"]: it for it in items}
    assert by_id[b["id"]]["message_count"] == 1
    assert by_id[a["id"]]["message_count"] == 0


def test_list_sessions_filtered_by_user():
    store.create_session("u1-a", user_id="u1")
    store.create_session("u2-a", user_id="u2")
    store.create_session("anon")  # 无 user_id
    u1 = store.list_sessions(user_id="u1")
    assert [s["title"] for s in u1] == ["u1-a"]
    assert all(s["user_id"] == "u1" for s in u1)
    assert len(store.list_sessions()) == 3  # 不传 user_id 返回全部


def test_rename_session():
    s = store.create_session()
    store.rename_session(s["id"], "新名字")
    assert store.list_sessions()[0]["title"] == "新名字"


def test_set_feedback_insert_then_overwrite():
    s = store.create_session()
    a = store.add_message(s["id"], "assistant", "a")
    f1 = store.set_feedback(a["id"], s["id"], "like")
    f2 = store.set_feedback(a["id"], s["id"], "dislike", reason="不准")
    assert f1["id"] == f2["id"]
    assert f2["feedback"] == "dislike"
    assert f2["reason"] == "不准"


def test_clear_feedback_removes_feedback_row():
    s = store.create_session()
    a = store.add_message(s["id"], "assistant", "a")
    store.set_feedback(a["id"], s["id"], "like")

    assert store.clear_feedback(a["id"]) is True
    assert store.clear_feedback(a["id"]) is False
    assert store.get_messages(s["id"])[0]["feedback"] is None


def test_message_exists():
    s = store.create_session()
    a = store.add_message(s["id"], "assistant", "a")
    got = store.message_exists(a["id"])
    assert got["role"] == "assistant"
    assert got["session_id"] == s["id"]
    assert store.message_exists("nope") is None


def test_delete_session_removes_messages_and_feedback():
    s = store.create_session()
    a = store.add_message(s["id"], "assistant", "a")
    store.set_feedback(a["id"], s["id"], "like")
    assert store.delete_session(s["id"]) is True
    assert store.delete_session(s["id"]) is False
    assert store.get_messages(s["id"]) == []


def test_session_exists_scoped_by_user():
    s = store.create_session("s", user_id="u1")
    assert store.session_exists(s["id"]) is True  # 不传 user_id:存在即真
    assert store.session_exists(s["id"], user_id="u1") is True  # 归属匹配
    assert store.session_exists(s["id"], user_id="u2") is False  # 非归属视为不存在


def test_delete_session_scoped_by_user():
    s = store.create_session("s", user_id="u1")
    store.add_message(s["id"], "user", "q")
    assert store.delete_session(s["id"], user_id="u2") is False  # 别人删不掉
    assert len(store.get_messages(s["id"])) == 1  # 消息也没被误删
    assert store.delete_session(s["id"], user_id="u1") is True  # 本人可删
    assert store.get_messages(s["id"]) == []


def test_message_exists_scoped_by_user():
    s = store.create_session("s", user_id="u1")
    m = store.add_message(s["id"], "assistant", "a", user_id="u1")
    assert store.message_exists(m["id"], user_id="u1")["role"] == "assistant"
    assert store.message_exists(m["id"], user_id="u2") is None  # 非归属视为不存在


def test_clear_sessions_by_user_only_removes_that_user():
    s1 = store.create_session("u1", user_id="u1")
    store.add_message(s1["id"], "assistant", "a")
    store.set_feedback(store.get_messages(s1["id"])[0]["id"], s1["id"], "like")
    s2 = store.create_session("u2", user_id="u2")
    store.add_message(s2["id"], "user", "q")

    deleted = store.clear_sessions(user_id="u1")
    assert deleted == {"sessions": 1, "messages": 1, "feedback": 1}
    assert [s["id"] for s in store.list_sessions()] == [s2["id"]]
    assert len(store.get_messages(s2["id"])) == 1


def test_clear_sessions_returns_counts():
    s = store.create_session()
    a = store.add_message(s["id"], "assistant", "a")
    store.set_feedback(a["id"], s["id"], "like")
    deleted = store.clear_sessions()
    assert deleted["sessions"] == 1
    assert deleted["messages"] == 1
    assert deleted["feedback"] == 1


def test_stats_shape():
    s = store.create_session()
    a = store.add_message(s["id"], "assistant", "a")
    store.set_feedback(a["id"], s["id"], "like")
    st = store.stats()
    assert st["backend"] == "sqlite"
    assert st["sessions"] == 1 and st["messages"] == 1 and st["like"] == 1
