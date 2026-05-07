import json
from datetime import datetime, timezone
import aiosqlite


async def audit(db: aiosqlite.Connection, mongo_user_id: str, client_id: str,
                event_type: str, payload: dict) -> None:
    await db.execute(
        "INSERT INTO audit_events (mongo_user_id, client_id, event_type, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (mongo_user_id, client_id, event_type, json.dumps(payload, ensure_ascii=False),
         datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()
