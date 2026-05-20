"""JWT 기반 액세스/리프레시 토큰 발급 및 검증.

인증 흐름:
1. POST /api/auth/token → access_token(단기) + refresh_token(장기) 반환
2. Authorization: Bearer <access_token> 헤더로 API 호출
3. 액세스 토큰 만료 시 POST /api/auth/token/refresh → 새 액세스 토큰 발급
4. 로그아웃 시 POST /api/auth/token/revoke → Redis 블랙리스트 등록

세션 쿠키 방식과 병행 지원 – get_current_user_any()로 두 방식 모두 허용.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings
from app.lib.redis_cache import jwt_blacklist_cache

_bearer = HTTPBearer(auto_error=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unix(dt: datetime) -> int:
    return int(dt.timestamp())


# ── 토큰 생성 ──────────────────────────────────────────────────────────────────

def create_access_token(payload: dict) -> str:
    """단기 액세스 토큰 (기본 15분)."""
    data = {
        **payload,
        "type": "access",
        "iat": _unix(_now()),
        "exp": _unix(_now() + timedelta(seconds=settings.JWT_ACCESS_TTL)),
    }
    return jwt.encode(data, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(payload: dict) -> str:
    """장기 리프레시 토큰 (기본 7일)."""
    data = {
        **payload,
        "type": "refresh",
        "iat": _unix(_now()),
        "exp": _unix(_now() + timedelta(seconds=settings.JWT_REFRESH_TTL)),
    }
    return jwt.encode(data, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_token_pair(user_payload: dict) -> dict:
    """액세스 + 리프레시 토큰 쌍 반환."""
    return {
        "access_token": create_access_token(user_payload),
        "refresh_token": create_refresh_token(user_payload),
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_TTL,
    }


# ── 토큰 검증 ──────────────────────────────────────────────────────────────────

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"유효하지 않은 토큰: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _bl_key(token: str) -> str:
    """블랙리스트 키: 토큰의 앞 32자 사용 (메모리 절약)."""
    return token[:32]


async def revoke_token(token: str) -> None:
    """토큰을 블랙리스트에 추가합니다."""
    try:
        payload = decode_token(token)
    except HTTPException:
        return
    exp = payload.get("exp", 0)
    ttl = max(0, exp - _unix(_now()))
    if ttl > 0:
        await jwt_blacklist_cache.set(_bl_key(token), "revoked", ttl=ttl)


async def is_revoked(token: str) -> bool:
    return await jwt_blacklist_cache.exists(_bl_key(token))


# ── FastAPI Depends ────────────────────────────────────────────────────────────

async def get_current_user_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """Bearer 토큰으로 현재 사용자를 반환합니다."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer 토큰이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    if await is_revoked(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="만료(폐기)된 토큰입니다.",
        )
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="액세스 토큰이 아닙니다.",
        )
    return payload


async def get_current_user_any(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    fin_session: Optional[str] = Cookie(default=None),
) -> dict:
    """Bearer JWT 또는 세션 쿠키 중 하나로 인증합니다.

    JWT가 있으면 우선 처리, 없으면 쿠키 세션으로 폴백합니다.
    """
    if credentials and credentials.credentials:
        return await get_current_user_jwt(credentials)

    if fin_session:
        from app.lib.session import get_session  # 순환 임포트 방지
        user = await get_session(fin_session)
        if user:
            return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="로그인이 필요합니다.",
    )


def require_roles(*roles: str):
    """특정 역할을 가진 사용자만 허용하는 Depends 팩토리.

    사용 예::

        @router.get("/admin")
        async def admin_only(user=Depends(require_roles("admin"))):
            ...
    """
    async def _check(user: dict = Depends(get_current_user_any)) -> dict:
        user_roles = user.get("roles", [])
        if not any(r in user_roles for r in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"권한이 없습니다. 필요 역할: {list(roles)}",
            )
        return user
    return _check
