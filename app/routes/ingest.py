from fastapi import APIRouter, Depends
import aiosqlite
from pydantic import BaseModel
from app.database.sqlite import get_db
from app.lib.session import get_current_user
from app.lib.ollama import get_ollama
from app.services.financial_ingest import run_full_ingest
from app.services.crawl import run_auto_crawl, crawl_url

router = APIRouter(prefix="/api")


@router.post("/ingest/financial")
async def ingest_financial(
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    log: list[str] = []
    result = await run_full_ingest(db, log)
    return {"ok": True, "result": result, "log": log}


@router.post("/ingest/crawl/auto")
async def crawl_auto(
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    ollama = get_ollama()
    log: list[str] = []
    result = await run_auto_crawl(db, ollama, log)
    return {"ok": True, "result": result, "log": log}


class CrawlUrlBody(BaseModel):
    url: str


@router.post("/ingest/crawl/url")
async def crawl_manual(
    body: CrawlUrlBody,
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    ollama = get_ollama()
    log: list[str] = []
    chunks = await crawl_url(body.url, db, ollama, log)
    return {"ok": True, "chunks": chunks, "log": log}


@router.get("/ingest/crawl/list")
async def list_crawled(
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    async with db.execute(
        "SELECT url, title, source, crawled_at FROM crawled_docs ORDER BY crawled_at DESC LIMIT 100"
    ) as cur:
        rows = await cur.fetchall()
    return {"items": [dict(r) for r in rows]}
