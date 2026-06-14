from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.services.config import BASE_DIR

app = FastAPI(
    title="Video Link to Xiaohongshu Asset Package",
    version="0.1.0",
    description="A real processing pipeline from video URL to Xiaohongshu image-text draft assets.",
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
