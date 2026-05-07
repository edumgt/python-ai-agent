from datetime import datetime, timezone
from app.database.mongo import get_mongo_db


async def audit(user_id: str, client_id: str, event_type: str, payload: dict) -> None:
    try:
        db = get_mongo_db()
        await db.audit_events.insert_one({
            "user_id":    user_id,
            "client_id":  client_id,
            "event_type": event_type,
            "payload":    payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass  # 감사 로그 실패는 무시
