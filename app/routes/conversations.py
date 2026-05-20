"""대화 이력 저장 및 상태 관리 API.

대화 스레드(Conversation) 개념을 도입하여 여러 세션에 걸친
대화 이력을 구조적으로 관리합니다.

MongoDB 컬렉션:
  conversations – 스레드 메타데이터
  chats         – 메시지 (conversation_id 필드로 스레드에 연결)

엔드포인트:
  POST   /api/conversations            – 새 대화 스레드 생성
  GET    /api/conversations            – 내 대화 목록
  GET    /api/conversations/{cid}      – 스레드 상세 + 메시지 목록
  PATCH  /api/conversations/{cid}      – 제목 수정
  DELETE /api/conversations/{cid}      – 스레드 + 메시지 삭제
  GET    /api/conversations/active     – 현재 활성 스레드
  POST   /api/conversations/{cid}/activate – 특정 스레드를 활성으로 설정
"""
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database.mongo import get_mdb
from app.lib.jwt_auth import get_current_user_any
from app.lib.user_state import (
    clear_active_conversation,
    get_active_conversation,
    get_user_state,
    set_active_conversation,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _oid(raw: str) -> ObjectId:
    try:
        return ObjectId(raw)
    except Exception:
        raise HTTPException(400, "유효하지 않은 대화 ID입니다.")


# ── 스키마 ─────────────────────────────────────────────────────────────────────

class ConversationCreate(BaseModel):
    title: Optional[str] = None


class ConversationPatch(BaseModel):
    title: str


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _serialize(doc: dict) -> dict:
    """MongoDB 문서의 ObjectId를 문자열로 변환합니다."""
    doc["id"] = str(doc.pop("_id"))
    return doc


async def _assert_owner(mdb, cid: str, user_id: str) -> dict:
    """스레드가 존재하고 현재 사용자 소유인지 확인합니다."""
    doc = await mdb.conversations.find_one({"_id": _oid(cid), "user_id": user_id})
    if not doc:
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    return doc


# ── 엔드포인트 ─────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_conversation(
    body: ConversationCreate,
    user=Depends(get_current_user_any),
    mdb=Depends(get_mdb),
):
    """새 대화 스레드를 생성하고 활성 스레드로 설정합니다."""
    title = body.title or f"대화 {_now()[:10]}"
    doc = {
        "user_id": user["id"],
        "title": title,
        "message_count": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = await mdb.conversations.insert_one(doc)
    cid = str(result.inserted_id)
    await set_active_conversation(user["id"], cid)
    return {"id": cid, "title": title, "active": True}


@router.get("")
async def list_conversations(
    limit: int = 20,
    offset: int = 0,
    user=Depends(get_current_user_any),
    mdb=Depends(get_mdb),
):
    """내 대화 스레드 목록을 최신 순으로 반환합니다."""
    active_cid = await get_active_conversation(user["id"])
    cursor = (
        mdb.conversations
        .find({"user_id": user["id"]})
        .sort("updated_at", -1)
        .skip(offset)
        .limit(limit)
    )
    items = []
    async for doc in cursor:
        item = _serialize(doc)
        item["active"] = item["id"] == active_cid
        items.append(item)
    total = await mdb.conversations.count_documents({"user_id": user["id"]})
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/active")
async def get_active(
    user=Depends(get_current_user_any),
    mdb=Depends(get_mdb),
):
    """현재 활성 대화 스레드를 반환합니다."""
    cid = await get_active_conversation(user["id"])
    if not cid:
        return {"active_conversation": None}
    doc = await mdb.conversations.find_one({"_id": _oid(cid), "user_id": user["id"]})
    if not doc:
        await clear_active_conversation(user["id"])
        return {"active_conversation": None}
    return {"active_conversation": _serialize(doc)}


@router.get("/{cid}")
async def get_conversation(
    cid: str,
    msg_limit: int = 50,
    msg_offset: int = 0,
    user=Depends(get_current_user_any),
    mdb=Depends(get_mdb),
):
    """스레드 메타데이터와 메시지 목록을 함께 반환합니다."""
    conv = await _assert_owner(mdb, cid, user["id"])

    # 메시지 조회 (오래된 순)
    cursor = (
        mdb.chats
        .find({"conversation_id": cid, "user_id": user["id"]})
        .sort("created_at", 1)
        .skip(msg_offset)
        .limit(msg_limit)
    )
    messages = []
    async for msg in cursor:
        msg["id"] = str(msg.pop("_id"))
        messages.append(msg)

    msg_total = await mdb.chats.count_documents(
        {"conversation_id": cid, "user_id": user["id"]}
    )

    result = _serialize(conv)
    result["messages"] = messages
    result["msg_total"] = msg_total
    return result


@router.patch("/{cid}")
async def update_conversation(
    cid: str,
    body: ConversationPatch,
    user=Depends(get_current_user_any),
    mdb=Depends(get_mdb),
):
    """대화 제목을 수정합니다."""
    await _assert_owner(mdb, cid, user["id"])
    await mdb.conversations.update_one(
        {"_id": _oid(cid)},
        {"$set": {"title": body.title, "updated_at": _now()}},
    )
    return {"ok": True, "title": body.title}


@router.delete("/{cid}", status_code=204)
async def delete_conversation(
    cid: str,
    user=Depends(get_current_user_any),
    mdb=Depends(get_mdb),
):
    """스레드와 해당 스레드의 모든 메시지를 삭제합니다."""
    await _assert_owner(mdb, cid, user["id"])
    await mdb.chats.delete_many({"conversation_id": cid})
    await mdb.conversations.delete_one({"_id": _oid(cid)})

    # 활성 스레드가 삭제된 경우 초기화
    if await get_active_conversation(user["id"]) == cid:
        await clear_active_conversation(user["id"])


@router.post("/{cid}/activate")
async def activate_conversation(
    cid: str,
    user=Depends(get_current_user_any),
    mdb=Depends(get_mdb),
):
    """특정 스레드를 현재 활성 대화로 설정합니다."""
    await _assert_owner(mdb, cid, user["id"])
    await set_active_conversation(user["id"], cid)
    return {"ok": True, "active_conversation_id": cid}


@router.get("/{cid}/messages")
async def list_messages(
    cid: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(get_current_user_any),
    mdb=Depends(get_mdb),
):
    """특정 스레드의 메시지 목록을 페이지네이션으로 반환합니다."""
    await _assert_owner(mdb, cid, user["id"])
    cursor = (
        mdb.chats
        .find({"conversation_id": cid, "user_id": user["id"]})
        .sort("created_at", 1)
        .skip(offset)
        .limit(limit)
    )
    messages = []
    async for msg in cursor:
        msg["id"] = str(msg.pop("_id"))
        messages.append(msg)
    total = await mdb.chats.count_documents(
        {"conversation_id": cid, "user_id": user["id"]}
    )
    return {"items": messages, "total": total}
