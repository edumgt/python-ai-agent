from pydantic_settings import BaseSettings
import os


class Settings(BaseSettings):
    PORT: int = 8000
    SESSION_SECRET: str = "change-me-super-secret"
    SESSION_TTL: int = 604800  # 7 days

    REDIS_URL: str = "redis://localhost:6379"
    MONGO_URI: str = "mongodb://law_user:law_pass@localhost:27017/fin_agent?authSource=admin"
    MONGO_DB: str = "fin_agent"

    # ── Supabase (선택 – SUPABASE_URL 미설정 시 기능 비활성) ──────────────────
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_KEY: str = ""  # service_role 키 (RLS 우회)

    # ── JWT ──────────────────────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-jwt-secret-32chars-min!!"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TTL: int = 900       # 15분 (초)
    JWT_REFRESH_TTL: int = 604800   # 7일 (초)

    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    LLM_MODEL: str = "llama3.1"
    EMBED_MODEL: str = "nomic-embed-text"
    VLM_MODEL: str = "llava"          # Vision-Language Model for image/slide description
    OLLAMA_TIMEOUT: float = 300.0

    VECTOR_STORE: str = "qdrant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "fin_chunks"
    DOCUMENT_COLLECTION: str = "fin_chunks"  # Qdrant collection for uploaded documents

    DATA_DIR: str = "./data"
    TOP_K: int = 6

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "finagent123"

    ADMIN_EMAILS: str = ""
    TRUST_PROXY: bool = False
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    # ── 알림 채널 설정 ──────────────────────────────────────────────────────────

    # 텔레그램
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Slack Incoming Webhook
    SLACK_WEBHOOK_URL: str = ""

    # 이메일 (SMTP / STARTTLS)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_TO: str = ""

    # 카카오 알림톡 · SMS (CoolSMS REST API)
    COOLSMS_API_KEY: str = ""
    COOLSMS_API_SECRET: str = ""
    KAKAO_SENDER_KEY: str = ""   # 카카오 채널 발신 프로필 키
    KAKAO_PHONE: str = ""        # 수신 전화번호 (예: 01012345678)
    SMS_FROM: str = ""           # 발신 번호
    SMS_TO: str = ""             # 수신 번호

    class Config:
        env_file = os.getenv("ENV_FILE", ".env.dev")
        extra = "ignore"

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip() for e in self.ADMIN_EMAILS.split(",") if e.strip()]


settings = Settings()
