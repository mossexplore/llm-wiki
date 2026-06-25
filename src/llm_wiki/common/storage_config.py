#!/usr/bin/env python3
"""存储后端配置读取:默认 SQLite,可在 config.yaml 中切换到 MySQL。"""

from __future__ import annotations

import os

import yaml

from llm_wiki.common.paths import CONFIG_PATH


def _config_data() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def storage_backend() -> str:
    """返回全局存储后端:`sqlite` 或 `mysql`;缺省为 `sqlite`。"""
    data = _config_data()
    storage = data.get("storage") or {}
    backend = os.environ.get("LOG_WIKI_STORAGE_BACKEND") or storage.get("backend") or "sqlite"
    backend = str(backend).strip().lower()
    if backend not in ("sqlite", "mysql"):
        raise RuntimeError("storage.backend 仅支持 sqlite 或 mysql")
    return backend


def _as_bool(value, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise RuntimeError(f"无法解析布尔配置值: {value!r}")


def auto_reindex_on_startup() -> bool:
    """返回启动时是否自动从 wiki/cases/ 整库重建检索索引;缺省为 true。"""
    data = _config_data()
    storage = data.get("storage") or {}
    value = os.environ.get("LOG_WIKI_AUTO_REINDEX_ON_STARTUP")
    if value in (None, ""):
        value = storage.get("auto_reindex_on_startup")
    return _as_bool(value, True)


def local_search() -> bool:
    """返回检索时是否允许扫描本地 wiki/cases/*.md;缺省为 true。

    true(默认):数据库索引后端不可用时,回退到本地 Markdown 文件扫描。
    false:只从数据库索引检索;后端不可用时直接判定无命中,绝不读本地文件。
    """
    data = _config_data()
    storage = data.get("storage") or {}
    value = os.environ.get("LOG_WIKI_LOCAL_SEARCH")
    if value in (None, ""):
        value = storage.get("local_search")
    return _as_bool(value, True)


def mysql_config() -> dict:
    """读取 MySQL 连接配置;仅在 storage.backend=mysql 时需要完整填写。"""
    data = _config_data()
    storage = data.get("storage") or {}
    mysql = dict(storage.get("mysql") or {})
    env_map = {
        "host": "LOG_WIKI_MYSQL_HOST",
        "port": "LOG_WIKI_MYSQL_PORT",
        "user": "LOG_WIKI_MYSQL_USER",
        "password": "LOG_WIKI_MYSQL_PASSWORD",
        "database": "LOG_WIKI_MYSQL_DATABASE",
        "charset": "LOG_WIKI_MYSQL_CHARSET",
    }
    for key, env_name in env_map.items():
        value = os.environ.get(env_name)
        if value not in (None, ""):
            mysql[key] = value
    mysql.setdefault("host", "127.0.0.1")
    mysql.setdefault("port", 3306)
    mysql.setdefault("charset", "utf8mb4")
    mysql["port"] = int(mysql["port"])
    missing = [key for key in ("user", "password", "database") if not mysql.get(key)]
    if missing:
        raise RuntimeError("storage.mysql 缺少必填字段: " + ", ".join(missing))
    return mysql
