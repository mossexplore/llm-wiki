#!/usr/bin/env python3
"""统一日志配置:本地落盘 app.log 与 access.log。"""

from __future__ import annotations

import logging
import os
import pathlib
import tempfile
from logging.handlers import RotatingFileHandler

from llm_wiki.common.paths import ROOT

DEFAULT_LOG_DIR = ROOT / "logs"
FALLBACK_LOG_DIR = pathlib.Path(tempfile.gettempdir()) / "llm-wiki-logs"


def _handler(path: pathlib.Path, formatter: logging.Formatter) -> RotatingFileHandler:
    h = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    h.setLevel(logging.INFO)
    h.setFormatter(formatter)
    return h


def setup_logging() -> pathlib.Path:
    """初始化项目日志;可重复调用,不会重复挂载文件 handler。"""
    log_dir = pathlib.Path(os.environ.get("LOG_WIKI_LOG_DIR") or DEFAULT_LOG_DIR)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "log_dir.create_failed path=%s fallback=%s error=%s",
            log_dir,
            FALLBACK_LOG_DIR,
            exc,
        )
        log_dir = FALLBACK_LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    app_log_path = (log_dir / "app.log").resolve()
    app_log = str(app_log_path)
    has_app_file = any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == app_log
        for h in root.handlers
    )
    if not has_app_file:
        root.addHandler(_handler(app_log_path, fmt))

    access = logging.getLogger("log_wiki.access")
    access.setLevel(logging.INFO)
    access.propagate = False
    access_log_path = (log_dir / "access.log").resolve()
    access_log = str(access_log_path)
    has_access_file = any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == access_log
        for h in access.handlers
    )
    if not has_access_file:
        access.addHandler(_handler(access_log_path, fmt))

    return log_dir
