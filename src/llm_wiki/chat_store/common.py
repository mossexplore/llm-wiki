#!/usr/bin/env python3
"""对话存储后端共享常量与工具函数。"""
from __future__ import annotations

import datetime
import logging
import os
import pathlib
import uuid
from dataclasses import dataclass

from llm_wiki.common.paths import ROOT

DB_PATH = pathlib.Path(os.environ.get("CHAT_DB", ROOT / "db" / "chat.db"))
SCHEMA_PATH = ROOT / "db" / "schema.chat.sql"
MYSQL_SCHEMA_PATH = ROOT / "db" / "schema.chat.mysql.sql"
logger = logging.getLogger("log_wiki.chat_store")


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class MessageMetrics:
    """assistant 回复的来源/检索/时延等运营指标。

    把 add_message 原本十余个并列参数收敛成一个对象,避免跨三层透传时位置错位。
    user 消息不带指标,传 None 即可。
    """
    answer_source: str | None = None      # 'wiki' | 'llm'
    retrieval_mode: str | None = None     # 'exact' | 'fuzzy' | 'none'
    refs: list | None = None              # 来源 wiki 列表
    elapsed_ms: int | None = None         # 兼容旧字段
    retrieval_ms: int | None = None
    model_wait_ms: int | None = None
    first_delta_ms: int | None = None
    total_ms: int | None = None
    message_count: int | None = None
    prompt_chars: int | None = None
