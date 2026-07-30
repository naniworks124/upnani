"""
Thin persistence layer over the `tasks` MongoDB collection. All task state
mutation goes through here so the worker and API always agree on shape.
"""
import time
from typing import Optional

from .database import tasks_collection
from .models import Task, TaskStatus


async def create_task(task: Task) -> Task:
    await tasks_collection().insert_one(task.model_dump())
    return task


async def get_task(task_id: str) -> Optional[Task]:
    doc = await tasks_collection().find_one({"id": task_id})
    return Task(**doc) if doc else None


async def list_tasks(status: Optional[str] = None, limit: int = 200) -> list[Task]:
    query = {"status": status} if status else {}
    cursor = tasks_collection().find(query).sort("created_at", -1).limit(limit)
    return [Task(**doc) async for doc in cursor]


async def update_task(task_id: str, **fields) -> None:
    fields["updated_at"] = time.time()
    await tasks_collection().update_one({"id": task_id}, {"$set": fields})


async def next_waiting_task() -> Optional[Task]:
    doc = await tasks_collection().find_one({"status": TaskStatus.WAITING.value}, sort=[("created_at", 1)])
    return Task(**doc) if doc else None


async def reset_stuck_tasks_on_boot() -> None:
    """On worker startup, any task left mid-flight from a crash/restart is
    reset back to WAITING so it can be recovered from MongoDB. Disk-mode
    tasks keep their resumable_offset so partially downloaded files aren't
    wasted; the download engine will resume or restart depending on server
    support."""
    await tasks_collection().update_many(
        {"status": {"$in": [TaskStatus.DOWNLOADING.value, TaskStatus.UPLOADING.value]}},
        {"$set": {"status": TaskStatus.WAITING.value, "updated_at": time.time()}},
    )


async def cancel_task(task_id: str) -> bool:
    result = await tasks_collection().update_one(
        {"id": task_id, "status": {"$in": [TaskStatus.WAITING.value, TaskStatus.DOWNLOADING.value,
                                            TaskStatus.UPLOADING.value, TaskStatus.PAUSED.value]}},
        {"$set": {"status": TaskStatus.CANCELLED.value, "updated_at": time.time()}},
    )
    return result.modified_count > 0


async def delete_task(task_id: str) -> bool:
    result = await tasks_collection().delete_one({"id": task_id})
    return result.deleted_count > 0
