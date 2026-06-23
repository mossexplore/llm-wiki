from fastapi import APIRouter

from llm_wiki.knowledge import graph  # noqa: E402

from ..config import ROOT  # noqa: F401
from ..response import success

router = APIRouter()


@router.get("/api/graph")
def knowledge_graph():
    return success(graph.build_graph())
