"""LangChain LCEL 기반 RAG 파이프라인.

구성:
  OllamaEmbeddings  →  QdrantVectorStore  →  similarity_search
  LCEL 체인: retriever | format_docs | prompt | llm | StrOutputParser
"""
from __future__ import annotations
from typing import Any

from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_ollama import ChatOllama
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, VectorParams

from app.config import settings


# ── 내부 팩토리 ───────────────────────────────────────────────────────────────

def _make_embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.EMBED_MODEL,
    )


async def _get_or_create_collection(client: AsyncQdrantClient, collection: str) -> None:
    """Qdrant 컬렉션이 없으면 nomic-embed-text 기준 dim=768로 생성한다."""
    try:
        await client.get_collection(collection)
    except Exception:
        await client.create_collection(
            collection,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )


# ── 공개 함수 ─────────────────────────────────────────────────────────────────

async def rag_search(
    query:      str,
    top_k:      int  = 5,
    collection: str | None = None,
    filter_source: str | None = None,
) -> list[dict]:
    """
    LangChain QdrantVectorStore를 통해 유사 문서를 검색한다.

    Args:
        query:         검색 쿼리
        top_k:         반환할 최대 문서 수
        collection:    Qdrant 컬렉션명 (None이면 settings.QDRANT_COLLECTION 사용)
        filter_source: 특정 source만 필터링 (예: "upload", "github:...")

    Returns:
        [{"text": ..., "url": ..., "title": ..., "source": ..., "score": ...}, ...]
    """
    coll = collection or settings.QDRANT_COLLECTION
    try:
        client = AsyncQdrantClient(url=settings.QDRANT_URL)
        await _get_or_create_collection(client, coll)

        store = QdrantVectorStore(
            client=client,
            collection_name=coll,
            embedding=_make_embeddings(),
        )

        qdrant_filter = None
        if filter_source:
            from qdrant_client.http.models import Filter, FieldCondition, MatchValue
            qdrant_filter = Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=filter_source))]
            )

        results = await store.asimilarity_search_with_score(
            query, k=top_k, filter=qdrant_filter
        )
        await client.close()

        return [
            {
                "text":   doc.page_content,
                "url":    doc.metadata.get("url", ""),
                "title":  doc.metadata.get("title", ""),
                "source": doc.metadata.get("source", ""),
                "score":  float(score),
            }
            for doc, score in results
        ]
    except Exception:
        return []


async def store_chunks(
    chunks:     list[str],
    metadata:   dict,
    collection: str | None = None,
) -> int:
    """
    텍스트 청크 목록을 OllamaEmbeddings로 임베딩하여 Qdrant에 저장한다.

    Returns:
        실제 저장된 청크 수
    """
    if not chunks:
        return 0

    coll = collection or settings.QDRANT_COLLECTION
    try:
        client = AsyncQdrantClient(url=settings.QDRANT_URL)
        await _get_or_create_collection(client, coll)

        store = QdrantVectorStore(
            client=client,
            collection_name=coll,
            embedding=_make_embeddings(),
        )
        docs = [Document(page_content=chunk, metadata=metadata) for chunk in chunks]
        await store.aadd_documents(docs)
        await client.close()
        return len(docs)
    except Exception:
        return 0


def build_rag_chain(collection: str | None = None):
    """
    LCEL 기반 RAG 체인을 반환한다.

    사용 예:
        chain = build_rag_chain()
        answer = await chain.ainvoke({"question": "..."})
    """
    coll = collection or settings.QDRANT_COLLECTION

    # 동기 Qdrant 클라이언트 (LCEL retriever는 sync 인터페이스 사용)
    from qdrant_client import QdrantClient
    sync_client = QdrantClient(url=settings.QDRANT_URL)

    vector_store = QdrantVectorStore(
        client=sync_client,
        collection_name=coll,
        embedding=_make_embeddings(),
    )
    retriever = vector_store.as_retriever(search_kwargs={"k": settings.TOP_K})

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "너는 금융 AI 어시스턴트다. 아래 참고 문서를 바탕으로 질문에 한국어로 답하라.\n\n"
            "[참고 문서]\n{context}",
        ),
        ("human", "{question}"),
    ])

    llm = ChatOllama(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.LLM_MODEL,
        temperature=0.2,
        num_predict=2048,
    )

    def format_docs(docs: list[Document]) -> str:
        return "\n\n".join(
            f"[{d.metadata.get('title', '문서')}]\n{d.page_content}" for d in docs
        )

    chain = (
        {"context": retriever | RunnableLambda(format_docs), "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    return chain


async def delete_chunks_by_source(source: str, collection: str | None = None) -> int:
    """
    특정 source 메타데이터를 가진 모든 벡터를 Qdrant에서 삭제한다.

    Returns:
        삭제 요청이 성공하면 1, 실패하면 0
    """
    coll = collection or settings.QDRANT_COLLECTION
    try:
        from qdrant_client.http.models import Filter, FieldCondition, MatchValue
        client = AsyncQdrantClient(url=settings.QDRANT_URL)
        await client.delete(
            collection_name=coll,
            points_selector=Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=source))]
            ),
        )
        await client.close()
        return 1
    except Exception:
        return 0
