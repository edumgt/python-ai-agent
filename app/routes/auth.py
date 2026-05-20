"""인증 라우터.

세션 쿠키 방식(기존)과 JWT Bearer 방식(신규)을 모두 지원합니다.

쿠키 방식 (브라우저):
  POST /api/auth/register  – 회원가입 + 세션 쿠키 발급
  POST /api/auth/login     – 로그인 + 세션 쿠키 발급
  POST /api/auth/logout    – 로그아웃 + 쿠키 삭제

JWT 방식 (API 클라이언트 / 모바일):
  POST /api/auth/token         – 로그인 → access_token + refresh_token 반환
  POST /api/auth/token/refresh – refresh_token → 새 access_token 발급
  POST /api/auth/token/revoke  – 토큰 폐기 (블랙리스트 등록)

공용:
  GET  /api/me       – 현재 사용자 정보 (쿠키 또는 Bearer 모두 허용)
  GET  /api/sessions – 내 활성 세션 목록
  DELETE /api/sessions/{sid} – 특정 세션 강제 만료
"""
import uuid
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr

from app.config import settings
from app.database.mongo import get_mongo_db
from app.lib.jwt_auth import (
    create_token_pair,
    decode_token,
    get_current_user_any,
    is_revoked,
    require_roles,
    revoke_token,
)
from app.lib.session import (
    create_session,
    delete_all_user_sessions,
    delete_session,
    get_current_user,
    get_session,
    list_user_sessions,
)
from app.lib.user_state import clear_user_state, mark_offline, mark_online

router = APIRouter(prefix="/api")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 요청/응답 스키마 ───────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginBody(BaseModel):
    email: str
    password: str


class TokenRefreshBody(BaseModel):
    refresh_token: str


class TokenRevokeBody(BaseModel):
    access_token: str
    refresh_token: str | None = None


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _build_session_data(user_id: str, user: dict) -> dict:
    return {
        "id": user_id,
        "name": user["name"],
        "email": user["email"],
        "client_id": user.get("client_id", ""),
        "roles": user.get("roles", ["user"]),
    }


async def _find_user_by_email(db, email: str):
    try:
        return await db.users.find_one({"email": email})
    except Exception as e:
        raise HTTPException(503, f"데이터베이스 오류: {e}")


# ── 쿠키 기반 인증 ─────────────────────────────────────────────────────────────

@router.post("/auth/register")
async def register(body: RegisterBody, response: Response):
    try:
        db = get_mongo_db()
    except RuntimeError:
        raise HTTPException(503, "인증 서버(MongoDB)에 연결할 수 없습니다.")
    if await db.users.find_one({"email": body.email}):
        raise HTTPException(400, "이미 사용 중인 이메일입니다.")

    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    client_id = str(uuid.uuid4()).replace("-", "")[:16].upper()
    roles = ["admin"] if body.email in settings.admin_email_list else ["user"]

    user_doc = {
        "name": body.name,
        "email": body.email,
        "password_hash": pw_hash,
        "client_id": client_id,
        "roles": roles,
        "created_at": _now(),
    }
    result = await db.users.insert_one(user_doc)
    user_id = str(result.inserted_id)

    session_data = _build_session_data(user_id, {**user_doc, "client_id": client_id})
    sid = await create_session(session_data)
    await mark_online(user_id)

    response.set_cookie(
        "fin_session", sid,
        httponly=True, samesite=settings.COOKIE_SAMESITE,
        secure=settings.COOKIE_SECURE, max_age=settings.SESSION_TTL,
    )
    return {"ok": True, "user": {"name": body.name, "email": body.email,
                                  "clientId": client_id, "roles": roles}}


@router.post("/auth/login")
async def login(body: LoginBody, response: Response):
    try:
        db = get_mongo_db()
    except RuntimeError:
        raise HTTPException(503, "인증 서버(MongoDB)에 연결할 수 없습니다.")

    user = await _find_user_by_email(db, body.email)
    if not user or not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")

    user_id = str(user["_id"])
    session_data = _build_session_data(user_id, user)
    sid = await create_session(session_data)
    await mark_online(user_id)

    response.set_cookie(
        "fin_session", sid,
        httponly=True, samesite=settings.COOKIE_SAMESITE,
        secure=settings.COOKIE_SECURE, max_age=settings.SESSION_TTL,
    )
    return {"ok": True, "user": {"name": user["name"], "email": user["email"],
                                  "clientId": user.get("client_id", ""),
                                  "roles": user.get("roles", ["user"])}}


