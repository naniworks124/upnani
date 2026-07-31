"""
Background worker: the Smart Upload Engine.

Runs as an asyncio task inside the same process as the FastAPI app (a
single Hugging Face Space container). It polls MongoDB for waiting tasks,
processes them one at a time (MAX_CONCURRENT_TASKS=1 by default, matching
"optimize for reliability, not concurrency"), and persists every state
change so a container restart can resume in-flight work.

Decision logic (never asked of the user):
  1. Try direct streaming: download bytes are piped straight into the
     upload request, with nothing written to disk.
  2. If streaming isn't possible for this destination/situation (unknown
     file size for Google Drive, streaming disabled, or a streaming
     attempt fails), fall back to disk mode:
        Download -> Verify -> Upload -> Delete
  3. If disk mode's download or upload fails after retries, the temp file
     is deleted (unless the download itself supports resuming and hasn't
     exhausted its retries) and the task is marked FAILED with a detailed
     reason.
"""
import asyncio
import logging
import os
import time

from tqdm import tqdm

from .config import get_settings
from .database import ensure_indexes
from .disk_utils import ensure_temp_dir, delete_file_if_exists, has_enough_free_space, cleanup_orphan_temp_files
from .download_engine import download_to_disk, stream_download_iter, probe, DownloadError
from .models import Task, TaskStatus, Destination
from .queue_manager import (
    next_waiting_task, update_task, reset_stuck_tasks_on_boot, get_task, list_tasks,
)
from .security import safe_join
from .upload import google_drive, gofile, buzzheavier

log = logging.getLogger("worker")
settings = get_settings()

_worker_task: asyncio.Task | None = None
_shutdown = False


# ---------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------

class _Throttle:
    """Avoids hammering MongoDB with a write on every chunk; persists
    progress at most every 0.5s."""
    def __init__(self):
        self.last = 0.0

    def ready(self) -> bool:
        now = time.time()
        if now - self.last > 0.5:
            self.last = now
            return True
        return False


async def _run_download_phase(task: Task) -> Task:
    ensure_temp_dir()
    dest_path = safe_join(settings.TEMP_DIR, task.filename or f"{task.id}.bin")
    await update_task(task.id, status=TaskStatus.DOWNLOADING.value, temp_path=dest_path)

    print(f"\n⬇️  Downloading: {task.filename or task.url}")
    throttle = _Throttle()
    bar = {"pbar": None}

    def on_progress(downloaded, total, speed):
        if bar["pbar"] is None:
            bar["pbar"] = tqdm(total=total, unit="B", unit_scale=True, desc="Downloading")
        bar["pbar"].n = downloaded
        if total and bar["pbar"].total != total:
            bar["pbar"].total = total
        bar["pbar"].refresh()
        if throttle.ready():
            eta = ((total - downloaded) / speed) if (total and speed > 0) else None
            asyncio.create_task(update_task(
                task.id,
                bytes_downloaded=downloaded,
                total_bytes=total,
                download_speed_bps=speed,
                eta_seconds=eta,
            ))

    try:
        meta = await download_to_disk(
            task.url, dest_path, on_progress, resume_offset=task.resumable_offset
        )
    except DownloadError as e:
        if bar["pbar"]:
            bar["pbar"].close()
        print(f"❌ Download failed: {e}")
        delete_file_if_exists(dest_path)
        await update_task(task.id, status=TaskStatus.FAILED.value, error=str(e))
        raise
    finally:
        if bar["pbar"]:
            bar["pbar"].close()

    filename = task.filename or meta["filename"]
    total_bytes = meta["size"]

    # --- Verify ---
    actual_size = os.path.getsize(dest_path) if os.path.exists(dest_path) else 0
    if total_bytes and actual_size != total_bytes:
        delete_file_if_exists(dest_path)
        err = f"Verification failed: expected {total_bytes} bytes, got {actual_size}."
        print(f"❌ {err}")
        await update_task(task.id, status=TaskStatus.FAILED.value, error=err)
        raise DownloadError(err)

    print(f"✅ Download complete: {filename} ({actual_size / (1024**2):.1f} MB)")

    await update_task(
        task.id,
        filename=filename,
        total_bytes=total_bytes,
        bytes_downloaded=actual_size,
        supports_resume=meta["supports_resume"],
    )
    return await get_task(task.id)


