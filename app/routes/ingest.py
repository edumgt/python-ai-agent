import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.database.mongo import get_mdb
from app.lib.session import get_current_user
from app.lib.ollama import get_ollama
from app.services.financial_ingest import run_full_ingest
from app.services.crawl import run_auto_crawl, crawl_url, crawl_naver_stock, _chunk_text, _store_qdrant
from app.services.translation_ingest import (
    run_translation_ingest,
    translation_search,
    TRANSLATION_COLLECTION,
)

router = APIRouter(prefix="/api")


@router.post("/ingest/financial")
async def ingest_financial(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    log: list[str] = []
    result = await run_full_ingest(mdb, log)
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


class CrawlNaverBody(BaseModel):
    code: str  # 6-digit stock code (e.g. 005930)


@router.post("/ingest/crawl/naver")
async def crawl_naver(
    body: CrawlNaverBody,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    """네이버 금융 종목 페이지 전용 크롤링."""
    ollama = get_ollama()
    log: list[str] = []
    chunks = await crawl_naver_stock(body.code, mdb, ollama, log)
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


class TranslationIngestBody(BaseModel):
    data_type: str = "labeled"       # "labeled" | "source" | "all"
    categories: list[str] = []       # [] = 전체. e.g. ["news","report"]
    languages: list[str] = []        # [] = 전체. e.g. ["en","ja"]
    max_docs: int = 0                # 0 = 무제한


@router.post("/ingest/translation-data")
async def ingest_translation_data(
    body: TranslationIngestBody,
    user=Depends(get_current_user),
):
    """data/1.데이터 다국어 번역 ZIP → Qdrant translation_docs 컬렉션 인제스트."""
    ollama = get_ollama()
    log: list[str] = []
    result = await run_translation_ingest(
        ollama,
        log,
        data_type=body.data_type,
        categories=body.categories or None,
        languages=body.languages or None,
        max_docs=body.max_docs,
    )
    return {"ok": True, "result": result, "log": log}


class TranslationSearchBody(BaseModel):
    query: str
    top_k: int = 5
    category: str | None = None
    target_language: str | None = None


@router.post("/ingest/translation-search")
async def search_translation(
    body: TranslationSearchBody,
    user=Depends(get_current_user),
):
    """translation_docs 컬렉션에서 한국어 쿼리로 유사 문서 검색."""
    ollama = get_ollama()
    hits = await translation_search(
        body.query,
        ollama,
        top_k=body.top_k,
        category=body.category,
        target_language=body.target_language,
    )
    return {"ok": True, "hits": hits, "collection": TRANSLATION_COLLECTION}


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
