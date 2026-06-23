"""Shared SQLAlchemy engine for MySQL storage backends."""
from __future__ import annotations

from llm_wiki.common import storage_config

_engine = None


def get_mysql_client():
    """Return the cached SQLAlchemy Engine configured for the MySQL backend."""
    global _engine
    if _engine is None:
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            raise RuntimeError("使用 MySQL 存储需安装 SQLAlchemy: pip install SQLAlchemy") from exc
        _engine = create_engine(storage_config.mysql_sqlalchemy_url(), pool_pre_ping=True)
    return _engine


def _sql_text(statement: str):
    """Wrap SQL text lazily so SQLite-only imports do not require SQLAlchemy."""
    try:
        from sqlalchemy import text
    except ImportError as exc:
        raise RuntimeError("使用 MySQL 存储需安装 SQLAlchemy: pip install SQLAlchemy") from exc
    return text(statement)
