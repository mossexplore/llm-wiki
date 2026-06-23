"""SqliteSearch 精确/模糊/无命中三态 + signature 长度门控的回归测试。

直接用 index_case 注入规范化案例字典,不依赖 wiki/cases/ 下的真实文件,
因此与仓库内容解耦,可独立运行:

    pytest tests/test_search_index.py
"""
from __future__ import annotations

import pytest

from llm_wiki.search_index.common import exact_signatures, is_cjk, iter_search_tokens
from llm_wiki.search_index.sqlite_backend import SqliteSearch, fts_query

HIKARI_SIGNATURE = "HikariPool-1 - Connection is not available, request timed out"


def _case(**overrides) -> dict:
    case = {
        "id": "hikari",
        "file": "wiki/cases/hikari.md",
        "title": "HikariPool 连接池耗尽",
        "category": "数据库",
        "status": "verified",
        "confidence": "high",
        "signatures": [HIKARI_SIGNATURE],
        "components": ["HikariCP"],
        "background": "大促高峰连接池耗尽",
        "diagnosis": "慢查询长时间占满连接",
        "solution": "为慢查询加复合索引并调大 maximumPoolSize",
        "updated_at": "2026-01-01T00:00:00",
    }
    case.update(overrides)
    return case


@pytest.fixture()
def backend(tmp_path):
    b = SqliteSearch(db_path=tmp_path / "search.db")
    if not b.available():
        pytest.skip("当前 sqlite3 不支持 FTS5 + trigram,跳过检索后端测试")
    return b


def test_exact_hit_returns_solution(backend):
    backend.index_case(_case())
    log = f"线上日志:{HIKARI_SIGNATURE} after 30007ms"
    res = backend.search(log)
    assert res["mode"] == "exact"
    assert res["hits"][0]["file"] == "wiki/cases/hikari.md"
    assert "maximumPoolSize" in res["hits"][0]["solution"]


def test_exact_hit_is_case_insensitive(backend):
    backend.index_case(_case())
    res = backend.search(HIKARI_SIGNATURE.lower())
    assert res["mode"] == "exact"


def test_fuzzy_hit_when_signature_not_substring(backend):
    backend.index_case(_case())
    # 不含完整 signature 原文,只共享正文里的 token → 走模糊召回
    res = backend.search("maximumPoolSize 应该配置多大比较合理")
    assert res["mode"] == "fuzzy"
    assert res["hits"][0]["file"] == "wiki/cases/hikari.md"


def test_none_when_unrelated(backend):
    backend.index_case(_case())
    res = backend.search("完全不相关的内容 xyzqwerty")
    assert res["mode"] == "none"
    assert res["hits"] == []


def test_short_signature_excluded_from_exact(backend):
    # 仅有过短 signature "500" 的案例,不应再因 "500" 触发精确命中
    backend.index_case(_case(id="short", file="wiki/cases/short.md", signatures=["500"]))
    res = backend.search("接口返回 500 错误码")
    assert res["mode"] != "exact"


def test_exact_signatures_filters_and_dedups():
    got = exact_signatures(["500", " timeout error ", "timeout error", "OOM", "连接池耗尽"])
    assert got == ["timeout error", "连接池耗尽"]


def test_iter_search_tokens_splits_en_num_cjk():
    toks = iter_search_tokens("HikariPool timed out 30007ms 连接池耗尽 ab")
    assert "HikariPool" in toks       # 英文 >=3 字母
    assert "30007" in toks            # 数字 >=3 位
    assert "连接池耗尽" in toks         # 连续中文
    assert "ab" not in toks           # <3 字母被丢弃


def test_is_cjk():
    assert is_cjk("连接池")
    assert not is_cjk("HikariPool")
    assert not is_cjk("")


def test_fts_query_quotes_and_trigrams_cjk():
    q = fts_query("HikariPool 连接池耗尽")
    assert '"HikariPool"' in q
    assert '"连接池"' in q             # 中文按 trigram 切窗
    assert " OR " in q
