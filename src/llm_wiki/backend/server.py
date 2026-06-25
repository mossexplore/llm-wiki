#!/usr/bin/env python3
"""
llm-wiki Web 后端(FastAPI)

    启动:
    pip install -r requirements.txt
    uvicorn --app-dir src llm_wiki.backend.server:app --reload --port 8000
    # 浏览器打开 http://127.0.0.1:8000/
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from llm_wiki import search_index
from llm_wiki.common import storage_config

from .api import chat, eval, graph, ingest, knowledge, search, static_pages
from .core.app_logging import LOG_DIR, logger
from .core.config import FRONTEND_DIR, ROOT
from .core.middleware import request_logging_middleware
from .core.response import register_exception_handlers


def build_search_index() -> None:
    """按配置决定启动时是否从 wiki/cases/ 整库重建检索索引。"""
    logger.info("server.startup log_dir=%s root=%s frontend=%s", LOG_DIR, ROOT, FRONTEND_DIR)
    try:
        if not storage_config.auto_reindex_on_startup():
            logger.info("search_index.reindex_all skipped by storage.auto_reindex_on_startup=false")
            return
        backend = search_index.get_backend()
        if backend.available():
            n = backend.reindex_all()
            logger.info(
                f"search_index.reindex_all built={n} backend={type(backend).__name__} "
                f"db={backend.label()}"
            )
        else:
            logger.warning("FTS5 不可用,检索将回退到文件扫描(功能正常,速度较慢)。")
    except Exception:
        logger.exception("search_index startup reindex failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:启动时按需重建检索索引(替代已弃用的 on_event）。"""
    build_search_index()
    yield


app = FastAPI(title="llm-wiki", lifespan=lifespan)
app.middleware("http")(request_logging_middleware)
register_exception_handlers(app)

app.include_router(ingest.router)
app.include_router(knowledge.router)
app.include_router(search.router)
app.include_router(eval.router)
app.include_router(graph.router)
app.include_router(chat.router)
app.include_router(static_pages.router)
static_pages.mount_static(app)
