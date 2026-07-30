"""
FastAPI application entrypoint.

Serves the dashboard (static files), the REST API, and starts the
background worker (Smart Upload Engine) plus the remote queue fetcher
as asyncio tasks inside the same process.

No login/auth here — this app is meant to run on your own VPS behind
your own firewall/network, for personal single-user use. If you expose
it on the public internet, put it behind your VPS provider's firewall
or a VPN, or ask for HTTP Basic Auth to be re-added.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

# Frontend location is resolved relative to this file (not a hardcoded
# container path), so it works regardless of where you deploy this.
# Expected layout on disk:
#   <project_root>/backend/app/main.py   (this file)
#   <project_root>/frontend/static
#   <project_root>/frontend/templates
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_STATIC = _PROJECT_ROOT / "frontend" / "static"
_FRONTEND_INDEX = _PROJECT_ROOT / "frontend" / "templates" / "index.html"

from .routers import tasks, disk, remote_queue
from .worker import start_worker, stop_worker
from .remote_queue_fetcher import stop_remote_queue_fetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Only the download/upload worker starts automatically. The remote
    # queue ("Auto from JSON") fetcher does NOT start here on purpose --
    # it only starts when the frontend calls POST /api/remote-queue/start
    # (see routers/remote_queue.py). This means a plain `python run.py`
    # never auto-downloads anything; it just waits for instructions.
    start_worker()
    yield
    await stop_worker()
    await stop_remote_queue_fetcher()  # safe no-op if it was never started


app = FastAPI(title="Smart Upload Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(disk.router)
app.include_router(remote_queue.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=str(_FRONTEND_STATIC)), name="static")


@app.get("/")
async def dashboard():
    return FileResponse(str(_FRONTEND_INDEX))
