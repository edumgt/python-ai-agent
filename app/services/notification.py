"""다중 채널 알림 서비스.

지원 채널:
  telegram  – 텔레그램 봇
  slack     – Slack Incoming Webhook
  email     – SMTP 이메일
  kakao     – 카카오 알림톡 (CoolSMS REST API)
  sms       – SMS (CoolSMS REST API)

채널 활성화 방법:
  1. .env 글로벌 설정 (전체 사용자 공용)
  2. MongoDB notification_settings 컬렉션의 사용자별 설정 우선 적용

사용법:
  텔레그램: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 설정
  Slack:    SLACK_WEBHOOK_URL 설정
  이메일:   SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_TO 설정
  카카오:   COOLSMS_API_KEY, COOLSMS_API_SECRET, KAKAO_SENDER_KEY, KAKAO_PHONE 설정
  SMS:      COOLSMS_API_KEY, COOLSMS_API_SECRET, SMS_FROM, SMS_TO 설정
"""
import asyncio
import hashlib
import hmac
import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ── 채널별 전송 함수 ────────────────────────────────────────────────────────


async def send_telegram(
    message: str,
    token: str = "",
    chat_id: str = "",
) -> bool:
    """텔레그램 메시지 전송 (HTML 태그 지원)."""
    tok = token or settings.TELEGRAM_BOT_TOKEN
    cid = chat_id or settings.TELEGRAM_CHAT_ID
    if not tok or not cid:
        logger.debug("텔레그램 설정 없음 – 건너뜀")
        return False
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(url, json={"chat_id": cid, "text": message, "parse_mode": "HTML"})
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("텔레그램 알림 실패: %s", exc)
        return False


async def send_slack(message: str, webhook_url: str = "") -> bool:
    """Slack Incoming Webhook 메시지 전송."""
    url = webhook_url or settings.SLACK_WEBHOOK_URL
    if not url:
        logger.debug("Slack 웹훅 URL 없음 – 건너뜀")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(url, json={"text": message})
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("Slack 알림 실패: %s", exc)
        return False


