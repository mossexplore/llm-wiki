import pathlib

from .config import ROOT  # noqa: F401

from llm_wiki import search_index

from .app_logging import logger


def index_case_file(case_path: pathlib.Path) -> None:
    """把刚写好的案例文件同步进检索索引;失败不影响主流程。"""
    try:
        case = search_index.case_from_file(case_path)
        if case:
            search_index.backend.index_case(case)
    except Exception:
        logger.exception("search_index.index_case failed file=%s", case_path)


def index_remove(case_path: pathlib.Path) -> None:
    try:
        search_index.backend.remove_case(case_path.stem)
    except Exception:
        logger.exception("search_index.remove_case failed file=%s", case_path)
