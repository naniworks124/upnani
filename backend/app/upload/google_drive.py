"""
Google Drive uploads using an OAuth refresh token (never token.pickle).

Access tokens are minted on-demand from the long-lived refresh token via
the standard OAuth2 token endpoint, and are never persisted to disk.
Uses Drive's resumable upload protocol so large files can survive
transient network failures without restarting from byte 0.
"""
import asyncio
import logging
import os
import time
from typing import AsyncIterator, Callable, Optional

import httpx

from ..config import get_settings

log = logging.getLogger("google_drive")
settings = get_settings()

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"

_cached_access_token: Optional[str] = None
_cached_expiry: float = 0.0


class GoogleDriveError(Exception):
    pass


async def _get_access_token(client: httpx.AsyncClient) -> str:
    """Returns a cached access token or refreshes a new one. Never touches
    disk; lives only in process memory for its short lifetime."""
    global _cached_access_token, _cached_expiry
    if _cached_access_token and time.time() < _cached_expiry - 60:
        return _cached_access_token

    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_REFRESH_TOKEN):
        raise GoogleDriveError("Google Drive credentials are not configured (check environment secrets).")

    resp = await client.post(
        TOKEN_URL,
        data={
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": settings.GOOGLE_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise GoogleDriveError("Authentication expired or invalid (token refresh failed).")
    data = resp.json()
    _cached_access_token = data["access_token"]
    _cached_expiry = time.time() + data.get("expires_in", 3600)
    return _cached_access_token


async def create_resumable_session(filename: str, size: Optional[int], mime_type: str = "application/octet-stream") -> str:
    """Creates a resumable upload session and returns its session URI."""
    async with httpx.AsyncClient() as client:
        token = await _get_access_token(client)
        metadata = {"name": filename}
        if settings.GOOGLE_DRIVE_FOLDER_ID:
            metadata["parents"] = [settings.GOOGLE_DRIVE_FOLDER_ID]
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": mime_type,
        }
        if size is not None:
            headers["X-Upload-Content-Length"] = str(size)
        resp = await client.post(UPLOAD_URL, headers=headers, json=metadata, timeout=30)
        if resp.status_code == 403 and "quota" in resp.text.lower():
            raise GoogleDriveError("Drive quota exceeded")
        if resp.status_code not in (200,):
            raise GoogleDriveError(f"Failed to start resumable session: HTTP {resp.status_code}")
        session_uri = resp.headers.get("location")
        if not session_uri:
            raise GoogleDriveError("Drive did not return a resumable session URI.")
        return session_uri


async def get_upload_offset(session_uri: str, total_size: Optional[int]) -> int:
    """Queries Drive for how many bytes it has already received, so an
    interrupted resumable upload can continue where it left off."""
    async with httpx.AsyncClient() as client:
        headers = {"Content-Range": f"bytes */{total_size if total_size is not None else '*'}"}
        resp = await client.put(session_uri, headers=headers, timeout=30)
        if resp.status_code in (200, 201):
            return total_size or 0  # already complete
        if resp.status_code == 308:
            range_header = resp.headers.get("range")
            if range_header and "-" in range_header:
                return int(range_header.split("-")[-1]) + 1
            return 0
        if resp.status_code == 404:
            raise GoogleDriveError("Upload session expired; a new session is required.")
        raise GoogleDriveError(f"Unexpected status while checking offset: HTTP {resp.status_code}")


async def upload_file_from_disk(
    session_uri: str,
    file_path: str,
    total_size: int,
    start_offset: int,
    on_progress: Callable[[int, int, float], None],
) -> dict:
    """Uploads (or resumes uploading) a file already on disk to an existing
    resumable session, chunked so progress can be reported."""
    chunk_size = settings.CHUNK_SIZE_BYTES
    uploaded = start_offset
    start_time = time.time()

    async with httpx.AsyncClient(timeout=None) as client:
        with open(file_path, "rb") as f:
            f.seek(start_offset)
            while uploaded < total_size:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                chunk_start = uploaded
                chunk_end = uploaded + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {chunk_start}-{chunk_end}/{total_size}",
                }
                resp = await client.put(session_uri, headers=headers, content=chunk)
                if resp.status_code in (200, 201):
                    uploaded += len(chunk)
                    elapsed = max(time.time() - start_time, 1e-6)
                    on_progress(uploaded, total_size, (uploaded - start_offset) / elapsed)
                    body = resp.json()
                    return {"file_id": body.get("id"), "link": body.get("webViewLink") or f"https://drive.google.com/file/d/{body.get('id')}/view"}
                elif resp.status_code == 308:
                    uploaded += len(chunk)
                    elapsed = max(time.time() - start_time, 1e-6)
                    on_progress(uploaded, total_size, (uploaded - start_offset) / elapsed)
                    continue
                elif resp.status_code == 403 and "quota" in resp.text.lower():
                    raise GoogleDriveError("Drive quota exceeded")
                else:
                    raise GoogleDriveError(f"Upload chunk failed: HTTP {resp.status_code} {resp.text[:200]}")
    raise GoogleDriveError("Upload ended unexpectedly before completion.")


async def stream_upload_from_iterator(
    session_uri: str,
    byte_iter: AsyncIterator[bytes],
    total_size: Optional[int],
    on_progress: Callable[[int, Optional[int], float], None],
) -> dict:
    """True streaming upload: pipes bytes from an async iterator (e.g. a
    download response body) directly into the resumable session without
    ever writing to disk. Requires a known total_size for a single-shot
    PUT with Content-Range; if unknown, caller should use disk mode instead
    since Drive's resumable protocol needs deterministic chunk ranges for
    unknown-length streaming."""
    if total_size is None:
        raise GoogleDriveError("Streaming upload requires a known file size.")

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
        headers = {"Content-Length": str(total_size)}
        resp = await client.put(session_uri, headers=headers, content=wrapped())
        if resp.status_code in (200, 201):
            body = resp.json()
            return {"file_id": body.get("id"), "link": body.get("webViewLink") or f"https://drive.google.com/file/d/{body.get('id')}/view"}
        if resp.status_code == 403 and "quota" in resp.text.lower():
            raise GoogleDriveError("Drive quota exceeded")
        raise GoogleDriveError(f"Streaming upload failed: HTTP {resp.status_code} {resp.text[:200]}")
