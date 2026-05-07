from fastapi import APIRouter, Depends, HTTPException
import aiosqlite
from app.database.sqlite import get_db
from app.database.mongo import get_mdb
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
    mdb=Depends(get_mdb),
):
    # SQLite 금융 데이터
    sqlite_tables = ["personal_cb_stats", "corporate_cb_stats", "bank_products", "fund_products"]
    for t in sqlite_tables:
        await db.execute(f"DELETE FROM {t}")
    await db.commit()

    # MongoDB 사용자 데이터
    mongo_cols = ["chats", "portfolio", "orders", "broker_settings",
                  "crawled_docs", "audit_events"]
    for col in mongo_cols:
        await mdb[col].delete_many({})

    return {"ok": True, "message": f"SQLite {len(sqlite_tables)}테이블 + MongoDB {len(mongo_cols)}컬렉션 초기화 완료"}


@router.get("/stats")
async def db_stats(
    user=Depends(_require_admin),
    db: aiosqlite.Connection = Depends(get_db),
    mdb=Depends(get_mdb),
):
    stats: dict = {}

    # SQLite 통계
    for t in ["personal_cb_stats", "corporate_cb_stats", "bank_products", "fund_products"]:
        async with db.execute(f"SELECT COUNT(*) FROM {t}") as cur:
            row = await cur.fetchone()
            stats[f"sqlite.{t}"] = row[0] if row else 0

    # MongoDB 통계
    for col in ["chats", "portfolio", "orders", "broker_settings", "crawled_docs", "audit_events"]:
        stats[f"mongo.{col}"] = await mdb[col].count_documents({})

    return {"stats": stats}
