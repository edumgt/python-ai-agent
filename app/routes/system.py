"""시스템 관리 대시보드 라우터."""
from fastapi import APIRouter, Depends
from app.lib.session import get_current_user
from app.services.system_info import get_system_status

router = APIRouter(prefix="/api/system")


@router.get("/status")
async def system_status(user=Depends(get_current_user)):
    return await get_system_status()
