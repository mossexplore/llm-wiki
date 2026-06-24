"""Shared SQLAlchemy engine for MySQL storage backends."""

from __future__ import annotations

from llm_wiki.common import storage_config

_engine = None


def _mysql_sqlalchemy_url():
    """Build the SQLAlchemy URL from storage.mysql config."""
    try:
        from sqlalchemy.engine import URL
    except ImportError as exc:
        raise RuntimeError("使用 MySQL 存储需安装 SQLAlchemy: pip install SQLAlchemy") from exc
    cfg = storage_config.mysql_config()
    return URL.create(
        "mysql+pymysql",
        username=cfg["user"],
        password=cfg["password"],
        host=cfg["host"],
        port=cfg["port"],
        database=cfg["database"],
        query={"charset": cfg["charset"]},
    )


def get_mysql_client():
    """Return the cached SQLAlchemy Engine configured for the MySQL backend."""
    global _engine
    if _engine is None:
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            raise RuntimeError("使用 MySQL 存储需安装 SQLAlchemy: pip install SQLAlchemy") from exc
        _engine = create_engine(_mysql_sqlalchemy_url(), pool_pre_ping=True)
    return _engine


def get_mysql_label() -> str:
    """Return a fixed, non-sensitive MySQL label for logs/stats.

    不暴露任何连接信息(user/host/port/database 都是凭据或网络坐标,不应随
    日志或 stats 输出泄漏),统一返回固定占位串。
    """
    return "mysql error"


def _sql_text(statement: str):
    """Wrap SQL text lazily so SQLite-only imports do not require SQLAlchemy."""
    try:
        from sqlalchemy import text
    except ImportError as exc:
        raise RuntimeError("使用 MySQL 存储需安装 SQLAlchemy: pip install SQLAlchemy") from exc
    return text(statement)


def split_sql_statements(sql: str) -> list:
    """按语句分号切分 SQL,但忽略注释与字符串字面量里的分号。

    朴素的 ``sql.split(";")`` 会被 ``-- 示例: ... LIMIT 10;`` 这类注释内分号或
    ``COMMENT '...;...'`` 字符串内分号切断,产生残缺语句。这里识别:
      - 行注释 ``-- ...`` 和 ``# ...``;
      - 块注释 ``/* ... */``;
      - 单引号 / 双引号 / 反引号字符串(含 ``''`` 与 ``\\'`` 转义)。
    不支持 DELIMITER 自定义分隔符(存储过程/触发器),本项目 schema 仅用简单 DDL。
    """
    statements, buf = [], []
    i, n, quote = 0, len(sql), None
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if quote:
            buf.append(ch)
            if ch == "\\" and quote in ("'", '"') and nxt:
                buf.append(nxt)
                i += 2
                continue
            if ch == quote:
                if nxt == quote:  # 字符串内 '' / "" / `` 转义
                    buf.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if (ch == "-" and nxt == "-") or ch == "#":  # 行注释
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "/" and nxt == "*":  # 块注释
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def run_mysql_schema(conn, schema_path) -> None:
    """Execute MySQL schema statements on an open connection."""
    for statement in split_sql_statements(schema_path.read_text(encoding="utf-8")):
        conn.execute(_sql_text(statement))
