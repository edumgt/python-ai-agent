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
    """SQLite: 금융 통계 / 상품 데이터 전용 (사용자 데이터는 MongoDB)."""
    os.makedirs(os.path.dirname(os.path.abspath(settings.SQLITE_PATH)), exist_ok=True)
    async with aiosqlite.connect(settings.SQLITE_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript("""
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
        """)
        await db.commit()
