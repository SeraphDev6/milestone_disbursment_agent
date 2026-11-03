"""Serves the built web app (web/dist) under /app when it exists. The API itself never depends on it."""

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def mount_spa(app: FastAPI, dist: Path = DIST) -> bool:
    """Mount /app/assets and an /app/* fallback to index.html. Returns False when there's no build."""
    index = dist / "index.html"
    if not index.is_file():
        return False
    router = APIRouter(include_in_schema=False)
    app.mount("/app/assets", StaticFiles(directory=dist / "assets"), name="spa-assets")

    @router.get("/app")
    def app_root() -> RedirectResponse:
        return RedirectResponse("/app/", status_code=308)

    @router.get("/app/{path:path}")
    def app_page(path: str) -> FileResponse:
        # Real files in dist (favicon, fonts) are served as-is; every other path is a client-side route.
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        if path.startswith("assets/"):
            raise HTTPException(404)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    app.include_router(router)
    return True
