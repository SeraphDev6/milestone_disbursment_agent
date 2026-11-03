"""The built web app (web/dist), served at the site root by the dispatcher in app.main.

Real files in dist (assets, favicon, fonts) are served as-is; every other GET is a client-side route and gets
index.html. The API never depends on the build: without one, the root points people at the API docs.
"""

from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from app.config import API_PREFIX

DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def has_build(dist: Path = DIST) -> bool:
    return (dist / "index.html").is_file()


def make_spa(dist: Path = DIST) -> Starlette:
    """An ASGI app for everything that isn't the API or MCP. Returns the no-build app when there's no dist."""
    index = dist / "index.html"
    if not index.is_file():
        return Starlette(routes=[Route("/{path:path}", _no_build, methods=["GET", "HEAD"])])

    async def page(request: Request) -> Response:
        path = request.path_params["path"]
        if path == "app" or path.startswith("app/"):  # where the app lived before it moved to the root
            return RedirectResponse("/" + path[3:].lstrip("/"), status_code=308)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        if path.startswith("assets/"):
            return Response(status_code=404)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return Starlette(
        routes=[
            Mount("/assets", StaticFiles(directory=dist / "assets"), name="spa-assets"),
            Route("/{path:path}", page, methods=["GET", "HEAD"]),
        ]
    )


async def _no_build(request: Request) -> Response:
    if request.path_params["path"] in ("", "app", "app/"):
        return RedirectResponse(f"{API_PREFIX}/docs", status_code=307)
    return JSONResponse({"detail": f"web app not built; the API is at {API_PREFIX}"}, status_code=404)
