"""Redis 기반 사용자 실시간 상태 관리.

세션 쿠키/JWT와 별도로, 사용자가 현재 어떤 대화를 진행 중인지,
마지막으로 접속한 시각, 온라인 여부 등 휘발성 상태를 Redis에 저장합니다.

저장 구조:
  user_state:{user_id} → JSON 객체
    {
      "active_conversation_id": "...",
      "online": true/false,
      "last_seen": "ISO datetime",
      "preferences": { ... },
      "updated_at": "ISO datetime"
    }
"""
from datetime import datetime, timezone
from typing import Any, Optional

from app.lib.redis_cache import user_state_cache
from app.config import settings

_TTL = settings.SESSION_TTL  # 세션 TTL과 동일하게 유지


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_user_state(user_id: str) -> dict:
    """사용자 상태 전체를 반환합니다. 없으면 빈 dict."""
    return await user_state_cache.get(user_id) or {}


async def update_user_state(user_id: str, updates: dict) -> dict:
    """상태를 부분 업데이트합니다 (기존 필드 보존)."""
    state = await get_user_state(user_id)
    state.update(updates)
    state["updated_at"] = _now_iso()
    await user_state_cache.set(user_id, state, ttl=_TTL)
    return state


# ── 대화 컨텍스트 ──────────────────────────────────────────────────────────────

async def set_active_conversation(user_id: str, conversation_id: str) -> None:
    """활성 대화 스레드 ID를 Redis에 기록합니다."""
    await update_user_state(user_id, {"active_conversation_id": conversation_id})


async def get_active_conversation(user_id: str) -> Optional[str]:
    """현재 활성 대화 ID를 반환합니다."""
    state = await get_user_state(user_id)
    return state.get("active_conversation_id")


async def clear_active_conversation(user_id: str) -> None:
    state = await get_user_state(user_id)
    state.pop("active_conversation_id", None)
    state["updated_at"] = _now_iso()
    await user_state_cache.set(user_id, state, ttl=_TTL)


# ── 온라인 프레즌스 ────────────────────────────────────────────────────────────

async def mark_online(user_id: str) -> None:
    await update_user_state(user_id, {
        "online": True,
        "last_seen": _now_iso(),
    })


async def mark_offline(user_id: str) -> None:
    await update_user_state(user_id, {
        "online": False,
        "last_seen": _now_iso(),
    })


async def get_online_status(user_id: str) -> dict:
    state = await get_user_state(user_id)
    return {
        "online": state.get("online", False),
        "last_seen": state.get("last_seen"),
    }


# ── 사용자 환경설정 (휘발성) ───────────────────────────────────────────────────

async def set_preference(user_id: str, key: str, value: Any) -> None:
    """사용자 환경설정 항목 1개를 저장합니다."""
    state = await get_user_state(user_id)
    prefs = state.get("preferences", {})
    prefs[key] = value
    await update_user_state(user_id, {"preferences": prefs})


async def get_preference(user_id: str, key: str, default: Any = None) -> Any:
    state = await get_user_state(user_id)
    return state.get("preferences", {}).get(key, default)


# ── 전체 상태 초기화 ──────────────────────────────────────────────────────────

async def clear_user_state(user_id: str) -> None:
    """로그아웃 시 사용자 상태를 삭제합니다."""
    await user_state_cache.delete(user_id)
