"""
BuzzHeavier uploads. Credentials are read only from the environment
(BUZZHEAVIER_ACCOUNT_ID, BUZZHEAVIER_FOLDER_ID) and are never logged or
returned to the client.

BuzzHeavier's upload API is a plain PUT of the raw file bytes to
https://w.buzzheavier.com/{FOLDER_ID}/{filename}, authenticated with a
Bearer token. Because the body is raw bytes (not multipart, unlike
GoFile), this provider can genuinely stream a download straight through
to the upload without ever touching disk -- httpx accepts an async
iterator directly as the request body.
"""
import time
import logging
from typing import AsyncIterator, Callable, Optional

import httpx

from ..config import get_settings

log = logging.getLogger("buzzheavier")
settings = get_settings()


class BuzzHeavierError(Exception):
    pass


def _auth_headers() -> dict:
    if not settings.BUZZHEAVIER_ACCOUNT_ID:
        raise BuzzHeavierError("BuzzHeavier account ID is not configured.")
    return {"Authorization": f"Bearer {settings.BUZZHEAVIER_ACCOUNT_ID}"}


def _upload_url(filename: str) -> str:
    if not settings.BUZZHEAVIER_FOLDER_ID:
        raise BuzzHeavierError("BuzzHeavier folder ID is not configured.")
    return f"https://w.buzzheavier.com/{settings.BUZZHEAVIER_FOLDER_ID}/{filename}"


class _ProgressFile:
    """Sync file wrapper that reports progress as it's read, used for the
    disk-mode upload path."""

    def __init__(self, path: str, total_size: int, on_progress: Callable[[int, int, float], None]):
        self._f = open(path, "rb")
        self._total = total_size
        self._on_progress = on_progress
        self._uploaded = 0
        self._start = time.time()

    def read(self, size: int = -1) -> bytes:
        chunk = self._f.read(size if size and size > 0 else settings.CHUNK_SIZE_BYTES)
        if chunk:
            self._uploaded += len(chunk)
            elapsed = max(time.time() - self._start, 1e-6)
            self._on_progress(self._uploaded, self._total, self._uploaded / elapsed)
        return chunk

    def close(self):
        self._f.close()


async def _file_to_async_iter(pf: "_ProgressFile"):
    """Adapts the synchronous _ProgressFile.read() into an async iterator
    so httpx can stream it as the PUT body instead of loading the whole
    file into memory at once."""
    while True:
        chunk = pf.read(settings.CHUNK_SIZE_BYTES)
        if not chunk:
            break
        yield chunk


async def upload_file_from_disk(
    file_path: str,
    filename: str,
    total_size: int,
    on_progress: Callable[[int, int, float], None],
) -> dict:
    headers = _auth_headers()
    pf = _ProgressFile(file_path, total_size, on_progress)

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.put(_upload_url(filename), headers=headers, content=_file_to_async_iter(pf))
            result = _parse_response(resp)
    finally:
        pf.close()

    return result


async def stream_upload_from_iterator(
    byte_iter: AsyncIterator[bytes],
    filename: str,
    total_size: Optional[int],
    on_progress: Callable[[int, Optional[int], float], None],
) -> dict:
    """True pass-through streaming: bytes from the download are forwarded
    directly as the PUT body, never written to disk."""
    headers = _auth_headers()

    uploaded = 0
    start_time = time.time()

    async def wrapped():
        nonlocal uploaded
        async for chunk in byte_iter:
            uploaded += len(chunk)
            elapsed = max(time.time() - start_time, 1e-6)
            on_progress(uploaded, total_size, uploaded / elapsed)
            yield chunk

    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.put(_upload_url(filename), headers=headers, content=wrapped())
        result = _parse_response(resp)

    return result


def _parse_response(resp: httpx.Response) -> dict:
    if resp.status_code not in (200, 201):
        raise BuzzHeavierError(f"BuzzHeavier upload failed: HTTP {resp.status_code} {resp.text[:200]}")
    body = resp.json()
    data = body.get("data", body)
    download_url = data.get("downloadUrl") or data.get("url")
    if not download_url:
        raise BuzzHeavierError(f"BuzzHeavier upload response missing download URL: {body}")
    return {
        "file_id": data.get("id"),
        "link": download_url,
    }
