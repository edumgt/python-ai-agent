import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Response, Cookie
from pydantic import BaseModel, EmailStr
import bcrypt
from app.database.mongo import get_mongo_db
from app.lib.session import create_session, delete_session, get_current_user, get_session
from app.config import settings

router = APIRouter(prefix="/api")


class RegisterBody(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/auth/register")
async def register(body: RegisterBody, response: Response):
    try:
        db = get_mongo_db()
    except RuntimeError:
        raise HTTPException(503, "인증 서버(MongoDB)에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.")
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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.users.insert_one(user_doc)
    user_id = str(result.inserted_id)

    session_data = {"id": user_id, "name": body.name,
                    "email": body.email, "client_id": client_id, "roles": roles}
    sid = await create_session(session_data)
    response.set_cookie(
        "fin_session", sid,
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
        secure=settings.COOKIE_SECURE,
        max_age=settings.SESSION_TTL,
    )
    return {"ok": True, "user": {"name": body.name, "email": body.email,
                                  "clientId": client_id, "roles": roles}}


@router.post("/auth/login")
async def login(body: LoginBody, response: Response):
    try:
        db = get_mongo_db()
        user = await db.users.find_one({"email": body.email})
    except RuntimeError:
        raise HTTPException(503, "인증 서버(MongoDB)에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.")
    except Exception as e:
        raise HTTPException(503, f"데이터베이스 오류: {e}")
    if not user or not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")

    user_id = str(user["_id"])
    roles = user.get("roles", ["user"])
    session_data = {
        "id": user_id, "name": user["name"], "email": user["email"],
        "client_id": user.get("client_id", ""), "roles": roles,
    }
    sid = await create_session(session_data)
    response.set_cookie(
        "fin_session", sid,
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
        secure=settings.COOKIE_SECURE,
        max_age=settings.SESSION_TTL,
    )
    return {"ok": True, "user": {"name": user["name"], "email": user["email"],
                                  "clientId": user.get("client_id", ""), "roles": roles}}


@router.post("/auth/logout")
async def logout(response: Response, user=Depends(get_current_user),
                 fin_session: str | None = Cookie(default=None)):
    if fin_session:
        await delete_session(fin_session)
    response.delete_cookie("fin_session")
    return {"ok": True}


@router.get("/me")
async def me(user=Depends(get_current_user)):
    return {"user": {
        "name": user["name"], "email": user["email"],
        "clientId": user["client_id"], "roles": user.get("roles", ["user"]),
    }}
