"""
Data models for upload tasks. Stored as plain dicts in MongoDB but
validated/shaped through these Pydantic models at the API boundary.
"""
import uuid
import time
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl


class TaskStatus(str, Enum):
    WAITING = "waiting"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class Destination(str, Enum):
    GOOGLE_DRIVE = "google_drive"
    GOFILE = "gofile"
    BUZZHEAVIER = "buzzheavier"


class TaskCreateRequest(BaseModel):
    url: HttpUrl
    destination: Destination
    filename_override: Optional[str] = None


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    url: str
    destination: Destination
    filename: Optional[str] = None
    status: TaskStatus = TaskStatus.WAITING
    method: Optional[str] = None  # "stream" or "disk"

    # progress
    bytes_downloaded: int = 0
    bytes_uploaded: int = 0
    total_bytes: Optional[int] = None
    download_speed_bps: float = 0.0
    upload_speed_bps: float = 0.0
    eta_seconds: Optional[float] = None

    # retry bookkeeping
    download_attempts: int = 0
    upload_attempts: int = 0

    # disk mode bookkeeping
    temp_path: Optional[str] = None
    resumable_offset: int = 0
    supports_resume: Optional[bool] = None

    # upload provider state (e.g. Google resumable session URI)
    upload_session_uri: Optional[str] = None
    remote_file_id: Optional[str] = None
    remote_link: Optional[str] = None

    error: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def touch(self):
        self.updated_at = time.time()
