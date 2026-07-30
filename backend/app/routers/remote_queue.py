"""
REST API for the remote-queue ("Auto from JSON") feature.

This is the ONLY way the remote queue fetcher ever starts. The app never
turns it on by itself at boot (see remote_queue_fetcher.py docstring) --
the frontend calls /start when the user picks "Auto from JSON" mode and
/stop when they switch back to "Manual".
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

from .. import remote_queue_fetcher as rqf
from ..config import get_settings

router = APIRouter(prefix="/api/remote-queue", tags=["remote-queue"])
settings = get_settings()


class RemoteQueueStartRequest(BaseModel):
    url: HttpUrl
    interval_seconds: int = 60


@router.get("")
async def status():
    """Current live state (never auto-enabled) plus .env-provided default
    values purely for pre-filling the dashboard form."""
    return {
        **rqf.get_status(),
        "default_url": settings.REMOTE_QUEUE_URL or None,
        "default_interval_seconds": settings.REMOTE_FETCH_INTERVAL_SECONDS,
    }


@router.post("/start")
async def start(req: RemoteQueueStartRequest):
    rqf.start_remote_queue_fetcher(str(req.url), req.interval_seconds)
    return rqf.get_status()


@router.post("/stop")
async def stop():
    await rqf.stop_remote_queue_fetcher()
    return rqf.get_status()


@router.post("/sync-now")
async def sync_now():
    """Trigger one immediate fetch without waiting for the poll interval."""
    if not rqf.is_enabled():
        raise HTTPException(status_code=400, detail="Auto from JSON is off. Start it first.")
    return await rqf.sync_once_now()
