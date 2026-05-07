import aiosqlite
import os
from app.config import settings


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.SQLITE_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(settings.SQLITE_PATH)), exist_ok=True)
    async with aiosqlite.connect(settings.SQLITE_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript("""
            -- 개인 CB 집계 테이블
            CREATE TABLE IF NOT EXISTS personal_cb_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stdt TEXT NOT NULL,
                gender INTEGER,
                age_band INTEGER,
                cnt INTEGER,
                avg_score REAL,
                avg_score_6m REAL,
                default_rate_1 REAL,
                default_rate_2 REAL
            );
            CREATE INDEX IF NOT EXISTS idx_pcb_stdt ON personal_cb_stats(stdt);

            -- 기업 CB 집계 테이블
            CREATE TABLE IF NOT EXISTS corporate_cb_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bs_dt TEXT NOT NULL,
                sic_cd TEXT,
                wg_gb INTEGER,
                cnt INTEGER,
                avg_corp_grad REAL,
                default_rate REAL
            );
            CREATE INDEX IF NOT EXISTS idx_ccb_dt ON corporate_cb_stats(bs_dt);

            -- 은행 수신상품
            CREATE TABLE IF NOT EXISTS bank_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bank_code TEXT,
                bank_name TEXT,
                product_code TEXT,
                product_name TEXT,
                product_group TEXT,
                min_period TEXT,
                max_period TEXT,
                min_amount TEXT,
                max_amount TEXT,
                base_rate REAL,
                max_rate REAL,
                deposit_type TEXT,
                maturity TEXT,
                deposit_protection TEXT,
                product_summary TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_bp_rate ON bank_products(base_rate);

            -- 공모펀드 상품
            CREATE TABLE IF NOT EXISTS fund_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eval_date TEXT,
                fund_code TEXT,
                fund_name TEXT,
                company_name TEXT,
                main_type TEXT,
                mid_type TEXT,
                sub_type TEXT,
                strategy TEXT,
                aum REAL,
                risk_grade INTEGER,
                nav REAL,
                return_1y REAL,
                expense_ratio REAL,
                is_retirement INTEGER DEFAULT 0,
                is_esg INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_fp_type ON fund_products(main_type);

            -- 채팅 내역
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mongo_user_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                steps_json TEXT,
                citations_json TEXT,
                created_at TEXT NOT NULL
            );

            -- 감사 로그
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mongo_user_id TEXT,
                client_id TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ae_user ON audit_events(mongo_user_id, created_at);

            -- 가상 포트폴리오
            CREATE TABLE IF NOT EXISTS portfolio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mongo_user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                avg_price REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(mongo_user_id, symbol)
            );

            -- 가상 주문 내역
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mongo_user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                order_type TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'filled',
                broker TEXT DEFAULT 'virtual',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(mongo_user_id, created_at);

            -- 증권사 API 설정 (mockup)
            CREATE TABLE IF NOT EXISTS broker_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mongo_user_id TEXT NOT NULL UNIQUE,
                kiwoom_app_key TEXT,
                kiwoom_secret TEXT,
                toss_app_key TEXT,
                toss_secret TEXT,
                updated_at TEXT NOT NULL
            );

            -- 크롤링된 뉴스/공시 (Qdrant 미사용 시 폴백)
            CREATE TABLE IF NOT EXISTS crawled_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                content TEXT,
                source TEXT,
                crawled_at TEXT NOT NULL
            );
        """)
        await db.commit()
