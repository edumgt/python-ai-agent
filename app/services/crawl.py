"""크롤링 서비스: GitHub docs, 금융 포털, Qdrant RAG 구축."""
import httpx
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from bs4 import BeautifulSoup
from app.config import settings
from app.lib.ollama import OllamaClient

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"

CRAWL_TARGETS = [
    {
        "name": "python-quant docs (GitHub)",
        "type": "github_docs",
        "owner": "edumgt",
        "repo": "python-quant",
        "branch": "main",
        "path": "docs",
    },
]


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._texts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self._texts.append(t)

    def get_text(self) -> str:
        return " ".join(self._texts)


def _extract_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return p.get_text()


def _chunk_text(text: str) -> list[str]:
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i:i + CHUNK_SIZE])
        if chunk:
            chunks.append(chunk)
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


async def _store_qdrant(chunks: list[str], meta: dict, ollama: OllamaClient) -> int:
    """Qdrant에 임베딩 저장 (Qdrant 미연결 시 스킵)."""
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.http.models import PointStruct

        client = AsyncQdrantClient(url=settings.QDRANT_URL)
        collection = settings.QDRANT_COLLECTION

        # Ensure collection exists
        try:
            await client.get_collection(collection)
        except Exception:
            from qdrant_client.http.models import Distance, VectorParams
            dim = 768  # nomic-embed-text dimension
            test_emb = await ollama.embed(settings.EMBED_MODEL, "test")
            dim = len(test_emb) if test_emb else dim
            await client.create_collection(
                collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

        points = []
        for i, chunk in enumerate(chunks):
            emb = await ollama.embed(settings.EMBED_MODEL, chunk)
            if not emb:
                continue
            point_id = abs(hash(f"{meta.get('url', '')}-{i}")) % (2 ** 63)
            points.append(PointStruct(
                id=point_id,
                vector=emb,
                payload={**meta, "text": chunk, "chunk_index": i},
            ))

        if points:
            await client.upsert(collection_name=collection, points=points)
        await client.close()
        return len(points)
    except Exception as e:
        return 0  # silently skip if Qdrant unavailable


async def crawl_github_docs(
    owner: str, repo: str, branch: str, path: str,
    mdb, ollama: OllamaClient, log: list[str],
) -> int:
    """GitHub 레포의 markdown docs를 크롤링하여 Qdrant에 저장."""
    total = 0
    api_url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={branch}"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(api_url, headers={"Accept": "application/vnd.github.v3+json"})
            resp.raise_for_status()
            files = resp.json()
        except Exception as e:
            log.append(f"[ERROR] GitHub API 실패: {e}")
            return 0

        for f in files:
            if not (isinstance(f, dict) and f.get("type") == "file"
                    and f.get("name", "").endswith(".md")):
                continue

            raw_url = f"{GITHUB_RAW}/{owner}/{repo}/{branch}/{path}/{f['name']}"
            try:
                r = await client.get(raw_url)
                r.raise_for_status()
                content = r.text
            except Exception as e:
                log.append(f"  [SKIP] {f['name']}: {e}")
                continue

            # Remove markdown syntax for cleaner text
            text = re.sub(r"```[\s\S]*?```", "", content)
            text = re.sub(r"`[^`]+`", "", text)
            text = re.sub(r"#+\s+", "", text)
            text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
            text = re.sub(r"[*_~]{1,3}", "", text)
            text = " ".join(text.split())

            if len(text) < 50:
                continue

            chunks = _chunk_text(text)
            url = f"https://github.com/{owner}/{repo}/blob/{branch}/{path}/{f['name']}"
            meta = {"url": url, "title": f["name"], "source": f"github:{owner}/{repo}"}

            stored = await _store_qdrant(chunks, meta, ollama)
            await mdb.crawled_docs.update_one(
                {"url": url},
                {"$set": {"title": f["name"], "content": content[:5000],
                           "source": f"github:{owner}/{repo}",
                           "crawled_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
            log.append(f"  ✓ {f['name']} → {len(chunks)}청크 (Qdrant {stored}건)")
            total += len(chunks)

    return total


async def crawl_url(
    url: str, mdb, ollama: OllamaClient, log: list[str],
) -> int:
    """임의 URL 크롤링."""
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; FinAgent/1.0)"},
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            log.append(f"[ERROR] {url}: {e}")
            return 0

    text = _extract_text(html)
    if len(text) < 100:
        log.append(f"[SKIP] 텍스트 추출 실패: {url}")
        return 0

    chunks = _chunk_text(text)
    title = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    title_text = title.group(1).strip() if title else url

    meta = {"url": url, "title": title_text, "source": "web"}
    stored = await _store_qdrant(chunks, meta, ollama)
    await mdb.crawled_docs.update_one(
        {"url": url},
        {"$set": {"title": title_text, "content": text[:5000],
                   "source": "web", "crawled_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    log.append(f"✓ {title_text[:50]} → {len(chunks)}청크 (Qdrant {stored}건)")
    return len(chunks)


async def crawl_naver_stock(
    code: str, mdb, ollama: OllamaClient, log: list[str],
) -> int:
    """네이버 금융 종목 메인 페이지 전용 크롤링."""
    stock_code = re.sub(r"[^0-9]", "", code or "")
    if len(stock_code) != 6:
        log.append("[ERROR] 종목코드는 6자리 숫자여야 합니다. (예: 005930)")
        return 0

    url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FinAgent/1.0)",
            "Referer": "https://finance.naver.com/",
        },
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            log.append(f"[ERROR] 네이버 금융 요청 실패: {e}")
            return 0

    soup = BeautifulSoup(html, "html.parser")
    name = ""
    h2 = soup.select_one("div.wrap_company h2 a")
    if h2:
        name = h2.get_text(strip=True)

    current = ""
    no_today = soup.select_one("p.no_today span.blind")
    if no_today:
        current = no_today.get_text(strip=True)

    news_items = []
    for a in soup.select("div.section.new_bbs ul li a")[:10]:
        t = a.get("title") or a.get_text(strip=True)
        if t:
            news_items.append(t.strip())

    if not name and not news_items:
        log.append("[ERROR] 네이버 페이지 파싱 실패")
        return 0

    text = f"종목명: {name}\n현재가: {current}\n"
    if news_items:
        text += "주요 뉴스:\n" + "\n".join(f"- {n}" for n in news_items)

    chunks = _chunk_text(text)
    meta = {
        "url": url,
        "title": f"{name or stock_code} 네이버금융",
        "source": f"naver:stock:{stock_code}",
        "stock_code": stock_code,
    }
    stored = await _store_qdrant(chunks, meta, ollama)
    await mdb.crawled_docs.update_one(
        {"url": url},
        {"$set": {
            "title": meta["title"],
            "content": text[:5000],
            "source": meta["source"],
            "crawled_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    log.append(f"✓ {meta['title']} → {len(chunks)}청크 (Qdrant {stored}건)")
    return len(chunks)


async def run_auto_crawl(mdb, ollama: OllamaClient, log: list[str]) -> dict:
    """자동 크롤링 실행."""
    total_chunks = 0
    for target in CRAWL_TARGETS:
        log.append(f"\n[크롤링] {target['name']}")
        if target["type"] == "github_docs":
            n = await crawl_github_docs(
                target["owner"], target["repo"], target["branch"], target["path"],
                mdb, ollama, log,
            )
            total_chunks += n

    return {"total_chunks": total_chunks, "sources": len(CRAWL_TARGETS)}


async def qdrant_search(query: str, ollama: OllamaClient, top_k: int = 5) -> list[dict]:
    """Qdrant 벡터 검색."""
    try:
        from qdrant_client import AsyncQdrantClient
        qemb = await ollama.embed(settings.EMBED_MODEL, query)
        if not qemb:
            return []
        client = AsyncQdrantClient(url=settings.QDRANT_URL)
        results = await client.search(
            collection_name=settings.QDRANT_COLLECTION,
            query_vector=qemb,
            limit=top_k,
        )
        await client.close()
        return [{"text": r.payload.get("text", ""), "url": r.payload.get("url", ""),
                 "title": r.payload.get("title", ""), "score": r.score}
                for r in results]
    except Exception:
        return []
