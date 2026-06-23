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
    """Return a password-free MySQL label for logs and stats output."""
    cfg = storage_config.mysql_config()
    return f"mysql://{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}"


def _sql_text(statement: str):
    """Wrap SQL text lazily so SQLite-only imports do not require SQLAlchemy."""
    try:
        from sqlalchemy import text
    except ImportError as exc:
        raise RuntimeError("使用 MySQL 存储需安装 SQLAlchemy: pip install SQLAlchemy") from exc
    return text(statement)


def run_mysql_schema(conn, schema_path) -> None:
    """Execute semicolon-separated MySQL schema statements on an open connection."""
    statements = [
        stmt.strip()
        for stmt in schema_path.read_text(encoding="utf-8").split(";")
        if stmt.strip()
    ]
    for statement in statements:
        conn.execute(_sql_text(statement))
