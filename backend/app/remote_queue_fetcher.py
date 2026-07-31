"""
Remote queue fetcher.

Lets you control the upload queue from a JSON file hosted anywhere public
(e.g. a small Vercel site) instead of / in addition to typing URLs into
the dashboard — e.g. https://mynanidown.vercel.app/down.json

Expected JSON shape:
{
  "gdrive": ["https://example.com/file1.zip", "https://example.com/file2.zip"],
  "gofile": ["https://example.com/file3.zip"]
}

IMPORTANT: this module does NOT start itself. It never runs just because
the app booted or because REMOTE_QUEUE_URL happens to be set in .env.
It is 100% frontend-controlled:

  - start_remote_queue_fetcher(url, interval) -> begins polling
  - stop_remote_queue_fetcher()               -> stops polling
  - get_status()                              -> current on/off state + stats

`main.py` never calls start_remote_queue_fetcher() on boot. The only way
this loop ever runs is via a POST /api/remote-queue/start call, i.e. the
user explicitly flipping "Auto from JSON" on in the dashboard. A server
restart always comes back up with this OFF, even if it was on before the
restart -- there is no persisted "was enabled" flag by design, so nothing
downloads until the frontend says so again.
"""
import asyncio
import logging
import time

import httpx

from .database import tasks_collection
from .models import Task, Destination
from .queue_manager import create_task

log = logging.getLogger("remote_queue_fetcher")

_task: asyncio.Task | None = None
_shutdown = False

# Runtime-only state (set by start_remote_queue_fetcher, cleared by stop).
_current_url: str | None = None
_current_interval: int = 60
_last_sync_at: float | None = None
_last_error: str | None = None
_total_added: int = 0


async def _already_queued(url: str, destination: str) -> bool:
    """True if this exact url+destination has already been created before
    (in any status), so we never re-queue the same download twice."""
    doc = await tasks_collection().find_one({"url": url, "destination": destination})
    return doc is not None


async def _sync_once() -> dict:
    global _last_sync_at, _last_error, _total_added

    if not _current_url:
        return {"added": 0, "skipped": 0, "error": "not configured"}

    added = 0
    skipped = 0
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(_current_url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        _last_error = str(e)
        log.warning("Could not fetch remote queue JSON: %s", e)
        return {"added": 0, "skipped": 0, "error": _last_error}

    mapping = {
        "gdrive": Destination.GOOGLE_DRIVE,
        "google_drive": Destination.GOOGLE_DRIVE,
        "gofile": Destination.GOFILE,
        "buzzheavier": Destination.BUZZHEAVIER,
    }

    for key, destination in mapping.items():
        urls = data.get(key) or []
        if not isinstance(urls, list):
            continue
        for url in urls:
            if not isinstance(url, str) or not url.strip():
                continue
            url = url.strip()
            if await _already_queued(url, destination.value):
                skipped += 1
                continue
            try:
                task = Task(url=url, destination=destination)
                await create_task(task)
                added += 1
                log.info("Queued from remote list: %s -> %s", url, destination.value)
            except Exception as e:
                log.warning("Failed to queue %s: %s", url, e)

    _last_error = None
    _last_sync_at = time.time()
    _total_added += added

    if added:
        log.info("Remote queue sync added %s new task(s).", added)
    elif skipped:
        log.debug("Remote queue sync: %s URL(s) already queued, nothing new.", skipped)

    return {"added": added, "skipped": skipped, "error": None}


async def _loop():
    global _shutdown
    log.info(
        "Remote queue fetcher ENABLED by frontend request (polling every %ss): %s",
        _current_interval, _current_url,
    )
    while not _shutdown:
        await _sync_once()
        await asyncio.sleep(_current_interval)


def start_remote_queue_fetcher(url: str, interval_seconds: int = 60):
    """Called only from the /api/remote-queue/start endpoint -- i.e. only
    when the user explicitly turns this on from the dashboard."""
    global _task, _shutdown, _current_url, _current_interval

    _current_url = url
    _current_interval = max(10, int(interval_seconds))  # floor of 10s, don't hammer the JSON host

    if _task is not None and not _task.done():
        # Already running: just update the settings, the loop picks up
        # _current_url / _current_interval on its next iteration.
        return

    _shutdown = False
    _task = asyncio.create_task(_loop())


async def stop_remote_queue_fetcher():
    global _shutdown, _task, _current_url
    _shutdown = True
    if _task:
        _task.cancel()
    _task = None
    _current_url = None


def is_enabled() -> bool:
    return _task is not None and not _task.done()


async def sync_once_now() -> dict:
    """Triggers one fetch immediately, without waiting for the next poll
    tick. Requires the fetcher to already be started (has a URL)."""
    return await _sync_once()


def get_status() -> dict:
    return {
        "enabled": is_enabled(),
        "url": _current_url,
        "interval_seconds": _current_interval,
        "last_sync_at": _last_sync_at,
        "last_error": _last_error,
        "total_added": _total_added,
    }
