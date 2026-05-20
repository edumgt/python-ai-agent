-- ─────────────────────────────────────────────────────────────────────────────
-- Lumina Invest – Supabase (PostgreSQL) 테이블 스키마
-- Supabase Dashboard → SQL Editor 에서 실행하세요.
-- ─────────────────────────────────────────────────────────────────────────────

-- 확장: UUID 생성 함수
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── 대화 스레드 ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       TEXT        NOT NULL,           -- MongoDB user._id (string)
    title         TEXT        NOT NULL DEFAULT '새 대화',
    message_count INT         NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_id
    ON conversations (user_id, updated_at DESC);

-- ── 메시지 ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID        NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id         TEXT        NOT NULL,
    role            TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT        NOT NULL,
    citations       JSONB       DEFAULT '[]',
    steps           JSONB       DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
    ON messages (conversation_id, created_at ASC);

-- ── 포트폴리오 ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolios (
    id          UUID    PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    qty         NUMERIC NOT NULL DEFAULT 0,
    avg_price   NUMERIC NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_portfolios_user_id ON portfolios (user_id);

-- ── 관심 종목 ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist (
    id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id  TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_user_id ON watchlist (user_id);

-- ── updated_at 자동 갱신 트리거 ───────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_portfolios_updated_at
    BEFORE UPDATE ON portfolios
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── RLS (Row Level Security) – 선택 사항 ──────────────────────────────────────
-- 서비스 키(service_role)를 사용할 때는 RLS 가 우회되므로 서버 사이드에서는
-- 비활성화 상태로 두어도 됩니다. 클라이언트 직접 접근이 필요하면 활성화하세요.
--
-- ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE messages      ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE portfolios    ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE watchlist     ENABLE ROW LEVEL SECURITY;
