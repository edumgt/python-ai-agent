from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import settings

_client: AsyncIOMotorClient | None = None


async def connect_mongo() -> None:
    global _client
    _client = AsyncIOMotorClient(
        settings.MONGO_URI,
        serverSelectionTimeoutMS=3000,
        connectTimeoutMS=3000,
    )
    await _client.admin.command("ping")


async def close_mongo() -> None:
    global _client
    if _client:
        _client.close()
        _client = None


def get_mongo_db() -> AsyncIOMotorDatabase:
    if _client is None:
        raise RuntimeError("MongoDB not connected")
    return _client[settings.MONGO_DB]


def get_mdb() -> AsyncIOMotorDatabase:
    """FastAPI Depends용 MongoDB 데이터베이스 의존성."""
    return get_mongo_db()


async def ensure_indexes() -> None:
    db = get_mongo_db()
    # 인증
    await db.users.create_index("email", unique=True)
    await db.users.create_index("client_id", unique=True)
    # 포트폴리오: 사용자+종목 복합 유니크
    await db.portfolio.create_index([("user_id", 1), ("symbol", 1)], unique=True)
    # 주문: 사용자+시간 조회 최적화
    await db.orders.create_index([("user_id", 1), ("created_at", -1)])
    # 증권사 설정: 사용자당 1건
    await db.broker_settings.create_index("user_id", unique=True)
    # 대화 스레드
    await db.conversations.create_index([("user_id", 1), ("updated_at", -1)])
    # 채팅 기록 (conversation_id 포함)
    await db.chats.create_index([("user_id", 1), ("created_at", -1)])
    await db.chats.create_index([("conversation_id", 1), ("created_at", 1)])
    # 크롤링 문서: URL 유니크
    await db.crawled_docs.create_index("url", unique=True)
    # 감사 로그
    await db.audit_events.create_index([("user_id", 1), ("created_at", -1)])
    # 금융 데이터 컬렉션 (SQLite에서 이관)
    await db.personal_cb_stats.create_index("stdt")
    await db.personal_cb_stats.create_index([("gender", 1), ("age_band", 1)])
    await db.corporate_cb_stats.create_index("bs_dt")
    await db.corporate_cb_stats.create_index([("sic_cd", 1), ("wg_gb", 1)])
    await db.bank_products.create_index("base_rate")
    await db.fund_products.create_index("main_type")