@router.post("/auth/logout")
async def logout(
    response: Response,
    user=Depends(get_current_user),
    fin_session: str | None = Cookie(default=None),
):
    if fin_session:
        await delete_session(fin_session)
    await mark_offline(user["id"])
    response.delete_cookie("fin_session")
    return {"ok": True}


# ── JWT 기반 인증 ──────────────────────────────────────────────────────────────

@router.post("/auth/token")
async def issue_token(body: LoginBody):
    """JWT 액세스/리프레시 토큰을 발급합니다 (API 클라이언트용)."""
    try:
        db = get_mongo_db()
    except RuntimeError:
        raise HTTPException(503, "인증 서버(MongoDB)에 연결할 수 없습니다.")

    user = await _find_user_by_email(db, body.email)
    if not user or not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")

    user_id = str(user["_id"])
    payload = {
        "sub": user_id,
        "id": user_id,
        "name": user["name"],
        "email": user["email"],
        "client_id": user.get("client_id", ""),
        "roles": user.get("roles", ["user"]),
    }
    await mark_online(user_id)
    return create_token_pair(payload)


@router.post("/auth/token/refresh")
async def refresh_token(body: TokenRefreshBody):
    """리프레시 토큰으로 새 액세스 토큰을 발급합니다."""
    if await is_revoked(body.refresh_token):
        raise HTTPException(401, "만료(폐기)된 리프레시 토큰입니다.")

    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(401, "리프레시 토큰이 아닙니다.")

    # 기존 리프레시 토큰은 유지, 새 액세스 토큰만 발급
    from app.lib.jwt_auth import create_access_token
    user_payload = {k: v for k, v in payload.items() if k not in ("type", "iat", "exp")}
    return {
        "access_token": create_access_token(user_payload),
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_TTL,
    }


@router.post("/auth/token/revoke")
async def revoke_tokens(body: TokenRevokeBody):
    """토큰을 블랙리스트에 등록합니다 (JWT 로그아웃)."""
    await revoke_token(body.access_token)
    if body.refresh_token:
        payload = decode_token(body.refresh_token)
        await mark_offline(payload.get("id", ""))
        await revoke_token(body.refresh_token)
    return {"ok": True}


# ── 공용 엔드포인트 ────────────────────────────────────────────────────────────

@router.get("/me")
async def me(user=Depends(get_current_user_any)):
    """현재 사용자 정보를 반환합니다 (쿠키 or Bearer 모두 허용)."""
    from app.lib.user_state import get_user_state
    state = await get_user_state(user["id"])
    return {
        "user": {
            "name": user["name"],
            "email": user["email"],
            "clientId": user.get("client_id", ""),
            "roles": user.get("roles", ["user"]),
        },
        "state": {
            "online": state.get("online", False),
            "last_seen": state.get("last_seen"),
            "active_conversation_id": state.get("active_conversation_id"),
        },
    }


@router.get("/sessions")
async def list_sessions(user=Depends(get_current_user_any)):
    """내 활성 세션 목록을 반환합니다."""
    sids = await list_user_sessions(user["id"])
    return {"sessions": sids, "count": len(sids)}


@router.delete("/sessions/{sid}")
async def revoke_session(
    sid: str,
    user=Depends(get_current_user_any),
):
    """특정 세션을 강제 만료시킵니다."""
    session = await get_session(sid)
    if not session or session.get("id") != user["id"]:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    await delete_session(sid)
    return {"ok": True}


@router.delete("/sessions")
async def revoke_all_sessions(user=Depends(require_roles("admin", "user"))):
    """내 모든 세션을 일괄 만료시킵니다 (전체 로그아웃)."""
    count = await delete_all_user_sessions(user["id"])
    await clear_user_state(user["id"])
    return {"ok": True, "revoked": count}
