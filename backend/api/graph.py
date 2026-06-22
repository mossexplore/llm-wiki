from fastapi import APIRouter

from ..config import ROOT  # noqa: F401

import graph  # noqa: E402

router = APIRouter()


@router.get("/api/graph")
def knowledge_graph():
    return graph.build_graph()