def _send_email_sync(
    subject: str,
    body: str,
    to_addr: str,
    host: str,
    port: int,
    user: str,
    password: str,
    from_addr: str,
) -> bool:
    """동기 SMTP 이메일 전송 (executor 에서 실행)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr or user
        msg["To"] = to_addr
        msg.attach(MIMEText(body, "html", "utf-8"))
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr or user, [to_addr], msg.as_string())
        return True
    except Exception as exc:
        logger.warning("이메일 알림 실패: %s", exc)
        return False


async def send_email(
    subject: str,
    body: str,
    to_addr: str = "",
    host: str = "",
    port: int = 0,
    user: str = "",
    password: str = "",
    from_addr: str = "",
) -> bool:
    """이메일 전송 (SMTP / STARTTLS)."""
    to_addr   = to_addr   or settings.SMTP_TO
    host      = host      or settings.SMTP_HOST
    port      = port      or settings.SMTP_PORT
    user      = user      or settings.SMTP_USER
    password  = password  or settings.SMTP_PASSWORD
    from_addr = from_addr or settings.SMTP_FROM or user
    if not (host and user and password and to_addr):
        logger.debug("SMTP 설정 없음 – 건너뜀")
        return False
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _send_email_sync, subject, body, to_addr, host, port, user, password, from_addr
    )


def _coolsms_auth_header(api_key: str, api_secret: str) -> str:
    """CoolSMS HMAC-SHA256 인증 헤더 생성."""
    date_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    salt = str(time.time())
    sig = hmac.new(api_secret.encode(), (date_str + salt).encode(), hashlib.sha256).hexdigest()
    return f"HMAC-SHA256 apiKey={api_key}, date={date_str}, salt={salt}, signature={sig}"


async def send_kakao(
    message: str,
    api_key: str = "",
    api_secret: str = "",
    sender_key: str = "",
    phone: str = "",
) -> bool:
    """카카오 알림톡 전송 (CoolSMS 친구톡 FriendTalk)."""
    api_key    = api_key    or settings.COOLSMS_API_KEY
    api_secret = api_secret or settings.COOLSMS_API_SECRET
    sender_key = sender_key or settings.KAKAO_SENDER_KEY
    phone      = phone      or settings.KAKAO_PHONE
    if not (api_key and api_secret and sender_key and phone):
        logger.debug("카카오 알림톡 설정 없음 – 건너뜀")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(
                "https://api.coolsms.co.kr/kakao/v2/send",
                headers={
                    "Authorization": _coolsms_auth_header(api_key, api_secret),
                    "Content-Type": "application/json",
                },
                json={
                    "message": {
                        "to": phone,
                        "from": sender_key,
                        "type": "FT",
                        "content": message,
                    }
                },
            )
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("카카오 알림톡 실패: %s", exc)
        return False


async def send_sms(
    message: str,
    api_key: str = "",
    api_secret: str = "",
    from_no: str = "",
    to_no: str = "",
) -> bool:
    """SMS 문자 전송 (CoolSMS REST API)."""
    api_key    = api_key    or settings.COOLSMS_API_KEY
    api_secret = api_secret or settings.COOLSMS_API_SECRET
    from_no    = from_no    or settings.SMS_FROM
    to_no      = to_no      or settings.SMS_TO
    if not (api_key and api_secret and from_no and to_no):
        logger.debug("SMS 설정 없음 – 건너뜀")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(
                "https://api.coolsms.co.kr/messages/v4/send",
                headers={
                    "Authorization": _coolsms_auth_header(api_key, api_secret),
                    "Content-Type": "application/json",
                },
                json={
                    "message": {
                        "to": to_no,
                        "from": from_no,
                        "text": message[:80],
                    }
                },
            )
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("SMS 알림 실패: %s", exc)
        return False


# ── 디스패처 ────────────────────────────────────────────────────────────────


async def _load_user_settings(user_id: str | None) -> dict:
    """MongoDB에서 사용자별 알림 설정을 조회한다."""
    if not user_id:
        return {}
    try:
        from app.database.mongo import get_mongo_db  # noqa: PLC0415 – lazy import to avoid circular
        mdb = get_mongo_db()
        doc = await mdb.notification_settings.find_one({"user_id": user_id})
        return doc or {}
    except Exception:
        return {}


def _default_channels() -> list[str]:
    """글로벌 .env 설정을 기반으로 활성화된 채널 목록을 반환한다."""
    channels: list[str] = []
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        channels.append("telegram")
    if settings.SLACK_WEBHOOK_URL:
        channels.append("slack")
    if settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD and settings.SMTP_TO:
        channels.append("email")
    if settings.COOLSMS_API_KEY and settings.COOLSMS_API_SECRET and settings.KAKAO_SENDER_KEY and settings.KAKAO_PHONE:
        channels.append("kakao")
    if settings.COOLSMS_API_KEY and settings.COOLSMS_API_SECRET and settings.SMS_FROM and settings.SMS_TO:
        channels.append("sms")
    return channels


async def dispatch(
    message: str,
    html_message: str = "",
    subject: str = "",
    user_id: str | None = None,
) -> None:
    """활성화된 모든 채널로 알림을 전송한다.

    Args:
        message:      일반 텍스트 메시지 (SMS · Slack · 카카오 사용)
        html_message: HTML 형식 메시지 (텔레그램 · 이메일 사용, 없으면 message 사용)
        subject:      이메일 제목 (없으면 message 앞 40자 사용)
        user_id:      사용자 ID – MongoDB 에서 개별 설정 조회
    """
    user_cfg = await _load_user_settings(user_id)
    channels: list[str] = user_cfg.get("channels") or _default_channels()

    html  = html_message or message
    plain = message
    subj  = subject or f"[매매 알림] {plain[:40]}"

    tasks = []
    if "telegram" in channels:
        tasks.append(send_telegram(
            html,
            token=user_cfg.get("telegram_token", ""),
            chat_id=user_cfg.get("telegram_chat_id", ""),
        ))
    if "slack" in channels:
        tasks.append(send_slack(
            plain,
            webhook_url=user_cfg.get("slack_webhook_url", ""),
        ))
    if "email" in channels:
        tasks.append(send_email(
            subject=subj,
            body=html,
            to_addr=user_cfg.get("email_to", ""),
            host=user_cfg.get("email_host", ""),
            port=int(user_cfg.get("email_port") or 0),
            user=user_cfg.get("email_user", ""),
            password=user_cfg.get("email_password", ""),
            from_addr=user_cfg.get("email_from", ""),
        ))
    if "kakao" in channels:
        tasks.append(send_kakao(
            plain,
            api_key=user_cfg.get("kakao_api_key", ""),
            api_secret=user_cfg.get("kakao_api_secret", ""),
            sender_key=user_cfg.get("kakao_sender_key", ""),
            phone=user_cfg.get("kakao_phone", ""),
        ))
    if "sms" in channels:
        tasks.append(send_sms(
            plain,
            api_key=user_cfg.get("sms_api_key", ""),
            api_secret=user_cfg.get("sms_api_secret", ""),
            from_no=user_cfg.get("sms_from", ""),
            to_no=user_cfg.get("sms_to", ""),
        ))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ── 알림 헬퍼 ────────────────────────────────────────────────────────────────


async def notify_insufficient_funds(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    required: float,
    available: float,
    user_id: str | None = None,
) -> None:
    """예수금 부족으로 주문 실패 알림."""
    label = "매수" if side == "buy" else "매도"
    html = (
        "🚨 <b>주문 실패 – 예수금 부족</b>\n\n"
        f"종목: <code>{symbol}</code>\n"
        f"구분: {label}\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"필요 금액: <b>{required:,.0f}원</b>\n"
        f"가용 예수금: <b>{available:,.0f}원</b>\n"
        f"부족분: {required - available:,.0f}원"
    )
    plain = (
        f"[주문 실패] 예수금 부족\n"
        f"종목: {symbol} / {label}\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"필요: {required:,.0f}원 / 가용: {available:,.0f}원"
    )
    await dispatch(plain, html_message=html, subject=f"[매매 알림] 주문 실패 – {symbol}", user_id=user_id)


async def notify_order_placed(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    broker: str = "",
    user_id: str | None = None,
) -> None:
    """주문 접수 알림."""
    emoji = "📈" if side == "buy" else "📉"
    label = "매수" if side == "buy" else "매도"
    broker_line = f"\n증권사: {broker}" if broker else ""
    html = (
        f"{emoji} <b>주문 접수</b>\n\n"
        f"종목: <code>{symbol}</code>\n"
        f"구분: {label}\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"총액: {quantity * price:,.0f}원{broker_line}"
    )
    plain = (
        f"{emoji} [주문 접수] {label}\n"
        f"종목: {symbol}\n"
        f"수량: {quantity:,}주 × {price:,.0f}원 / 총 {quantity * price:,.0f}원{broker_line}"
    )
    await dispatch(plain, html_message=html, subject=f"[매매 알림] {label} 주문 접수 – {symbol}", user_id=user_id)


async def notify_order_error(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    error: str,
    user_id: str | None = None,
) -> None:
    """주문 오류 알림."""
    label = "매수" if side == "buy" else "매도"
    html = (
        "❌ <b>주문 오류</b>\n\n"
        f"종목: <code>{symbol}</code>\n"
        f"구분: {label}\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"오류: {error}"
    )
    plain = (
        f"[주문 오류] {label}\n"
        f"종목: {symbol}\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"오류: {error}"
    )
    await dispatch(plain, html_message=html, subject=f"[매매 알림] 주문 오류 – {symbol}", user_id=user_id)


async def notify_auto_trade_started(user_id: str | None = None) -> None:
    """자동매매 시작 알림."""
    html  = "🤖 <b>자동매매 시작</b>\n\n10분 주기로 퀀트 신호를 분석합니다."
    plain = "[자동매매] 시작 – 10분 주기로 퀀트 신호를 분석합니다."
    await dispatch(plain, html_message=html, subject="[매매 알림] 자동매매 시작", user_id=user_id)


async def notify_auto_trade_stopped(user_id: str | None = None) -> None:
    """자동매매 중지 알림."""
    html  = "⏹ <b>자동매매 중지</b>\n\n자동매매가 정지되었습니다."
    plain = "[자동매매] 중지 – 자동매매가 정지되었습니다."
    await dispatch(plain, html_message=html, subject="[매매 알림] 자동매매 중지", user_id=user_id)


async def notify_auto_trade_executed(
    symbol: str,
    name: str,
    action: str,
    quantity: int,
    price: float,
    reason: str,
    user_id: str | None = None,
) -> None:
    """자동매매 체결 알림."""
    emoji = "📈" if action == "buy" else "📉"
    label = "매수" if action == "buy" else "매도"
    html = (
        f"{emoji} <b>[자동매매] {label} 체결</b>\n\n"
        f"종목: {name} (<code>{symbol}</code>)\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"사유: {reason}"
    )
    plain = (
        f"{emoji} [자동매매] {label} 체결\n"
        f"종목: {name} ({symbol})\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"사유: {reason}"
    )
    await dispatch(
        plain,
        html_message=html,
        subject=f"[매매 알림] {label} 체결 – {name}",
        user_id=user_id,
    )
