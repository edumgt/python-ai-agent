"""문서 업로드 → VLM 파싱 → Qdrant RAG 인제스트 API.

엔드포인트:
  POST   /api/documents/upload     - 파일 업로드 및 RAG 인제스트
  GET    /api/documents/list       - 업로드된 문서 목록
  DELETE /api/documents/{doc_id}   - 문서 삭제 (메타 + 벡터)
  POST   /api/documents/search     - 문서 전용 RAG 검색
"""
from __future__ import annotations
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.database.mongo import get_mdb
from app.lib.session import get_current_user
from app.lib.ollama import get_ollama
from app.services.doc_parser import parse_document, SUPPORTED_EXTENSIONS
from app.services.rag_pipeline import store_chunks, rag_search, delete_chunks_by_source
from app.config import settings

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


# ── 업로드 ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    """
    문서 파일을 업로드하여 VLM으로 파싱한 뒤 Qdrant RAG에 인제스트한다.

    - 지원 형식: pptx, ppt, docx, doc, xlsx, xls, pdf, md, txt
    - 이미지 슬라이드/페이지는 Ollama VLM(llava)이 자동으로 설명 텍스트 생성
    - 청크는 메인 Qdrant 컬렉션에 저장되며 채팅 RAG 검색에 즉시 반영됨
    """
    from pathlib import Path
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            400,
            f"지원하지 않는 파일 형식입니다. 허용: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "파일 크기가 50 MB를 초과합니다.")
    if len(content) == 0:
        raise HTTPException(400, "빈 파일입니다.")

    ollama = get_ollama()

    try:
        chunks = await parse_document(file.filename, content, ollama)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(422, f"문서 파싱 실패: {str(e)[:200]}")

    if not chunks:
        raise HTTPException(422, "문서에서 텍스트를 추출할 수 없습니다.")

    source_key = f"upload:{user['id']}:{file.filename}"
    meta = {
        "url":      f"upload://{file.filename}",
        "title":    file.filename,
        "source":   source_key,
        "uploader": user["email"],
    }

    stored = await store_chunks(chunks, meta, collection=settings.DOCUMENT_COLLECTION)

    doc_record = {
        "filename":   file.filename,
        "uploader":   user["email"],
        "user_id":    user["id"],
        "source_key": source_key,
        "chunks":     stored,
        "file_size":  len(content),
        "ext":        ext,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await mdb.uploaded_docs.insert_one(doc_record)

    return {
        "ok":       True,
        "doc_id":   str(result.inserted_id),
        "filename": file.filename,
        "chunks":   stored,
        "message":  f"'{file.filename}' 업로드 완료 ({stored}청크 저장)",
    }


# ── 목록 조회 ─────────────────────────────────────────────────────────────────

@router.get("/list")
async def list_documents(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    """현재 사용자가 업로드한 문서 목록을 반환한다."""
    cursor = (
        mdb.uploaded_docs
        .find(
            {"user_id": user["id"]},
            {"_id": 1, "filename": 1, "chunks": 1, "file_size": 1, "ext": 1, "created_at": 1},
        )
        .sort("created_at", -1)
        .limit(100)
    )

    items = []
    async for doc in cursor:
        doc["doc_id"] = str(doc.pop("_id"))
        items.append(doc)

    return {"items": items}


# ── 삭제 ─────────────────────────────────────────────────────────────────────

@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    """
    문서의 MongoDB 메타데이터와 Qdrant 벡터를 모두 삭제한다.
    본인이 업로드한 문서만 삭제할 수 있다.
    """
    try:
        oid = ObjectId(doc_id)
    except Exception:
        raise HTTPException(400, "유효하지 않은 doc_id입니다.")

    doc = await mdb.uploaded_docs.find_one({"_id": oid, "user_id": user["id"]})
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없거나 삭제 권한이 없습니다.")

    # Qdrant 벡터 삭제 (source_key로 필터링)
    source_key = doc.get("source_key", "")
    if source_key:
        await delete_chunks_by_source(source_key, collection=settings.DOCUMENT_COLLECTION)

    # MongoDB 메타 삭제
    await mdb.uploaded_docs.delete_one({"_id": oid})

    return {"ok": True, "message": f"'{doc['filename']}' 삭제 완료"}


# ── 문서 전용 RAG 검색 ────────────────────────────────────────────────────────

class DocSearchBody(BaseModel):
    query:  str
    top_k:  int  = 5
    source: str | None = None   # 특정 문서 source_key로 좁혀서 검색


@router.post("/search")
async def search_documents(
    body: DocSearchBody,
    user=Depends(get_current_user),
):
    """업로드된 문서 컬렉션에서 쿼리와 유사한 청크를 검색한다."""
    hits = await rag_search(
        body.query,
        top_k=body.top_k,
        collection=settings.DOCUMENT_COLLECTION,
        filter_source=body.source,
    )
    return {"ok": True, "hits": hits}
