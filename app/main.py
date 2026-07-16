from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.services.config import BASE_DIR
from app.services.runtime_store import store
from app.services.task_manager import task_manager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.recover_interrupted_projects()
    yield
    task_manager.cancel_all()

app = FastAPI(
    title="Video Link to Multi-platform Content Package",
    version="0.2.0",
    description="A real processing pipeline from authorized video URLs to platform-specific articles and export files.",
    lifespan=lifespan,
)

app.include_router(router)

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
