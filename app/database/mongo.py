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


async def ensure_indexes() -> None:
    db = get_mongo_db()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("client_id", unique=True)
