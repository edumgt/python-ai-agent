from fastapi import APIRouter, Depends, Query
import aiosqlite
from app.database.sqlite import get_db
from app.database.mongo import get_mdb
from app.lib.session import get_current_user
from app.lib.financial_tools import search_bank_products, search_funds

router = APIRouter(prefix="/api")


@router.get("/library/search")
async def library_search(
    q: str = Query(""),
    category: str = Query("all"),
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
    mdb=Depends(get_mdb),
):
    items = []

    if category in ("all", "bank"):
        result = await search_bank_products(db, {"keyword": q, "limit": 5})
        items.append({"type": "은행상품", "content": result})

    if category in ("all", "fund"):
        result = await search_funds(db, {"keyword": q, "limit": 5})
        items.append({"type": "펀드상품", "content": result})

    if category in ("all", "news"):
        cursor = mdb.crawled_docs.find(
            {"$or": [{"title": {"$regex": q, "$options": "i"}},
                     {"content": {"$regex": q, "$options": "i"}}]},
            {"_id": 0, "title": 1, "content": 1, "url": 1, "crawled_at": 1},
        ).sort("crawled_at", -1).limit(5)
        rows = [doc async for doc in cursor]
        if rows:
            news_text = "\n".join(
                f"[{r['title']}] {r.get('content','')[:200]}..." for r in rows
            )
            items.append({"type": "크롤링 문서", "content": news_text})

    return {"items": items, "query": q}