async def _upload_disk_mode(task: Task) -> dict:
    await update_task(task.id, status=TaskStatus.UPLOADING.value)
    dest_name = {
        Destination.GOOGLE_DRIVE: "Google Drive",
        Destination.GOFILE: "GoFile",
        Destination.BUZZHEAVIER: "BuzzHeavier",
    }[task.destination]
    print(f"⬆️  Uploading to {dest_name}: {task.filename}")

    throttle = _Throttle()
    pbar = tqdm(total=task.total_bytes, unit="B", unit_scale=True, desc="Uploading")

    def on_progress(uploaded, total, speed):
        pbar.n = uploaded
        if total and pbar.total != total:
            pbar.total = total
        pbar.refresh()
        if throttle.ready():
            eta = ((total - uploaded) / speed) if (total and speed > 0) else None
            asyncio.create_task(update_task(
                task.id, bytes_uploaded=uploaded, upload_speed_bps=speed, eta_seconds=eta
            ))

    last_error = None
    try:
        for attempt in range(1, settings.UPLOAD_MAX_RETRIES + 1):
            try:
                if task.destination == Destination.GOOGLE_DRIVE:
                    if not task.upload_session_uri:
                        session_uri = await google_drive.create_resumable_session(task.filename, task.total_bytes)
                        await update_task(task.id, upload_session_uri=session_uri)
                    else:
                        session_uri = task.upload_session_uri
                    start_offset = 0
                    if attempt > 1:
                        start_offset = await google_drive.get_upload_offset(session_uri, task.total_bytes)
                    result = await google_drive.upload_file_from_disk(
                        session_uri, task.temp_path, task.total_bytes, start_offset, on_progress
                    )
                    print(f"✅ Uploaded to Google Drive: {result['link']}")
                    return {"remote_file_id": result["file_id"], "remote_link": result["link"]}
                elif task.destination == Destination.GOFILE:
                    result = await gofile.upload_file_from_disk(
                        task.temp_path, task.filename, task.total_bytes, on_progress
                    )
                    print(f"✅ Uploaded to GoFile: {result['link']}")
                    return {"remote_file_id": result["file_id"], "remote_link": result["link"]}
                else:  # BuzzHeavier
                    result = await buzzheavier.upload_file_from_disk(
                        task.temp_path, task.filename, task.total_bytes, on_progress
                    )
                    print(f"✅ Uploaded to BuzzHeavier: {result['link']}")
                    return {"remote_file_id": result["file_id"], "remote_link": result["link"]}
            except Exception as e:
                last_error = e
                await update_task(task.id, upload_attempts=attempt)
                if attempt >= settings.UPLOAD_MAX_RETRIES:
                    break
                backoff = min(2 ** attempt, 60)
                print(f"⚠️  Upload attempt {attempt} failed ({e}); retrying in {backoff}s")
                log.warning("Upload attempt %s failed for task %s (%s); retrying in %ss", attempt, task.id, e, backoff)
                await asyncio.sleep(backoff)
    finally:
        pbar.close()

    print(f"❌ Upload failed after {settings.UPLOAD_MAX_RETRIES} attempts: {last_error}")
    raise RuntimeError(str(last_error))


