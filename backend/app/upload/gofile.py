"""
GoFile uploads. The API token is read only from the environment
(GOFILE_API_TOKEN) and is never logged or returned to the client.

Uses the fixed https://upload.gofile.io/uploadfile endpoint directly
(matches the known-working Colab script) rather than querying
/servers first for a server name -- one less network round trip and
one less thing that can fail.
"""
import time
import logging
from typing import AsyncIterator, Callable, Optional

import httpx

from ..config import get_settings

log = logging.getLogger("gofile")
settings = get_settings()

UPLOAD_URL = "https://upload.gofile.io/uploadfile"


class GoFileError(Exception):
    pass


def _auth_headers() -> dict:
    if not settings.GOFILE_API_TOKEN:
        raise GoFileError("GoFile API token is not configured.")
    return {"Authorization": f"Bearer {settings.GOFILE_API_TOKEN}"}


async def rename_content(content_id: str, new_name: str) -> None:
    """Renames the uploaded file/folder on GoFile. Best-effort: failures
    here shouldn't fail the whole task since the upload itself already
    succeeded."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.put(
                f"https://api.gofile.io/contents/{content_id}/update",
                headers=_auth_headers(),
                json={"attribute": "name", "attributeValue": new_name},
            )
    except Exception as e:
        log.warning("GoFile rename failed for %s (%s); continuing anyway.", content_id, e)


class _ProgressFile:
    """File-like wrapper providing .read() (what httpx's multipart encoder
    actually calls) while reporting progress -- a bare generator does NOT
    work here (httpx calls .read() on it and crashes with
    "'generator' object has no attribute 'read'"), which is why uploads
    were failing on every single attempt."""

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
            files = {"file": (filename, pf)}
            resp = await client.post(UPLOAD_URL, headers=headers, files=files)
            result = _parse_response(resp)
    finally:
        pf.close()

    if result.get("parent_folder"):
        import os
        folder_name = os.path.splitext(filename)[0]
        await rename_content(result["parent_folder"], folder_name)

    return result


async def stream_upload_from_iterator(
    byte_iter: AsyncIterator[bytes],
    filename: str,
    total_size: Optional[int],
    on_progress: Callable[[int, Optional[int], float], None],
) -> dict:
    """Attempts a direct pass-through upload without writing to disk.
    GoFile's server does not guarantee reliable handling of chunked/unknown
    length bodies, so the worker should treat any failure here as a signal
    to fall back to disk mode rather than retrying streaming indefinitely."""
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
        files = {"file": (filename, wrapped())}
        resp = await client.post(UPLOAD_URL, headers=headers, files=files)
        result = _parse_response(resp)

    if result.get("parent_folder"):
        import os
        folder_name = os.path.splitext(filename)[0]
        await rename_content(result["parent_folder"], folder_name)

    return result


def _parse_response(resp: httpx.Response) -> dict:
    if resp.status_code != 200:
        raise GoFileError(f"GoFile upload failed: HTTP {resp.status_code} {resp.text[:200]}")
    body = resp.json()
    if body.get("status") != "ok":
        raise GoFileError(f"GoFile upload failed: {body.get('status')}")
    data = body.get("data", {})
    return {
        "file_id": data.get("id") or data.get("fileId"),
        "link": data.get("downloadPage"),
        "parent_folder": data.get("parentFolder"),
    }
