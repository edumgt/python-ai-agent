"""Redis 기반 세션 관리.

연결 관리는 app/lib/redis_cache.py 의 connect_redis/close_redis 에서 담당합니다.
이 모듈은 하위 호환을 위해 connect_redis/close_redis 를 재내보냅니다.

세션 키 형식: fin_session:{sid}
슬라이딩 만료: 요청마다 TTL을 갱신해 마지막 활동으로부터 SESSION_TTL 후 만료됩니다.

멀티 디바이스: 동일 user_id 로 여러 세션(sid)이 공존할 수 있습니다.
  사용자별 세션 목록 키: fin_session_list:{user_id} → Set of sid
"""
import uuid
from typing import Optional

from fastapi import Cookie, HTTPException, status

from app.config import settings
from app.lib.redis_cache import (
    connect_redis,   # 재내보내기 – main.py 가 여기서 import
    close_redis,     # 재내보내기
    get_redis,
    session_cache,
)

__all__ = [
    "connect_redis",
    "close_redis",
    "create_session",
    "get_session",
    "refresh_session",
    "delete_session",
    "delete_all_user_sessions",
    "get_current_user",
    "get_optional_user",
]


def _list_key(user_id: str) -> str:
    """사용자의 모든 세션 ID를 보관하는 Redis Set 키."""
    return f"fin_session_list:{user_id}"


# ── 세션 CRUD ──────────────────────────────────────────────────────────────────

async def create_session(user_data: dict) -> str:
    """새 세션을 생성하고 세션 ID(sid)를 반환합니다."""
    sid = str(uuid.uuid4())
    await session_cache.set(sid, user_data, ttl=settings.SESSION_TTL)

    # 사용자별 세션 목록에 등록
    r = get_redis()
    list_key = _list_key(user_data["id"])
    await r.sadd(list_key, sid)
    await r.expire(list_key, settings.SESSION_TTL)

    return sid


async def get_session(sid: str) -> Optional[dict]:
    """세션 ID로 사용자 데이터를 조회합니다."""
    return await session_cache.get(sid)


async def refresh_session(sid: str) -> bool:
    """슬라이딩 만료: 세션 TTL을 현재 시각 기준으로 재설정합니다.

    세션이 존재하지 않으면 False, 갱신 성공 시 True.
    """
    data = await session_cache.get(sid)
    if data is None:
        return False
    # 데이터를 재기록해 TTL 초기화
    await session_cache.set(sid, data, ttl=settings.SESSION_TTL)
    r = get_redis()
    list_key = _list_key(data["id"])
    await r.expire(list_key, settings.SESSION_TTL)
    return True


async def delete_session(sid: str) -> None:
    """단일 세션을 삭제합니다."""
    data = await session_cache.get(sid)
    if data:
        r = get_redis()
        await r.srem(_list_key(data["id"]), sid)
    await session_cache.delete(sid)


async def delete_all_user_sessions(user_id: str) -> int:
    """사용자의 모든 세션을 삭제합니다 (강제 로그아웃)."""
    r = get_redis()
    list_key = _list_key(user_id)
    sids = await r.smembers(list_key)
    for sid in sids:
        await session_cache.delete(sid)
    await r.delete(list_key)
    return len(sids)


async def list_user_sessions(user_id: str) -> list[str]:
    """사용자의 활성 세션 ID 목록을 반환합니다."""
    r = get_redis()
    return list(await r.smembers(_list_key(user_id)))


# ── FastAPI Depends ────────────────────────────────────────────────────────────

async def get_current_user(
    fin_session: Optional[str] = Cookie(default=None),
) -> dict:
    """세션 쿠키로 현재 사용자를 반환합니다 (슬라이딩 만료 포함)."""
    if not fin_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )
    user = await get_session(fin_session)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="세션이 만료되었습니다.",
        )
    # 슬라이딩 만료: 유효한 요청마다 TTL 갱신
    await refresh_session(fin_session)
    return user


async def get_optional_user(
    fin_session: Optional[str] = Cookie(default=None),
) -> Optional[dict]:
    """인증이 선택적인 엔드포인트용 – 미인증 시 None 반환."""
    if not fin_session:
        return None
    return await get_session(fin_session)
