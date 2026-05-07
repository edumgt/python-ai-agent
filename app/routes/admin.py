from fastapi import APIRouter, Depends, HTTPException
import aiosqlite
from app.database.sqlite import get_db
from app.lib.session import get_current_user

router = APIRouter(prefix="/api/admin")


def _require_admin(user=Depends(get_current_user)):
    if "admin" not in user.get("roles", []):
        raise HTTPException(403, "관리자 권한이 필요합니다.")
    return user


@router.post("/reset")
async def reset_db(
    user=Depends(_require_admin),
    db: aiosqlite.Connection = Depends(get_db),
):
    tables = ["personal_cb_stats", "corporate_cb_stats", "bank_products",
              "fund_products", "chats", "audit_events", "crawled_docs",
              "portfolio", "orders", "broker_settings"]
    for t in tables:
        await db.execute(f"DELETE FROM {t}")
    await db.commit()
    return {"ok": True, "message": f"{len(tables)}개 테이블 초기화 완료"}


@router.get("/stats")
async def db_stats(
    user=Depends(_require_admin),
    db: aiosqlite.Connection = Depends(get_db),
):
    stats = {}
    for t in ["personal_cb_stats", "corporate_cb_stats", "bank_products",
              "fund_products", "chats", "crawled_docs", "portfolio", "orders"]:
        async with db.execute(f"SELECT COUNT(*) FROM {t}") as cur:
            row = await cur.fetchone()
            stats[t] = row[0] if row else 0
    return {"stats": stats}
