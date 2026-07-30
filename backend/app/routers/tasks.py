"""
REST API for submitting URLs and inspecting/controlling the upload queue.
Single-user app: no auth on these routes beyond the basic-auth login gate
applied at the app level (see main.py).
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models import Task, TaskCreateRequest, TaskStatus, Destination
from ..queue_manager import create_task, list_tasks, get_task, cancel_task, delete_task
from ..security import validate_url, InvalidURLError, sanitize_filename

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class BulkTaskCreateRequest(BaseModel):
    urls: list[str]
    destination: Destination


@router.post("/bulk", response_model=list[Task])
async def submit_bulk_tasks(req: BulkTaskCreateRequest):
    created = []
    errors = []
    for raw_url in req.urls:
        url = raw_url.strip()
        if not url:
            continue
        try:
            validate_url(url)
        except InvalidURLError as e:
            errors.append(f"{url}: {e}")
            continue
        task = Task(url=url, destination=req.destination)
        await create_task(task)
        created.append(task)
    if not created and errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    return created


@router.post("/cancel-all")
async def cancel_all():
    tasks = await list_tasks()
    cancelled = 0
    for t in tasks:
        if t.status in (TaskStatus.WAITING, TaskStatus.DOWNLOADING, TaskStatus.UPLOADING, TaskStatus.PAUSED):
            if await cancel_task(t.id):
                cancelled += 1
    return {"cancelled": cancelled}


@router.post("", response_model=Task)
async def submit_task(req: TaskCreateRequest):
    try:
        validate_url(str(req.url))
    except InvalidURLError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = sanitize_filename(req.filename_override) if req.filename_override else None
    task = Task(url=str(req.url), destination=req.destination, filename=filename)
    await create_task(task)
    return task


@router.get("", response_model=list[Task])
async def get_queue(status: str | None = None):
    if status and status not in [s.value for s in TaskStatus]:
        raise HTTPException(status_code=400, detail="Invalid status filter.")
    return await list_tasks(status=status)


@router.get("/{task_id}", response_model=Task)
async def get_single_task(task_id: str):
    task = await get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.post("/{task_id}/cancel")
async def cancel(task_id: str):
    ok = await cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Task cannot be cancelled (already finished or not found).")
    return {"cancelled": True}


@router.delete("/{task_id}")
async def remove(task_id: str):
    task = await get_task(task_id)
    if task and task.status in (TaskStatus.WAITING, TaskStatus.DOWNLOADING, TaskStatus.UPLOADING):
        raise HTTPException(status_code=400, detail="Cancel the task before deleting it.")
    ok = await delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"deleted": True}
