"""알림 설정 API.

GET  /api/notification/settings  – 현재 사용자의 알림 설정 조회
POST /api/notification/settings  – 알림 설정 저장
POST /api/notification/test      – 테스트 알림 전송
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.database.mongo import get_mdb
from app.lib.session import get_current_user
from app.services import notification

router = APIRouter(prefix="/api/notification")

_SUPPORTED_CHANNELS = {"telegram", "slack", "email", "kakao", "sms"}


class NotificationSettingsBody(BaseModel):
    """알림 설정 저장 요청 모델."""

    channels: list[str] = Field(default_factory=list, description="활성화할 채널 목록")

    # 텔레그램
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Slack
    slack_webhook_url: str = ""

    # 이메일
    email_to: str = ""
    email_host: str = ""
    email_port: int = Field(default=587, ge=1, le=65535)
    email_user: str = ""
    email_password: str = ""
    email_from: str = ""

    # 카카오 알림톡
    kakao_api_key: str = ""
    kakao_api_secret: str = ""
    kakao_sender_key: str = ""
    kakao_phone: str = ""

    # SMS
    sms_api_key: str = ""
    sms_api_secret: str = ""
    sms_from: str = ""
    sms_to: str = ""


@router.get("/settings")
async def get_notification_settings(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    """현재 사용자의 알림 설정을 반환한다. 비밀값(password/secret)은 마스킹."""
    doc = await mdb.notification_settings.find_one({"user_id": user["id"]}) or {}

    def mask(v: str) -> str:
        return v[:4] + "****" if v and len(v) > 4 else ("****" if v else "")

    return {
        "channels":          doc.get("channels", []),
        "telegram_token":    mask(doc.get("telegram_token", "")),
        "telegram_chat_id":  doc.get("telegram_chat_id", ""),
        "slack_webhook_url": doc.get("slack_webhook_url", ""),
        "email_to":          doc.get("email_to", ""),
        "email_host":        doc.get("email_host", ""),
        "email_port":        doc.get("email_port", 587),
        "email_user":        doc.get("email_user", ""),
        "email_password":    mask(doc.get("email_password", "")),
        "email_from":        doc.get("email_from", ""),
        "kakao_api_key":     mask(doc.get("kakao_api_key", "")),
        "kakao_api_secret":  mask(doc.get("kakao_api_secret", "")),
        "kakao_sender_key":  doc.get("kakao_sender_key", ""),
        "kakao_phone":       doc.get("kakao_phone", ""),
        "sms_api_key":       mask(doc.get("sms_api_key", "")),
        "sms_api_secret":    mask(doc.get("sms_api_secret", "")),
        "sms_from":          doc.get("sms_from", ""),
        "sms_to":            doc.get("sms_to", ""),
    }


@router.post("/settings")
async def save_notification_settings(
    body: NotificationSettingsBody,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    """알림 설정을 저장한다. 빈 문자열 비밀값은 기존 값을 유지한다."""
    channels = [c for c in body.channels if c in _SUPPORTED_CHANNELS]
    now = datetime.now(timezone.utc).isoformat()

    # 기존 저장된 비밀값을 읽어 빈 입력 시 유지
    existing = await mdb.notification_settings.find_one({"user_id": user["id"]}) or {}

    def keep_secret(new_val: str, field: str) -> str:
        """마스킹된 값(****) 또는 빈 값이면 기존 값을 유지."""
        if not new_val or "****" in new_val:
            return existing.get(field, "")
        return new_val

    await mdb.notification_settings.update_one(
        {"user_id": user["id"]},
        {"$set": {
            "channels":          channels,
            "telegram_token":    keep_secret(body.telegram_token, "telegram_token"),
            "telegram_chat_id":  body.telegram_chat_id,
            "slack_webhook_url": body.slack_webhook_url,
            "email_to":          body.email_to,
            "email_host":        body.email_host,
            "email_port":        body.email_port,
            "email_user":        body.email_user,
            "email_password":    keep_secret(body.email_password, "email_password"),
            "email_from":        body.email_from,
            "kakao_api_key":     keep_secret(body.kakao_api_key, "kakao_api_key"),
            "kakao_api_secret":  keep_secret(body.kakao_api_secret, "kakao_api_secret"),
            "kakao_sender_key":  body.kakao_sender_key,
            "kakao_phone":       body.kakao_phone,
            "sms_api_key":       keep_secret(body.sms_api_key, "sms_api_key"),
            "sms_api_secret":    keep_secret(body.sms_api_secret, "sms_api_secret"),
            "sms_from":          body.sms_from,
            "sms_to":            body.sms_to,
            "updated_at":        now,
        }},
        upsert=True,
    )
    return {"ok": True}


@router.post("/test")
async def test_notification(
    user=Depends(get_current_user),
):
    """현재 설정된 모든 채널로 테스트 알림을 전송한다."""
    html = (
        "🔔 <b>[알림 테스트]</b>\n\n"
        "매매 알림이 정상적으로 설정되었습니다.\n"
        "이 메시지가 수신되면 해당 채널이 활성화된 것입니다."
    )
    plain = (
        "[알림 테스트] 매매 알림이 정상적으로 설정되었습니다.\n"
        "이 메시지가 수신되면 해당 채널이 활성화된 것입니다."
    )
    await notification.dispatch(
        plain,
        html_message=html,
        subject="[매매 알림] 테스트",
        user_id=user["id"],
    )
    return {"ok": True, "message": "테스트 알림을 전송했습니다."}
