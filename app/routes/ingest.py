import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.database.sqlite import get_db
from app.database.mongo import get_mdb
from app.lib.session import get_current_user
from app.lib.ollama import get_ollama
from app.services.financial_ingest import run_full_ingest
from app.services.crawl import run_auto_crawl, crawl_url, _chunk_text, _store_qdrant

router = APIRouter(prefix="/api")


@router.post("/ingest/financial")
async def ingest_financial(
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    log: list[str] = []
    result = await run_full_ingest(db, log)
    return {"ok": True, "result": result, "log": log}


@router.post("/ingest/crawl/auto")
async def crawl_auto(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    ollama = get_ollama()
    log: list[str] = []
    result = await run_auto_crawl(mdb, ollama, log)
    return {"ok": True, "result": result, "log": log}


class CrawlUrlBody(BaseModel):
    url: str


@router.post("/ingest/crawl/url")
async def crawl_manual(
    body: CrawlUrlBody,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    ollama = get_ollama()
    log: list[str] = []
    chunks = await crawl_url(body.url, mdb, ollama, log)
    return {"ok": True, "chunks": chunks, "log": log}


@router.post("/ingest/local-docs")
async def ingest_local_docs(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    """data/raw/ 하위 로컬 Markdown 문서를 Qdrant RAG에 인제스트."""
    ollama = get_ollama()
    log: list[str] = []
    total = 0

    raw_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "raw")
    )

    for dirpath, _, files in os.walk(raw_root):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(dirpath, fname)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            if len(content) < 50:
                continue

            chunks = _chunk_text(content)
            rel_path = os.path.relpath(fpath, raw_root)
            meta = {
                "url":    f"local://{rel_path}",
                "title":  fname,
                "source": f"local:{rel_path}",
            }
            stored = await _store_qdrant(chunks, meta, ollama)
            await mdb.crawled_docs.update_one(
                {"url": meta["url"]},
                {"$set": {"title": fname, "content": content[:5000],
                           "source": meta["source"],
                           "crawled_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
            log.append(f"✓ {rel_path} → {len(chunks)}청크 (Qdrant {stored}건)")
            total += len(chunks)

    return {"ok": True, "total_chunks": total, "log": log}


@router.get("/ingest/crawl/list")
async def list_crawled(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    cursor = mdb.crawled_docs.find(
        {}, {"_id": 0, "url": 1, "title": 1, "source": 1, "crawled_at": 1}
    ).sort("crawled_at", -1).limit(100)
    items = [doc async for doc in cursor]
    return {"items": items}