async def _try_streaming(task: Task) -> dict | None:
    """Attempts true pass-through streaming (no disk write). Returns the
    upload result dict on success, or None if streaming isn't viable so the
    caller should fall back to disk mode. Any mid-stream failure also
    triggers a fallback rather than a retry, since a partially-consumed
    download stream can't be rewound."""
    if not settings.ENABLE_STREAMING:
        return None

    # GoFile's upload requires a synchronous multipart body under the
    # hood (httpx's `files=` param needs a `.read()`-able object), so an
    # async-generator streaming attempt can never succeed here -- it
    # always dies with "'async_generator' object has no attribute
    # 'read'" after already downloading part of the file. Skip straight
    # to disk mode for GoFile instead of wasting a doomed attempt (and a
    # second full download) every single time.
    if task.destination == Destination.GOFILE:
        return None

    try:
        meta_probe = await probe(task.url, __import__("httpx").AsyncClient())
    except Exception:
        meta_probe = {"size": None, "filename": task.filename}

    filename = task.filename or meta_probe.get("filename") or f"{task.id}.bin"

    if task.destination == Destination.GOOGLE_DRIVE and meta_probe.get("size") is None:
        # Drive's resumable single-shot streaming path requires a known size.
        return None

    await update_task(task.id, status=TaskStatus.DOWNLOADING.value, method="stream", filename=filename)

    client = resp = None
    try:
        client, resp, dl_meta = await stream_download_iter(task.url)
        total_size = dl_meta["size"]
        filename = task.filename or dl_meta["filename"] or filename

        throttle = _Throttle()

        def dl_progress(downloaded, total, speed):
            if throttle.ready():
                asyncio.create_task(update_task(task.id, bytes_downloaded=downloaded, total_bytes=total, download_speed_bps=speed))

        async def counted_iter():
            count = 0
            async for chunk in resp.aiter_bytes(settings.CHUNK_SIZE_BYTES):
                count += len(chunk)
                dl_progress(count, total_size, 0)
                yield chunk

        await update_task(task.id, status=TaskStatus.UPLOADING.value, total_bytes=total_size, filename=filename)

        up_throttle = _Throttle()

        def up_progress(uploaded, total, speed):
            if up_throttle.ready():
                asyncio.create_task(update_task(task.id, bytes_uploaded=uploaded, upload_speed_bps=speed))

        if task.destination == Destination.GOOGLE_DRIVE:
            session_uri = await google_drive.create_resumable_session(filename, total_size)
            result = await google_drive.stream_upload_from_iterator(session_uri, counted_iter(), total_size, up_progress)
        elif task.destination == Destination.BUZZHEAVIER:
            result = await buzzheavier.stream_upload_from_iterator(counted_iter(), filename, total_size, up_progress)
        else:
            result = await gofile.stream_upload_from_iterator(counted_iter(), filename, total_size, up_progress)

        return {"remote_file_id": result["file_id"], "remote_link": result["link"]}
    except Exception as e:
        log.info("Streaming attempt failed for task %s (%s); falling back to disk mode.", task.id, e)
        return None
    finally:
        if resp is not None:
            await resp.aclose()
        if client is not None:
            await client.aclose()


async def _process_task(task: Task):
    print(f"\n{'='*60}\n🚀 TASK START: {task.filename or task.url}\n   -> {task.destination.value}\n{'='*60}")
    log.info("Processing task %s (%s -> %s)", task.id, task.url, task.destination)
    try:
        stream_result = await _try_streaming(task)
        if stream_result is not None:
            await update_task(
                task.id,
                status=TaskStatus.COMPLETED.value,
                method="stream",
                remote_file_id=stream_result["remote_file_id"],
                remote_link=stream_result["remote_link"],
            )
            print(f"✅ TASK DONE (streamed): {stream_result['remote_link']}")
            return

        # --- Fallback: Download -> Verify -> Upload -> Delete ---
        await update_task(task.id, method="disk")
        task = await get_task(task.id)
        task = await _run_download_phase(task)

        result = await _upload_disk_mode(task)

        delete_file_if_exists(task.temp_path)
        await update_task(
            task.id,
            status=TaskStatus.COMPLETED.value,
            remote_file_id=result["remote_file_id"],
            remote_link=result["remote_link"],
            temp_path=None,
        )
        print(f"✅ TASK COMPLETE: {task.filename} -> {result['remote_link']}")
        log.info("Task %s completed.", task.id)
    except DownloadError:
        pass  # already marked FAILED with a detailed error inside _run_download_phase
    except Exception as e:
        current = await get_task(task.id)
        if current and current.temp_path:
            delete_file_if_exists(current.temp_path)
        await update_task(task.id, status=TaskStatus.FAILED.value, error=str(e), temp_path=None)
        print(f"❌ TASK FAILED: {task.filename or task.url} -- {e}")
        log.error("TASK FAILED: %s (%s -> %s) -- %s", task.id, task.url, task.destination, e)
        log.exception("Full traceback for task %s:", task.id)


async def _worker_loop():
    await ensure_indexes()
    await reset_stuck_tasks_on_boot()

    # Reclaim disk space from any temp files not tied to an active task.
    active = await list_tasks()
    keep = {t.temp_path for t in active if t.temp_path}
    cleanup_orphan_temp_files(keep)

    log.info("Smart Upload Engine worker started.")
    while not _shutdown:
        try:
            task = await next_waiting_task()
            if task is None:
                await asyncio.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)
                continue
            if not has_enough_free_space(None):
                await update_task(task.id, status=TaskStatus.FAILED.value, error="Disk full: cannot start new tasks.")
                continue
            await _process_task(task)
        except Exception:
            log.exception("Unexpected error in worker loop; continuing.")
            await asyncio.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)


def start_worker():
    global _worker_task
    if _worker_task is None:
        _worker_task = asyncio.create_task(_worker_loop())


async def stop_worker():
    global _shutdown
    _shutdown = True
    if _worker_task:
        _worker_task.cancel()
