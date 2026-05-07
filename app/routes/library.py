from fastapi import APIRouter, Depends, Query
import aiosqlite
from app.database.sqlite import get_db
from app.lib.session import get_current_user
from app.lib.financial_tools import search_bank_products, search_funds

router = APIRouter(prefix="/api")


@router.get("/library/search")
async def library_search(
    q: str = Query(""),
    category: str = Query("all"),  # all | bank | fund | news
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    items = []

    if category in ("all", "bank"):
        result = await search_bank_products(db, {"keyword": q, "limit": 5})
        items.append({"type": "은행상품", "content": result})

    if category in ("all", "fund"):
        result = await search_funds(db, {"keyword": q, "limit": 5})
        items.append({"type": "펀드상품", "content": result})

    if category in ("all", "news"):
        async with db.execute(
            "SELECT title, content, url, crawled_at FROM crawled_docs "
            "WHERE title LIKE ? OR content LIKE ? "
            "ORDER BY crawled_at DESC LIMIT 5",
            (f"%{q}%", f"%{q}%"),
        ) as cur:
            rows = await cur.fetchall()
        if rows:
            news_text = "\n".join(
                f"[{r['title']}] {r['content'][:200]}..." for r in rows
            )
            items.append({"type": "크롤링 문서", "content": news_text})

    return {"items": items, "query": q}
