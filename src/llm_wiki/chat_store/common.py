#!/usr/bin/env python3
"""对话存储后端共享常量与工具函数。"""
from __future__ import annotations

import datetime
import logging
import os
import pathlib
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[3]
DB_PATH = pathlib.Path(os.environ.get("CHAT_DB", ROOT / "db" / "chat.db"))
SCHEMA_PATH = ROOT / "db" / "schema.chat.sql"
MYSQL_SCHEMA_PATH = ROOT / "db" / "schema.chat.mysql.sql"
logger = logging.getLogger("log_wiki.chat_store")


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex
