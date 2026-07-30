"""
Download engine.

Supports:
 - HTTP redirects (handled natively by httpx)
 - HTTP Range requests / resume
 - Automatic retries with exponential backoff (up to DOWNLOAD_MAX_RETRIES)
 - Filename detection via Content-Disposition, falling back to the URL path
 - Unknown file size (Content-Length may be absent -> total_bytes stays None)

`download_to_disk` is used for disk-mode. `stream_download_iter` yields
chunks for true streaming mode (piped directly into an upload request
without ever touching disk).
"""
import asyncio
import os
import re
import time
import logging
from typing import AsyncIterator, Callable, Optional

import httpx

from .config import get_settings
from .security import sanitize_filename, validate_url, redact_secrets
from .disk_utils import has_enough_free_space, delete_file_if_exists

log = logging.getLogger("download_engine")
settings = get_settings()

# Some CDNs/hosts (e.g. seedr.cc) silently 404 requests without a
# browser-like User-Agent instead of returning a proper 403. Sending one
# by default avoids that whole class of false "file not found" errors.
_DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}

_CD_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.I)


class DownloadError(Exception):
    pass


def detect_filename(url: str, headers: httpx.Headers) -> str:
    cd = headers.get("content-disposition")
    if cd:
        match = _CD_FILENAME_RE.search(cd)
        if match:
            name = match.group(1)
        else:
            name = None
    else:
        name = None

    if not name:
        path = httpx.URL(url).path
        name = os.path.basename(path) or "download.bin"

    # Decode %xx escapes (e.g. "%20" -> " ") before sanitizing.
    from urllib.parse import unquote
    name = unquote(name)

    # Strip any stray query string that leaked in via the URL fallback.
    if "?" in name:
        name = name.split("?")[0]

    if not name.strip():
        name = "download.bin"

    name = sanitize_filename(name)

    # Force a fallback extension if none present, so uploads always have one.
    if "." not in name:
        name += ".bin"

    return name


async def probe(url: str, client: httpx.AsyncClient) -> dict:
    """HEAD request (falls back to a ranged GET) to discover size, filename,
    and whether the server supports Range/resume."""
    validate_url(url)
    try:
        resp = await client.head(url, follow_redirects=True, timeout=30, headers=_DEFAULT_HEADERS)
        headers = resp.headers
        supports_resume = headers.get("accept-ranges", "").lower() == "bytes"
        size = int(headers["content-length"]) if "content-length" in headers else None
        filename = detect_filename(str(resp.url), headers)
        return {"size": size, "filename": filename, "supports_resume": supports_resume}
    except Exception:
        # Some servers reject HEAD; fall back to a tiny ranged GET.
        try:
            headers = {**_DEFAULT_HEADERS, "Range": "bytes=0-0"}
            resp = await client.get(url, headers=headers, follow_redirects=True, timeout=30)
            supports_resume = resp.status_code == 206
            size = None
            cr = resp.headers.get("content-range")
            if cr and "/" in cr:
                total = cr.split("/")[-1]
                if total.isdigit():
                    size = int(total)
            filename = detect_filename(str(resp.url), resp.headers)
            return {"size": size, "filename": filename, "supports_resume": supports_resume}
        except Exception as e:
            raise DownloadError(redact_secrets(f"Unable to reach source URL: {e}"))


async def download_to_disk(
    url: str,
    dest_path: str,
    on_progress: Callable[[int, Optional[int], float], None],
    resume_offset: int = 0,
) -> dict:
    """Downloads `url` to `dest_path`, resuming from resume_offset if the
    file partially exists and the server supports it. Retries with
    exponential backoff up to DOWNLOAD_MAX_RETRIES times. Returns final
    metadata dict {size, filename, supports_resume}."""
    validate_url(url)
    attempt = 0
    last_error = None
    total_size: Optional[int] = None
    supports_resume = False
    filename = os.path.basename(dest_path)

    async with httpx.AsyncClient() as client:
        meta = await probe(url, client)
        total_size = meta["size"]
        supports_resume = meta["supports_resume"]
        filename = meta["filename"] or filename

        if not has_enough_free_space(total_size):
            raise DownloadError("Disk full: not enough free space to safely download this file.")

        offset = resume_offset if (resume_offset and supports_resume) else 0
        if offset == 0 and os.path.exists(dest_path):
            delete_file_if_exists(dest_path)

        while attempt < settings.DOWNLOAD_MAX_RETRIES:
            try:
                headers = dict(_DEFAULT_HEADERS)
                mode = "ab" if offset > 0 else "wb"
                if offset > 0:
                    headers["Range"] = f"bytes={offset}-"
                start_time = time.time()
                downloaded_this_attempt = 0
                async with client.stream("GET", url, headers=headers, follow_redirects=True, timeout=None) as resp:
                    if offset > 0 and resp.status_code != 206:
                        # Server ignored our Range request -> restart from scratch.
                        offset = 0
                        mode = "wb"
                        delete_file_if_exists(dest_path)
                    elif resp.status_code >= 400:
                        raise DownloadError(f"Remote server returned HTTP {resp.status_code}")

                    with open(dest_path, mode) as f:
                        async for chunk in resp.aiter_bytes(settings.CHUNK_SIZE_BYTES):
                            if not chunk:
                                continue
                            f.write(chunk)
                            offset += len(chunk)
                            downloaded_this_attempt += len(chunk)
                            elapsed = max(time.time() - start_time, 1e-6)
                            speed = downloaded_this_attempt / elapsed
                            on_progress(offset, total_size, speed)
                return {"size": total_size or offset, "filename": filename, "supports_resume": supports_resume}
            except (httpx.HTTPError, DownloadError, OSError) as e:
                last_error = e
                attempt += 1
                if not supports_resume:
                    # Can't resume: wipe partial data before retrying.
                    delete_file_if_exists(dest_path)
                    offset = 0
                if attempt >= settings.DOWNLOAD_MAX_RETRIES:
                    break
                backoff = min(2 ** attempt, 60)
                log.warning("Download attempt %s failed (%s), retrying in %ss", attempt, e, backoff)
                await asyncio.sleep(backoff)

    if not supports_resume:
        delete_file_if_exists(dest_path)
    raise DownloadError(redact_secrets(f"Download failed after {attempt} attempts: {last_error}"))


async def stream_download_iter(url: str) -> tuple[httpx.AsyncClient, httpx.Response, dict]:
    """Opens a streaming GET for true pass-through streaming mode. Caller is
    responsible for closing the returned client/response. Retries are not
    performed mid-stream here (true streaming can't resume a partially
    consumed upload); the caller should fall back to disk mode on failure."""
    validate_url(url)
    client = httpx.AsyncClient(timeout=None)
    resp = await client.send(client.build_request("GET", url, headers=_DEFAULT_HEADERS), stream=True, follow_redirects=True)
    if resp.status_code >= 400:
        await resp.aclose()
        await client.aclose()
        raise DownloadError(f"Remote server returned HTTP {resp.status_code}")
    meta = {
        "size": int(resp.headers["content-length"]) if "content-length" in resp.headers else None,
        "filename": detect_filename(str(resp.url), resp.headers),
    }
    return client, resp, meta
