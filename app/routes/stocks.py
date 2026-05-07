import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
import aiosqlite
from app.database.sqlite import get_db
from app.lib.session import get_current_user
from app.services.stock import (
    get_quote, get_candles, get_market_summary,
    get_quant_indicators, QUANT_STOCKS,
)
from app.services import auto_trade
from app.services.brokers.factory import get_broker_client

router = APIRouter(prefix="/api")


@router.get("/stocks/market")
async def market_summary():
    return {"indices": await get_market_summary()}


@router.get("/stocks/quote")
async def stock_quote(symbol: str = Query(...)):
    return await get_quote(symbol)


@router.get("/stocks/candles")
async def stock_candles(
    symbol: str = Query(...),
    period: str = Query("1y"),
    interval: str = Query("1d"),
):
    return await get_candles(symbol, period=period, interval=interval)


@router.get("/stocks/quant/indicators")
async def quant_indicators(
    symbol: str = Query(...),
    period: str = Query("2y"),
):
    return await get_quant_indicators(symbol, period=period)


@router.get("/stocks/quant/list")
async def quant_stock_list():
    return {"stocks": QUANT_STOCKS}


# ── 포트폴리오 ────────────────────────────────────────────────────────
class HoldingBody(BaseModel):
    symbol: str
    name: str
    quantity: int
    avg_price: float


