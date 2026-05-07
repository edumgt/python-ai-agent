from pydantic_settings import BaseSettings
import os


class Settings(BaseSettings):
    PORT: int = 8000
    SQLITE_PATH: str = "./data/app.db"
    SESSION_SECRET: str = "change-me-super-secret"
    SESSION_TTL: int = 604800  # 7 days

    REDIS_URL: str = "redis://localhost:6379"
    MONGO_URI: str = "mongodb://law_user:law_pass@localhost:27017/fin_agent?authSource=admin"
    MONGO_DB: str = "fin_agent"

    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    LLM_MODEL: str = "llama3.1"
    EMBED_MODEL: str = "nomic-embed-text"
    OLLAMA_TIMEOUT: float = 300.0

    VECTOR_STORE: str = "qdrant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "fin_chunks"

    DATA_DIR: str = "./data"
    TOP_K: int = 6

    ADMIN_EMAILS: str = ""
    TRUST_PROXY: bool = False
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    class Config:
        env_file = os.getenv("ENV_FILE", ".env.dev")
        extra = "ignore"

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip() for e in self.ADMIN_EMAILS.split(",") if e.strip()]


settings = Settings()
