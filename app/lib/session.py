import json
import uuid
from typing import Optional
from fastapi import Cookie, HTTPException, status
import redis.asyncio as aioredis
from app.config import settings

_redis: aioredis.Redis | None = None


async def connect_redis() -> None:
    global _redis
    _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await _redis.ping()


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


def _key(sid: str) -> str:
    return f"fin_session:{sid}"


async def create_session(user_data: dict) -> str:
    if _redis is None:
        raise HTTPException(status_code=503, detail="세션 서버(Redis) 연결 불가")
    sid = str(uuid.uuid4())
    await _redis.setex(_key(sid), settings.SESSION_TTL, json.dumps(user_data))
    return sid


async def get_session(sid: str) -> Optional[dict]:
    if _redis is None:
        return None
    raw = await _redis.get(_key(sid))
    if raw is None:
        return None
    return json.loads(raw)


async def delete_session(sid: str) -> None:
    if _redis is None:
        return
    await _redis.delete(_key(sid))


async def get_current_user(
    fin_session: Optional[str] = Cookie(default=None),
) -> dict:
    if not fin_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    user = await get_session(fin_session)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션이 만료되었습니다.")
    return user


async def get_optional_user(
    fin_session: Optional[str] = Cookie(default=None),
) -> Optional[dict]:
    if not fin_session:
        return None
    return await get_session(fin_session)
