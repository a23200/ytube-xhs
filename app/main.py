from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.services.batch_manager import batch_manager
from app.services.config import BASE_DIR
from app.services.runtime_store import store
from app.services.task_manager import task_manager


UTF8_MEDIA_TYPES = {
    "application/json",
    "text/css",
    "text/html",
    "text/javascript",
    "text/plain",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.recover_interrupted_projects()
    batch_manager.recover()
    yield
    batch_manager.shutdown()
    task_manager.cancel_all()

app = FastAPI(
    title="Video Link to Multi-platform Content Package",
    version="0.3.0",
    description="A real processing pipeline from authorized video URLs to platform-specific articles and export files.",
    lifespan=lifespan,
)

app.include_router(router)


@app.middleware("http")
async def enforce_web_encoding(request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in UTF8_MEDIA_TYPES:
        response.headers["content-type"] = f"{media_type}; charset=utf-8"
    if media_type == "text/html":
        response.headers["cache-control"] = "no-store, max-age=0"
        response.headers["pragma"] = "no-cache"
    elif request.url.path.startswith("/static/"):
        response.headers["cache-control"] = "no-cache"
    response.headers["x-content-type-options"] = "nosniff"
    return response


WEB_DIR = BASE_DIR / "app" / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(Path(WEB_DIR) / "index.html")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("static/"):
        return FileResponse(Path(WEB_DIR) / "index.html", status_code=404)
    return FileResponse(Path(WEB_DIR) / "index.html")
