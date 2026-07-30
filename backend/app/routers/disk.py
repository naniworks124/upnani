from fastapi import APIRouter
from ..disk_utils import get_disk_usage

router = APIRouter(prefix="/api/disk", tags=["disk"])


@router.get("")
async def disk_status():
    return get_disk_usage()
