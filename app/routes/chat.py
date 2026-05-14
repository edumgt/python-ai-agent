from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
from app.database.mongo import get_mdb
from app.lib.session import get_current_user
from app.lib.ollama import get_ollama
from app.services.langgraph_agent import run_agent   # LangGraph 기반 에이전트
from app.services.rag_pipeline import rag_search     # LangChain LCEL RAG
from app.config import settings

router = APIRouter(prefix="/api")


class ChatBody(BaseModel):
    question: str
    history:  list[dict] = []
    use_rag:  bool = True


@router.post("/chat")
async def chat(
    body: ChatBody,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    ollama = get_ollama()

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
            body.question, body.history,
            rag_context=rag_context,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                503,
                f"LLM 모델({settings.LLM_MODEL})을 찾을 수 없습니다. "
                f"(ollama pull {settings.LLM_MODEL})"
            )
        raise HTTPException(503, f"Ollama 오류: {e.response.status_code}")
    except httpx.ConnectError:
        raise HTTPException(503, f"Ollama 서버({settings.OLLAMA_BASE_URL})에 연결할 수 없습니다.")
    except httpx.TimeoutException:
        raise HTTPException(504, "LLM 응답 시간이 초과되었습니다.")
    except Exception as e:
        raise HTTPException(500, f"에이전트 오류: {str(e)[:200]}")

    # MongoDB에 채팅 기록 저장
    try:
        await mdb.chats.insert_one({
            "user_id":    user["id"],
            "client_id":  user["client_id"],
            "question":   body.question,
            "answer":     result["answer"],
            "steps":      result.get("steps", []),
            "citations":  result.get("citations", []),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass

    return result
