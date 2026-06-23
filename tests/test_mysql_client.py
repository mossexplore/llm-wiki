"""split_sql_statements 的纯函数测试:注释/字符串内的分号不应切断语句。"""
from __future__ import annotations

import pathlib

from llm_wiki.common.mysql_client import split_sql_statements

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_ignores_semicolon_in_line_comment():
    sql = """
    CREATE TABLE t (id INT);
    -- 示例查询: SELECT * FROM t LIMIT 10;
    CREATE TABLE u (id INT);
    """
    stmts = split_sql_statements(sql)
    assert len(stmts) == 2
    assert stmts[0].startswith("CREATE TABLE t")
    assert stmts[1].startswith("CREATE TABLE u")


def test_ignores_semicolon_in_string_literal():
    sql = "INSERT INTO t(x) VALUES ('a;b'); CREATE TABLE u (id INT);"
    stmts = split_sql_statements(sql)
    assert len(stmts) == 2
    assert "'a;b'" in stmts[0]


def test_ignores_block_comment():
    sql = "CREATE TABLE t (id INT); /* 注释; 里有分号 */ CREATE TABLE u (id INT);"
    stmts = split_sql_statements(sql)
    assert len(stmts) == 2


def test_handles_doubled_quote_escape():
    sql = "INSERT INTO t(x) VALUES ('it''s; ok'); SELECT 1;"
    stmts = split_sql_statements(sql)
    assert len(stmts) == 2
    assert "it''s; ok" in stmts[0]


def test_real_schema_files_have_no_comment_only_fragments():
    for name in ("schema.mysql.sql", "schema.chat.mysql.sql"):
        path = REPO_ROOT / "db" / name
        if not path.exists():
            continue
        stmts = split_sql_statements(path.read_text(encoding="utf-8"))
        assert stmts, f"{name} 解析出 0 条语句"
        # 不应有「纯注释残片」被当成语句
        for s in stmts:
            assert not s.lstrip().startswith("--"), f"{name} 切出了注释残片: {s[:40]!r}"
            assert s.lstrip().upper().startswith(("CREATE", "INSERT", "ALTER", "DROP")), \
                f"{name} 切出了非 DDL 残片: {s[:40]!r}"
