"""금융 AI Agent - FastAPI 메인 엔트리포인트."""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database.mongo import connect_mongo, close_mongo, ensure_indexes
from app.lib.session import connect_redis, close_redis
from app.routes import health, auth, chat, ingest, stocks, library, admin, system, quant, ml, macro, documents, notification


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작
    try:
        await connect_redis()
    except Exception as e:
        print(f"[WARN] Redis 연결 실패 (세션 비활성): {e}")
    try:
        await connect_mongo()
        await ensure_indexes()
    except Exception as e:
        print(f"[WARN] MongoDB 연결 실패 (인증 비활성): {e}")
    print("[fin-agent] 서버 시작 완료")
    yield
    # 종료
    await close_redis()
    await close_mongo()


app = FastAPI(
    title="금융 AI Agent",
    description="개인/기업 CB 분석 · 금융상품 · 주가 · 퀀트 자동매매",
    version="1.0.0",
    lifespan=lifespan,
)

# 라우터 등록
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(ingest.router)
app.include_router(stocks.router)
app.include_router(library.router)
app.include_router(admin.router)
app.include_router(system.router)
app.include_router(quant.router)
app.include_router(ml.router)
app.include_router(macro.router)
app.include_router(documents.router)
app.include_router(notification.router)

# 정적 파일 (프론트엔드)
_public = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(_public):
    app.mount("/js", StaticFiles(directory=os.path.join(_public, "js")), name="js")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(os.path.join(_public, "index.html"))

    @app.get("/login.html", include_in_schema=False)
    async def login_page():
        return FileResponse(os.path.join(_public, "login.html"))

    @app.get("/register.html", include_in_schema=False)
    async def register_page():
        return FileResponse(os.path.join(_public, "register.html"))

    @app.get("/app.html", include_in_schema=False)
    async def app_page():
        return FileResponse(os.path.join(_public, "app.html"))
