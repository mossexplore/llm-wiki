from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..core.config import FRONTEND_DIR

router = APIRouter()


@router.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


def mount_static(app) -> None:
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
