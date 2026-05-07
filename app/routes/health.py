from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/health")
async def health():
    return {"status": "ok", "service": "금융 AI Agent"}