@router.get("/portfolio")
async def get_portfolio(
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    async with db.execute(
        "SELECT * FROM portfolio WHERE mongo_user_id=? ORDER BY updated_at DESC",
        (user["id"],)
    ) as cur:
        rows = await cur.fetchall()
    return {"holdings": [dict(r) for r in rows]}


@router.post("/portfolio")
async def upsert_holding(
    body: HoldingBody,
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO portfolio (mongo_user_id, symbol, name, quantity, avg_price, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(mongo_user_id, symbol) DO UPDATE SET "
        "quantity=excluded.quantity, avg_price=excluded.avg_price, updated_at=excluded.updated_at",
        (user["id"], body.symbol, body.name, body.quantity, body.avg_price, now, now),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/portfolio/{symbol}")
async def delete_holding(
    symbol: str,
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute(
        "DELETE FROM portfolio WHERE mongo_user_id=? AND symbol=?",
        (user["id"], symbol)
    )
    await db.commit()
    return {"ok": True}


# ── 수동 주문 ─────────────────────────────────────────────────────────
class OrderBody(BaseModel):
    symbol: str
    name: str
    order_type: str  # buy | sell
    quantity: int
    price: float
    broker: str = "virtual"


@router.post("/orders")
async def place_order(
    body: OrderBody,
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    now = datetime.now(timezone.utc).isoformat()
    # 키움/토스는 Mockup: 즉시 filled 처리
    await db.execute(
        "INSERT INTO orders (mongo_user_id, symbol, name, order_type, quantity, price, status, broker, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'filled', ?, ?)",
        (user["id"], body.symbol, body.name, body.order_type,
         body.quantity, body.price, body.broker, now),
    )
    # 포트폴리오 반영
    if body.order_type == "buy":
        existing = await (await db.execute(
            "SELECT quantity, avg_price FROM portfolio WHERE mongo_user_id=? AND symbol=?",
            (user["id"], body.symbol)
        )).fetchone()
        if existing:
            new_qty = existing["quantity"] + body.quantity
            new_avg = (existing["avg_price"] * existing["quantity"] + body.price * body.quantity) / new_qty
            await db.execute(
                "UPDATE portfolio SET quantity=?, avg_price=?, updated_at=? WHERE mongo_user_id=? AND symbol=?",
                (new_qty, new_avg, now, user["id"], body.symbol),
            )
        else:
            await db.execute(
                "INSERT INTO portfolio (mongo_user_id, symbol, name, quantity, avg_price, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user["id"], body.symbol, body.name, body.quantity, body.price, now, now),
            )
    elif body.order_type == "sell":
        existing = await (await db.execute(
            "SELECT quantity FROM portfolio WHERE mongo_user_id=? AND symbol=?",
            (user["id"], body.symbol)
        )).fetchone()
        if existing:
            new_qty = existing["quantity"] - body.quantity
            if new_qty <= 0:
                await db.execute(
                    "DELETE FROM portfolio WHERE mongo_user_id=? AND symbol=?",
                    (user["id"], body.symbol)
                )
            else:
                await db.execute(
                    "UPDATE portfolio SET quantity=?, updated_at=? WHERE mongo_user_id=? AND symbol=?",
                    (new_qty, now, user["id"], body.symbol),
                )
    await db.commit()
    return {"ok": True, "status": "filled"}


@router.get("/orders")
async def order_history(
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    async with db.execute(
        "SELECT * FROM orders WHERE mongo_user_id=? ORDER BY created_at DESC LIMIT 200",
        (user["id"],)
    ) as cur:
        rows = await cur.fetchall()
    return {"orders": [dict(r) for r in rows]}


# ── 증권사 API 설정 ───────────────────────────────────────────────────
class BrokerSettingsBody(BaseModel):
    broker: str = "mock"          # kis | ebest | mock
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    paper: bool = True            # 모의투자 여부 (KIS만 해당)


@router.post("/broker/settings")
async def save_broker_settings(
    body: BrokerSettingsBody,
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO broker_settings "
        "(mongo_user_id, kiwoom_app_key, kiwoom_secret, toss_app_key, toss_secret, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(mongo_user_id) DO UPDATE SET "
        "kiwoom_app_key=excluded.kiwoom_app_key, kiwoom_secret=excluded.kiwoom_secret, "
        "toss_app_key=excluded.toss_app_key, toss_secret=excluded.toss_secret, "
        "updated_at=excluded.updated_at",
        (user["id"], json.dumps({"broker": body.broker, "app_key": body.app_key,
                                  "account_no": body.account_no, "paper": body.paper}),
         body.app_secret, "", "", now),
    )
    await db.commit()
    return {"ok": True}


@router.get("/broker/settings")
async def get_broker_settings(
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    async with db.execute(
        "SELECT kiwoom_app_key, kiwoom_secret FROM broker_settings WHERE mongo_user_id=?",
        (user["id"],)
    ) as cur:
        row = await cur.fetchone()
    if not row or not row["kiwoom_app_key"]:
        return {"broker": "mock", "connected": False, "account_no": "", "paper": True}
    try:
        cfg = json.loads(row["kiwoom_app_key"])
    except Exception:
        cfg = {}
    masked = (cfg.get("app_key", "") or "")
    masked = masked[:4] + "****" if masked else ""
    return {
        "broker":     cfg.get("broker", "mock"),
        "connected":  bool(cfg.get("app_key")),
        "app_key":    masked,
        "account_no": cfg.get("account_no", ""),
        "paper":      cfg.get("paper", True),
    }


async def _get_broker_client(user: dict, db: aiosqlite.Connection):
    async with db.execute(
        "SELECT kiwoom_app_key, kiwoom_secret FROM broker_settings WHERE mongo_user_id=?",
        (user["id"],)
    ) as cur:
        row = await cur.fetchone()
    if not row or not row["kiwoom_app_key"]:
        return get_broker_client("mock")
    try:
        cfg = json.loads(row["kiwoom_app_key"])
    except Exception:
        cfg = {}
    return get_broker_client(
        broker     = cfg.get("broker", "mock"),
        app_key    = cfg.get("app_key", ""),
        app_secret = row["kiwoom_secret"] or "",
        paper      = cfg.get("paper", True),
    )


# ── 증권사 API 실시간 조회 ────────────────────────────────────────────
@router.get("/broker/price")
async def broker_price(
    symbol: str = Query(...),
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    client = await _get_broker_client(user, db)
    try:
        info = await client.get_price(symbol)
        return {
            "symbol":     info.symbol,
            "name":       info.name,
            "current":    info.current,
            "open":       info.open,
            "high":       info.high,
            "low":        info.low,
            "volume":     info.volume,
            "change":     info.change,
            "change_pct": info.change_pct,
        }
    except Exception as e:
        raise HTTPException(502, f"증권사 API 오류: {e}")


@router.get("/broker/balance")
async def broker_balance(
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    client = await _get_broker_client(user, db)
    async with db.execute(
        "SELECT kiwoom_app_key FROM broker_settings WHERE mongo_user_id=?",
        (user["id"],)
    ) as cur:
        row = await cur.fetchone()
    try:
        cfg = json.loads(row["kiwoom_app_key"]) if row and row["kiwoom_app_key"] else {}
    except Exception:
        cfg = {}
    account_no = cfg.get("account_no", "")
    try:
        bal = await client.get_balance(account_no)
        return {
            "total_eval": bal.total_eval,
            "total_buy":  bal.total_buy,
            "total_gain": bal.total_gain,
            "holdings": [
                {
                    "symbol":        h.symbol,
                    "name":          h.name,
                    "quantity":      h.quantity,
                    "avg_price":     h.avg_price,
                    "current_price": h.current_price,
                    "eval_amount":   h.eval_amount,
                    "gain_loss":     h.gain_loss,
                    "gain_pct":      h.gain_pct,
                }
                for h in bal.holdings
            ],
        }
    except Exception as e:
        raise HTTPException(502, f"증권사 API 오류: {e}")


@router.get("/broker/ohlcv")
async def broker_ohlcv(
    symbol: str = Query(...),
    start: str = Query(..., description="YYYYMMDD"),
    end:   str = Query(..., description="YYYYMMDD"),
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    client = await _get_broker_client(user, db)
    try:
        rows = await client.get_daily_ohlcv(symbol, start, end)
        return {"candles": rows}
    except Exception as e:
        raise HTTPException(502, f"증권사 API 오류: {e}")


class BrokerOrderBody(BaseModel):
    symbol:   str
    side:     str    # buy | sell
    quantity: int
    price:    float


@router.post("/broker/order")
async def broker_order(
    body: BrokerOrderBody,
    user=Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    client = await _get_broker_client(user, db)
    async with db.execute(
        "SELECT kiwoom_app_key FROM broker_settings WHERE mongo_user_id=?",
        (user["id"],)
    ) as cur:
        row = await cur.fetchone()
    try:
        cfg = json.loads(row["kiwoom_app_key"]) if row and row["kiwoom_app_key"] else {}
    except Exception:
        cfg = {}
    account_no = cfg.get("account_no", "")
    try:
        result = await client.place_order(account_no, body.symbol, body.side, body.quantity, body.price)
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(502, f"증권사 API 주문 오류: {e}")


# ── 자동매매 제어 ─────────────────────────────────────────────────────
@router.post("/auto-trade/start")
async def start_auto_trade(user=Depends(get_current_user)):
    started = auto_trade.start_auto_trade()
    return {"ok": True, "started": started}


@router.post("/auto-trade/stop")
async def stop_auto_trade(user=Depends(get_current_user)):
    stopped = auto_trade.stop_auto_trade()
    return {"ok": True, "stopped": stopped}


@router.get("/auto-trade/status")
async def auto_trade_status(user=Depends(get_current_user)):
    return auto_trade.get_status()
