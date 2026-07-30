"""
Security helpers: URL validation, filename sanitization, path-traversal
prevention, and secret redaction for logs/error messages.
"""
import os
import re
import ipaddress
from urllib.parse import urlparse

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._\- ]+")

# Hosts/IP ranges we refuse to fetch from, to reduce SSRF risk since the
# backend performs the download on the user's behalf.
_BLOCKED_HOSTS = {"localhost", "0.0.0.0", "metadata.google.internal"}


class InvalidURLError(ValueError):
    pass


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidURLError("Only http/https URLs are allowed.")
    if not parsed.netloc:
        raise InvalidURLError("URL is missing a host.")
    hostname = parsed.hostname or ""
    if hostname.lower() in _BLOCKED_HOSTS:
        raise InvalidURLError("This host is not allowed.")
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise InvalidURLError("Requests to private/internal IP ranges are not allowed.")
    except ValueError:
        pass  # hostname is a normal domain name, not a literal IP
    return url


def sanitize_filename(name: str) -> str:
    """Strip directory components and unsafe characters to prevent
    path traversal and filesystem issues."""
    name = os.path.basename(name or "")
    name = name.replace("..", "")
    name = _FILENAME_SAFE.sub("_", name).strip()
    if not name:
        name = "download.bin"
    return name[:200]


def safe_join(base_dir: str, filename: str) -> str:
    filename = sanitize_filename(filename)
    full = os.path.abspath(os.path.join(base_dir, filename))
    if not full.startswith(os.path.abspath(base_dir) + os.sep):
        raise InvalidURLError("Unsafe path detected.")
    return full


_SECRET_PATTERNS = [
    re.compile(r"(refresh_token=)[^&\s\"']+", re.I),
    re.compile(r"(client_secret=)[^&\s\"']+", re.I),
    re.compile(r"(access_token=)[^&\s\"']+", re.I),
    re.compile(r"(Authorization: Bearer )\S+", re.I),
]


def redact_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text
