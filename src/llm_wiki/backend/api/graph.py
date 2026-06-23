from fastapi import APIRouter

from llm_wiki.knowledge import graph  # noqa: E402

from ..config import ROOT  # noqa: F401

router = APIRouter()


@router.get("/api/graph")
def knowledge_graph():
    return graph.build_graph()
