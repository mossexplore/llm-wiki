#!/usr/bin/env python3
"""
llm-wiki 对话后端(FastAPI,chat 分支:仅 /api/chat/*,无前端页面)

    启动:
    pip install -r requirements.txt
    uvicorn --app-dir src llm_wiki.backend.server:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from llm_wiki import search_index
from llm_wiki.common import storage_config

from .api import chat
from .core.app_logging import LOG_DIR, logger
from .core.config import ROOT
from .core.middleware import request_logging_middleware
from .core.response import register_exception_handlers


def build_search_index() -> None:
    """启动时准备检索:按配置决定是否从 wiki/cases/ 整库重建,并预热精确命中 AC 自动机。

    reindex 与 AC 预热是两件独立的事:
      - reindex_all 会「整表删除 + 从 wiki/cases 文件重建」,仅在 auto_reindex_on_startup=true 时执行;
        生产里若知识已直接写入数据库、不以文件为源,务必设为 false,避免删库重建。
      - AC 预热只从库里现有的 t_case_signatures 读取并编译,不写不删,因此无论是否 reindex 都执行。
    """
    logger.info("server.startup log_dir=%s root=%s", LOG_DIR, ROOT)
    try:
        backend = search_index.get_backend()
        if not backend.available():
            logger.warning("检索后端不可用,检索将回退到文件扫描(功能正常,速度较慢)。")
            return
        if storage_config.auto_reindex_on_startup():
            n = backend.reindex_all()
            logger.info(
                f"search_index.reindex_all built={n} backend={type(backend).__name__} db={backend.label()}"
            )
        else:
            logger.info("search_index.reindex_all skipped by storage.auto_reindex_on_startup=false")
        # 不论是否 reindex,都从库里现有数据加载并编译 AC 自动机(只读,不删不写)
        backend.warm_exact_index()
        logger.info("search_index.exact_index_warmed backend=%s", type(backend).__name__)
    except Exception:
        logger.exception("search_index startup failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期:启动时按需重建检索索引(替代已弃用的 on_event）。"""
    build_search_index()
    yield


app = FastAPI(title="llm-wiki", lifespan=lifespan)
app.middleware("http")(request_logging_middleware)
register_exception_handlers(app)

app.include_router(chat.router)
