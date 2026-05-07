import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
import aiosqlite
from app.database.sqlite import get_db
from app.lib.session import get_current_user
from app.lib.ollama import get_ollama
from app.services.agent import run_agent
from app.services.crawl import qdrant_search
from app.config import settings

router = APIRouter(prefix="/api")


class ChatBody(BaseModel):
    question: str
    history: list[dict] = []
    use_rag: bool = True


@router.post("/chat")
async def chat(
    body: ChatBody,
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    ollama = get_ollama()

    # Qdrant RAG 검색
    rag_context = ""
    if body.use_rag:
        try:
            docs = await qdrant_search(body.question, ollama, top_k=3)
            if docs:
                rag_context = "\n\n".join(
                    f"[{d['title']}] {d['text'][:500]}" for d in docs
                )
        except Exception:
            pass  # RAG 실패 시 무시하고 계속

    # 에이전트 실행
    try:
        result = await run_agent(
            db, ollama, settings.LLM_MODEL, body.question, body.history,
            rag_context=rag_context,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                503,
                f"LLM 모델({settings.LLM_MODEL})을 찾을 수 없습니다. "
                f"Ollama에서 해당 모델이 설치되어 있는지 확인해 주세요. "
                f"(ollama pull {settings.LLM_MODEL})"
            )
        raise HTTPException(503, f"Ollama 오류: {e.response.status_code}")
    except httpx.ConnectError:
        raise HTTPException(503, f"Ollama 서버({settings.OLLAMA_BASE_URL})에 연결할 수 없습니다.")
    except httpx.TimeoutException:
        raise HTTPException(504, "LLM 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.")
    except Exception as e:
        raise HTTPException(500, f"에이전트 오류: {str(e)[:200]}")

    # 채팅 기록 저장
    try:
        await db.execute(
            "INSERT INTO chats (mongo_user_id, client_id, question, answer, steps_json, citations_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user["id"], user["client_id"], body.question, result["answer"],
             json.dumps(result.get("steps", []), ensure_ascii=False),
             json.dumps(result.get("citations", []), ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    except Exception:
        pass  # DB 저장 실패해도 응답은 반환

    return result
