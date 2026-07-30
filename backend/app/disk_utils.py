"""
Disk usage reporting + temp file cleanup helpers.
"""
import os
import shutil
from .config import get_settings

settings = get_settings()


def ensure_temp_dir():
    os.makedirs(settings.TEMP_DIR, exist_ok=True)


def get_disk_usage() -> dict:
    """Returns disk usage stats (bytes) for the partition holding TEMP_DIR."""
    ensure_temp_dir()
    total, used, free = shutil.disk_usage(settings.TEMP_DIR)
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "total_gb": round(total / (1024 ** 3), 2),
        "used_gb": round(used / (1024 ** 3), 2),
        "free_gb": round(free / (1024 ** 3), 2),
        "used_percent": round((used / total) * 100, 1) if total else 0.0,
    }


def has_enough_free_space(required_bytes: int | None) -> bool:
    """Checks free space against required size (if known) plus a safety margin."""
    usage = get_disk_usage()
    margin = settings.MIN_FREE_DISK_MB * 1024 * 1024
    if required_bytes is None:
        # Unknown size: just make sure we're above the safety margin.
        return usage["free_bytes"] > margin
    return usage["free_bytes"] > (required_bytes + margin)


def delete_file_if_exists(path: str | None):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def cleanup_orphan_temp_files(keep_paths: set[str]):
    """Removes any file in TEMP_DIR that isn't referenced by an active task.
    Used on worker startup to reclaim disk space from a previous crash."""
    ensure_temp_dir()
    for fname in os.listdir(settings.TEMP_DIR):
        full = os.path.join(settings.TEMP_DIR, fname)
        if full not in keep_paths and os.path.isfile(full):
            try:
                os.remove(full)
            except OSError:
                pass
