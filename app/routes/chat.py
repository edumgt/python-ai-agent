"""채팅 API – 대화 스레드 + Redis 사용자 상태 연동.

변경 사항:
- conversation_id 필드 추가: 없으면 자동으로 새 스레드 생성
- Redis user_state 에 활성 conversation_id 기록
- 메시지 저장 시 conversation_id 포함
- 대화 스레드 updated_at / message_count 갱신
- JWT Bearer 또는 쿠키 세션 모두 허용 (get_current_user_any)
"""
from datetime import datetime, timezone
from typing import Optional

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database.mongo import get_mdb
from app.lib.jwt_auth import get_current_user_any
from app.lib.ollama import get_ollama
from app.lib.user_state import get_active_conversation, set_active_conversation
from app.services.langgraph_agent import run_agent
from app.services.rag_pipeline import rag_search

router = APIRouter(prefix="/api")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatBody(BaseModel):
    question: str
    history: list[dict] = []
    use_rag: bool = True
    conversation_id: Optional[str] = None  # 없으면 자동 생성


async def _get_or_create_conversation(mdb, user_id: str, cid: Optional[str]) -> str:
    """conversation_id 가 주어지면 검증, 없으면 Redis 활성 스레드 또는 신규 생성."""
    if cid:
        try:
            doc = await mdb.conversations.find_one(
                {"_id": ObjectId(cid), "user_id": user_id}
            )
        except Exception:
            doc = None
        if not doc:
            raise HTTPException(404, "대화 스레드를 찾을 수 없습니다.")
        return cid

    # Redis 에서 활성 스레드 확인
    active = await get_active_conversation(user_id)
    if active:
        exists = await mdb.conversations.find_one(
            {"_id": ObjectId(active), "user_id": user_id}
        )
        if exists:
            return active

    # 새 스레드 생성
    result = await mdb.conversations.insert_one({
        "user_id": user_id,
        "title": f"대화 {_now()[:10]}",
        "message_count": 0,
        "created_at": _now(),
        "updated_at": _now(),
    })
    new_cid = str(result.inserted_id)
    await set_active_conversation(user_id, new_cid)
    return new_cid


async def _build_history_from_db(mdb, conversation_id: str, user_id: str, limit: int = 10) -> list[dict]:
    """Redis/MongoDB 에서 최근 대화 이력을 LangGraph 포맷으로 변환합니다."""
    cursor = (
        mdb.chats
        .find({"conversation_id": conversation_id, "user_id": user_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    docs = []
    async for doc in cursor:
        docs.append(doc)
    docs.reverse()

    history = []
    for doc in docs:
        history.append({"role": "user", "content": doc["question"]})
        history.append({"role": "assistant", "content": doc["answer"]})
    return history


@router.post("/chat")
async def chat(
    body: ChatBody,
    user=Depends(get_current_user_any),
    mdb=Depends(get_mdb),
):
    user_id = user["id"]
    ollama = get_ollama()

    # 대화 스레드 확보
    conversation_id = await _get_or_create_conversation(mdb, user_id, body.conversation_id)

    # 클라이언트가 history 를 보내지 않았으면 DB 에서 최근 이력 로드
    history = body.history
    if not history:
        history = await _build_history_from_db(mdb, conversation_id, user_id, limit=10)

    # LangChain RAG 검색 (Qdrant)
    rag_context = ""
    if body.use_rag:
        try:
            docs = await rag_search(body.question, top_k=settings.TOP_K)
            if docs:
                rag_context = "\n\n".join(
                    f"[{d['title']}] {d['text'][:500]}" for d in docs
                )
        except Exception:
            pass

    # LangGraph 에이전트 실행
    try:
        result = await run_agent(
            mdb, ollama, settings.LLM_MODEL,
            body.question, history,
            rag_context=rag_context,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                503,
                f"LLM 모델({settings.LLM_MODEL})을 찾을 수 없습니다. "
                f"(ollama pull {settings.LLM_MODEL})",
            )
        raise HTTPException(503, f"Ollama 오류: {e.response.status_code}")
    except httpx.ConnectError:
        raise HTTPException(503, f"Ollama 서버({settings.OLLAMA_BASE_URL})에 연결할 수 없습니다.")
    except httpx.TimeoutException:
        raise HTTPException(504, "LLM 응답 시간이 초과되었습니다.")
    except Exception as e:
        raise HTTPException(500, f"에이전트 오류: {str(e)[:200]}")

    # MongoDB – 메시지 저장 (conversation_id 포함)
    try:
        await mdb.chats.insert_one({
            "user_id": user_id,
            "client_id": user.get("client_id", ""),
            "conversation_id": conversation_id,
            "question": body.question,
            "answer": result["answer"],
            "steps": result.get("steps", []),
            "citations": result.get("citations", []),
            "created_at": _now(),
        })
        # 스레드 통계 갱신
        await mdb.conversations.update_one(
            {"_id": ObjectId(conversation_id)},
            {
                "$inc": {"message_count": 1},
                "$set": {"updated_at": _now()},
            },
        )
    except Exception:
        pass

    # Redis 사용자 상태 갱신 (활성 대화 + 마지막 활동 시각)
    try:
        await set_active_conversation(user_id, conversation_id)
    except Exception:
        pass

    return {**result, "conversation_id": conversation_id}
