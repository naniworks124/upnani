"""
Simple local JSON-file storage for task state — replaces MongoDB.

This app is single-user, low-volume (a handful of downloads a month), so
a database is unnecessary complexity. Task state just lives in one JSON
file on disk, protected by an asyncio lock so concurrent reads/writes
from the API and the worker don't corrupt it.
"""
import asyncio
import json
import os
from .config import get_settings

settings = get_settings()

_STORE_PATH = os.path.abspath(os.path.join(settings.TEMP_DIR, "..", "tasks_store.json"))
_lock = asyncio.Lock()


def _ensure_store():
    os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
    if not os.path.exists(_STORE_PATH):
        with open(_STORE_PATH, "w") as f:
            json.dump({}, f)


def _read_all() -> dict:
    _ensure_store()
    with open(_STORE_PATH, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _write_all(data: dict):
    _ensure_store()
    tmp_path = _STORE_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, _STORE_PATH)  # atomic on POSIX


class TaskStore:
    """Minimal MongoDB-collection-like interface, just enough for
    queue_manager.py to use without needing a real database driver."""

    async def insert_one(self, doc: dict):
        async with _lock:
            data = _read_all()
            data[doc["id"]] = doc
            _write_all(data)

    async def find_one(self, query: dict, sort: list | None = None):
        async with _lock:
            data = _read_all()
            matches = [doc for doc in data.values() if _matches(doc, query)]
            if not matches:
                return None
            if sort:
                field, direction = sort[0]
                matches.sort(key=lambda d: d.get(field, 0), reverse=(direction == -1))
            return matches[0]

    def find(self, query: dict | None = None):
        data = _read_all()
        results = [doc for doc in data.values() if _matches(doc, query or {})]
        results.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        return _AsyncCursor(results)

    async def update_one(self, query: dict, update: dict):
        async with _lock:
            data = _read_all()
            for doc in data.values():
                if _matches(doc, query):
                    doc.update(update.get("$set", {}))
                    _write_all(data)
                    return _Result(modified_count=1)
            return _Result(modified_count=0)

    async def update_many(self, query: dict, update: dict):
        async with _lock:
            data = _read_all()
            count = 0
            for doc in data.values():
                if _matches(doc, query):
                    doc.update(update.get("$set", {}))
                    count += 1
            if count:
                _write_all(data)
            return _Result(modified_count=count)

    async def delete_one(self, query: dict):
        async with _lock:
            data = _read_all()
            for tid, doc in list(data.items()):
                if _matches(doc, query):
                    del data[tid]
                    _write_all(data)
                    return _Result(deleted_count=1)
            return _Result(deleted_count=0)

    async def create_index(self, *args, **kwargs):
        pass  # no-op; a flat JSON file doesn't need indexes


class _AsyncCursor:
    """Lets `async for doc in tasks_collection().find(...)` work like Motor."""
    def __init__(self, items: list):
        self._items = items

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, n: int):
        self._items = self._items[:n]
        return self

    def __aiter__(self):
        self._iter = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _Result:
    def __init__(self, modified_count=0, deleted_count=0):
        self.modified_count = modified_count
        self.deleted_count = deleted_count


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


_store = TaskStore()


def tasks_collection() -> TaskStore:
    return _store


async def ensure_indexes():
    pass  # not needed for a JSON file
