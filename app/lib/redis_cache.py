"""Redis 기반 Key-Value 캐시 레이어.

네임스페이스별로 격리된 캐시 인스턴스를 제공합니다.
연결 관리(connect/close)도 이 모듈에서 담당합니다.
"""
import json
from typing import Any, Optional

import redis.asyncio as aioredis

from app.config import settings

_redis: aioredis.Redis | None = None


# ── 연결 관리 ──────────────────────────────────────────────────────────────────

async def connect_redis() -> None:
    global _redis
    _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await _redis.ping()


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis가 초기화되지 않았습니다.")
    return _redis


# ── 네임스페이스 캐시 클래스 ───────────────────────────────────────────────────

class RedisCache:
    """네임스페이스로 격리된 Redis K-V 캐시.

    키 형식: ``{namespace}:{key}``

    사용 예::

        market = RedisCache("market")
        await market.set("005930.KS", {"price": 73000}, ttl=3600)
        data = await market.get("005930.KS")
    """

    def __init__(self, namespace: str):
        self.ns = namespace

    def _key(self, key: str) -> str:
        return f"{self.ns}:{key}"

    async def get(self, key: str) -> Optional[Any]:
        r = get_redis()
        raw = await r.get(self._key(key))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        r = get_redis()
        serialized = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        if ttl:
            await r.setex(self._key(key), ttl, serialized)
        else:
            await r.set(self._key(key), serialized)

    async def delete(self, key: str) -> None:
        await get_redis().delete(self._key(key))

    async def exists(self, key: str) -> bool:
        return bool(await get_redis().exists(self._key(key)))

    async def incr(self, key: str, ttl: Optional[int] = None) -> int:
        """카운터 증가. TTL은 첫 증가 시에만 설정됩니다 (레이트 리밋에 활용)."""
        r = get_redis()
        val = await r.incr(self._key(key))
        if ttl and val == 1:
            await r.expire(self._key(key), ttl)
        return val

    async def expire(self, key: str, ttl: int) -> None:
        await get_redis().expire(self._key(key), ttl)

    async def ttl(self, key: str) -> int:
        return await get_redis().ttl(self._key(key))

    async def keys(self, pattern: str = "*") -> list[str]:
        """네임스페이스 내 키 목록 반환 (prefix 제거)."""
        r = get_redis()
        raw_keys = await r.keys(f"{self.ns}:{pattern}")
        prefix = f"{self.ns}:"
        return [k[len(prefix):] for k in raw_keys]

    async def mget(self, keys: list[str]) -> list[Optional[Any]]:
        """여러 키를 한 번에 조회합니다."""
        r = get_redis()
        raw_list = await r.mget([self._key(k) for k in keys])
        result = []
        for raw in raw_list:
            if raw is None:
                result.append(None)
            else:
                try:
                    result.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    result.append(raw)
        return result

    async def set_hash(self, key: str, mapping: dict, ttl: Optional[int] = None) -> None:
        """Redis Hash 구조로 저장합니다."""
        r = get_redis()
        serialized = {k: json.dumps(v, ensure_ascii=False) for k, v in mapping.items()}
        await r.hset(self._key(key), mapping=serialized)
        if ttl:
            await r.expire(self._key(key), ttl)

    async def get_hash(self, key: str) -> dict:
        """Redis Hash 전체를 딕셔너리로 반환합니다."""
        r = get_redis()
        raw = await r.hgetall(self._key(key))
        result = {}
        for k, v in raw.items():
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                result[k] = v
        return result

    async def get_hash_field(self, key: str, field: str) -> Optional[Any]:
        r = get_redis()
        raw = await r.hget(self._key(key), field)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw


# ── 사전 정의된 캐시 인스턴스 (네임스페이스 규약) ────────────────────────────────
#
#  fin_session:{sid}   – 사용자 세션 (session.py에서 관리)
#  market:{key}        – 시세/캔들 캐시
#  user_state:{uid}    – 사용자 활성 상태 (user_state.py에서 관리)
#  rl:{uid}:{route}    – 레이트 리밋 카운터
#  jwt_bl:{token_prefix} – JWT 블랙리스트
#
session_cache = RedisCache("fin_session")
market_cache = RedisCache("market")
user_state_cache = RedisCache("user_state")
rate_limit_cache = RedisCache("rl")
jwt_blacklist_cache = RedisCache("jwt_bl")
