"""History routes for the independent Web product; API/static misses stay misses."""

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agentic_graphrag.config import ROOT_DIR


def install_frontend(app: FastAPI) -> None:
    root = ROOT_DIR / "web"
    app.mount("/web/static", StaticFiles(directory=root / "static"), name="web-static")

    @app.get("/", include_in_schema=False)
    @app.get("/login", include_in_schema=False)
    @app.get("/register", include_in_schema=False)
    @app.get("/forgot", include_in_schema=False)
    @app.get("/reset", include_in_schema=False)
    @app.get("/invite/{token}", include_in_schema=False)
    @app.get("/app", include_in_schema=False)
    @app.get("/app/{path:path}", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(root / "application.html", media_type="text/html")

    @app.get("/web", include_in_schema=False)
    @app.get("/web/", include_in_schema=False)
    def legacy() -> RedirectResponse:
        return RedirectResponse("/app/chat", status_code=307)
