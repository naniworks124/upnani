"""
Central configuration for the Smart Upload Engine.
All values are read from environment variables (see .env.example).
Everything here has a sensible default except the actual upload
credentials — you only need to set what you're actually using.
"""
import os
from functools import lru_cache


class Settings:
    # ---- Google Drive (only needed if you use Google Drive uploads) ----
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REFRESH_TOKEN: str = os.getenv("GOOGLE_REFRESH_TOKEN", "")
    GOOGLE_DRIVE_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")  # optional target folder

    # ---- GoFile (only needed if you use GoFile uploads) ----
    GOFILE_API_TOKEN: str = os.getenv("GOFILE_API_TOKEN", "")

    # ---- BuzzHeavier ----
    BUZZHEAVIER_ACCOUNT_ID: str = os.getenv("BUZZHEAVIER_ACCOUNT_ID", "")
    BUZZHEAVIER_FOLDER_ID: str = os.getenv("BUZZHEAVIER_FOLDER_ID", "")

    # ---- Remote queue ("Auto from JSON" mode) ----
    # NOTE: these two values are ONLY used to pre-fill the URL/interval
    # fields in the dashboard's "Auto from JSON" panel. Setting them here
    # does NOT make the app auto-download anything on startup -- the
    # fetcher only ever runs after you explicitly click "Start" in the
    # frontend (POST /api/remote-queue/start). See remote_queue_fetcher.py.
    # Example: https://raw.githubusercontent.com/you/repo/main/down.json
    REMOTE_QUEUE_URL: str = os.getenv("REMOTE_QUEUE_URL", "")
    REMOTE_FETCH_INTERVAL_SECONDS: int = int(os.getenv("REMOTE_FETCH_INTERVAL_SECONDS", "60"))

    # ---- Everything below has a working default; only change if needed ----
    TEMP_DIR: str = os.getenv("TEMP_DIR", "/tmp/sue_workdir")
    MIN_FREE_DISK_MB: int = int(os.getenv("MIN_FREE_DISK_MB", "512"))
    DOWNLOAD_MAX_RETRIES: int = int(os.getenv("DOWNLOAD_MAX_RETRIES", "3"))
    UPLOAD_MAX_RETRIES: int = int(os.getenv("UPLOAD_MAX_RETRIES", "10"))
    CHUNK_SIZE_BYTES: int = int(os.getenv("CHUNK_SIZE_BYTES", str(8 * 1024 * 1024)))  # 8MB
    WORKER_POLL_INTERVAL_SECONDS: float = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "2"))
    MAX_CONCURRENT_TASKS: int = int(os.getenv("MAX_CONCURRENT_TASKS", "1"))
    ENABLE_STREAMING: bool = os.getenv("ENABLE_STREAMING", "true").lower() == "true"


@lru_cache
def get_settings() -> Settings:
    return Settings()
