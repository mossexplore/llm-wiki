from fastapi import APIRouter

from llm_wiki.knowledge import graph

from ..core.response import success

router = APIRouter()


@router.get("/api/graph")
def knowledge_graph():
    return success(graph.build_graph())
