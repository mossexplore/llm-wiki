#!/usr/bin/env python3
"""
llm-wiki Web 后端(FastAPI)

启动:
    pip install -r requirements.txt
    uvicorn backend.server:app --reload --port 8000
    # 浏览器打开 http://127.0.0.1:8000/
"""
from fastapi import FastAPI

from .api import chat, graph, ingest, knowledge, search, static_pages
from .app_logging import LOG_DIR, logger
from .config import FRONTEND_DIR, ROOT
from .middleware import request_logging_middleware

import search_index  # noqa: E402

app = FastAPI(title="log-wiki")
app.middleware("http")(request_logging_middleware)


@app.on_event("startup")
def build_search_index() -> None:
    """启动时从 wiki/cases/ 整库重建索引,确保与磁盘文件一致。"""
    logger.info("server.startup log_dir=%s root=%s frontend=%s", LOG_DIR, ROOT, FRONTEND_DIR)
    try:
        if search_index.backend.available():
            n = search_index.backend.reindex_all()
            logger.info("search_index.reindex_all built=%s db=%s", n, search_index.DB_PATH)
        else:
            logger.warning("FTS5 不可用,检索将回退到文件扫描(功能正常,速度较慢)。")
    except Exception:
        logger.exception("search_index startup reindex failed")


app.include_router(ingest.router)
app.include_router(knowledge.router)
app.include_router(search.router)
app.include_router(graph.router)
app.include_router(chat.router)
app.include_router(static_pages.router)
static_pages.mount_static(app)
