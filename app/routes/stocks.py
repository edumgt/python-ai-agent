import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from app.database.mongo import get_mdb
from app.lib.session import get_current_user
from app.services.stock import (
    get_quote, get_candles, get_market_summary,
    get_quant_indicators, QUANT_STOCKS,
)
from app.services import auto_trade
from app.services.quant_pipeline import backtest_custom_indicator
from app.services.brokers.factory import get_broker_client
from app.services.brokers.catalog import get_broker_catalog, get_broker_codes
from app.services import notification

router = APIRouter(prefix="/api")
DEFAULT_BROKER = "mock"


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


@router.get("/stocks/signals")
async def stock_signals(
    signal: str = Query("all", description="all | buy | sell"),
    model: str = Query("lightgbm", description="lightgbm | rsi | ma | bollinger"),
    min_confidence: int = Query(65, ge=0, le=100),
):
    """대표 종목 패턴 시그널 스크리닝."""
    rows = []
    for stock in QUANT_STOCKS:
        indicators = await get_quant_indicators(stock["symbol"], period="1y")
        if indicators.get("error"):
            continue
        quote = await get_quote(stock["symbol"])
        action = (indicators.get("signal") or {}).get("action", "관망")
        score = float((indicators.get("signal") or {}).get("score", 0))
        mapped = "HOLD"
        if action in ("강력 매수", "매수"):
            mapped = "BUY"
        elif action in ("강력 매도", "매도"):
            mapped = "SELL"
        confidence = int(max(50, min(95, 50 + abs(score) * 12)))
        row = {
            "symbol": stock["symbol"],
            "name": stock["name"],
            "sector": stock.get("sector", ""),
            "model": model,
            "signal": mapped,
            "confidence": confidence,
            "score": score,
            "rsi": indicators.get("current_rsi"),
            "price": quote.get("price") or indicators.get("current_price"),
            "change_pct": quote.get("change_pct"),
        }
        rows.append(row)

    signal_filter = (signal or "all").lower()
    if signal_filter in ("buy", "sell"):
        rows = [r for r in rows if r["signal"].lower() == signal_filter]
    rows = [r for r in rows if r["confidence"] >= int(min_confidence)]
    rows.sort(key=lambda x: (x["confidence"], abs(x["score"])), reverse=True)
    return {"signals": rows, "count": len(rows)}


# ── 포트폴리오 (MongoDB) ──────────────────────────────────────────────

class HoldingBody(BaseModel):
    symbol: str
    name: str
    quantity: int
    avg_price: float


@router.get("/portfolio")
async def get_portfolio(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    cursor = mdb.portfolio.find({"user_id": user["id"]}).sort("updated_at", -1)
    holdings = []
    async for doc in cursor:
        doc.pop("_id", None)
        holdings.append(doc)
    return {"holdings": holdings}


@router.post("/portfolio")
async def upsert_holding(
    body: HoldingBody,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    now = datetime.now(timezone.utc).isoformat()
    await mdb.portfolio.update_one(
        {"user_id": user["id"], "symbol": body.symbol},
        {"$set": {
            "name":       body.name,
            "quantity":   body.quantity,
            "avg_price":  body.avg_price,
            "updated_at": now,
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"ok": True}


@router.delete("/portfolio/{symbol}")
async def delete_holding(
    symbol: str,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    await mdb.portfolio.delete_one({"user_id": user["id"], "symbol": symbol})
    return {"ok": True}


# ── 수동 주문 (MongoDB) ───────────────────────────────────────────────

class OrderBody(BaseModel):
    symbol: str
    name: str
    order_type: str   # buy | sell
    quantity: int
    price: float
    broker: str = "virtual"


async def _apply_portfolio(mdb, user_id: str, symbol: str, name: str,
                           order_type: str, quantity: int, price: float) -> None:
    now = datetime.now(timezone.utc).isoformat()
    if order_type == "buy":
        existing = await mdb.portfolio.find_one({"user_id": user_id, "symbol": symbol})
        if existing:
            old_qty = existing["quantity"]
            old_avg = existing["avg_price"]
            new_qty = old_qty + quantity
            new_avg = (old_avg * old_qty + price * quantity) / new_qty
            await mdb.portfolio.update_one(
                {"user_id": user_id, "symbol": symbol},
                {"$set": {"quantity": new_qty, "avg_price": new_avg, "updated_at": now}},
            )
        else:
            await mdb.portfolio.insert_one({
                "user_id": user_id, "symbol": symbol, "name": name,
                "quantity": quantity, "avg_price": price,
                "created_at": now, "updated_at": now,
            })
    elif order_type == "sell":
        existing = await mdb.portfolio.find_one({"user_id": user_id, "symbol": symbol})
        if existing:
            new_qty = existing["quantity"] - quantity
            if new_qty <= 0:
                await mdb.portfolio.delete_one({"user_id": user_id, "symbol": symbol})
            else:
                await mdb.portfolio.update_one(
                    {"user_id": user_id, "symbol": symbol},
                    {"$set": {"quantity": new_qty, "updated_at": now}},
                )


@router.post("/orders")
async def place_order(
    body: OrderBody,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    now = datetime.now(timezone.utc).isoformat()
    await mdb.orders.insert_one({
        "user_id":    user["id"],
        "symbol":     body.symbol,
        "name":       body.name,
        "order_type": body.order_type,
        "quantity":   body.quantity,
        "price":      body.price,
        "status":     "filled",
        "broker":     body.broker,
        "created_at": now,
    })
    await _apply_portfolio(mdb, user["id"], body.symbol, body.name,
                           body.order_type, body.quantity, body.price)
    return {"ok": True, "status": "filled"}


@router.get("/orders")
async def order_history(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    cursor = mdb.orders.find({"user_id": user["id"]}).sort("created_at", -1).limit(200)
    orders = []
    async for doc in cursor:
        doc.pop("_id", None)
        orders.append(doc)
    return {"orders": orders}


# ── 증권사 API 설정 (MongoDB) ─────────────────────────────────────────

class BrokerSettingsBody(BaseModel):
    """브로커 설정 저장용 입력 모델.

    legacy 프론트(iapi)에서 broker_type/paper_trading 키를 보내므로
    alias를 통해 신규 키(broker/paper)와 함께 병행 지원한다.
    """

    # 레거시/신규 프론트 혼재 환경에서 미사용 필드가 들어와도 저장 API가 깨지지 않도록 무시.
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # 레거시 프론트(iapi 영역)의 broker_type/paper_trading 페이로드를 계속 수용.
    broker: str = Field(default=DEFAULT_BROKER, alias="broker_type")
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    paper: bool = Field(default=True, alias="paper_trading")


class QuantSettingsBody(BaseModel):
    """퀀트 자동매매 설정 저장용 입력 모델."""

    model_config = ConfigDict(extra="ignore")

    mode: str = Field(default="paper", description="paper | live")
    broker: str = DEFAULT_BROKER
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    symbol_source: str = Field(default="ai", description="ai | manual")
    selected_symbols: list[str] = Field(default_factory=list)
    ai_top_n: int = Field(default=3, ge=1, le=5)
    per_trade_budget: float = Field(default=1_000_000, ge=10_000, le=10_000_000)
    buy_ratio: float = Field(default=1.0, ge=0.1, le=1.0)
    sell_ratio: float = Field(default=0.5, ge=0.1, le=1.0)


@router.get("/broker/catalog")
async def broker_catalog():
    return {"brokers": get_broker_catalog()}


@router.post("/broker/settings")
async def save_broker_settings(
    body: BrokerSettingsBody,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    broker = (body.broker or DEFAULT_BROKER).strip().lower()
    if broker not in get_broker_codes():
        raise HTTPException(422, f"지원하지 않는 broker: {broker}")

    now = datetime.now(timezone.utc).isoformat()
    await mdb.broker_settings.update_one(
        {"user_id": user["id"]},
        {"$set": {
            "broker":     broker,
            "app_key":    body.app_key,
            "app_secret": body.app_secret,
            "account_no": body.account_no,
            "paper":      body.paper,
            "updated_at": now,
        }},
        upsert=True,
    )
    return {"ok": True}


@router.get("/broker/settings")
async def get_broker_settings(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    catalog = get_broker_catalog()
    doc = await mdb.broker_settings.find_one({"user_id": user["id"]})
    if not doc:
        return {
            "broker": DEFAULT_BROKER,
            "connected": False,
            "account_no": "",
            "paper": True,
            "brokers": catalog,
        }
    key = doc.get("app_key", "")
    masked = key[:4] + "****" if key else ""
    return {
        "broker":     doc.get("broker", DEFAULT_BROKER),
        "connected":  bool(key),
        "app_key":    masked,
        "account_no": doc.get("account_no", ""),
        "paper":      doc.get("paper", True),
        "brokers": catalog,
    }


@router.get("/quant/settings")
async def get_quant_settings(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    catalog = get_broker_catalog()
    stocks = QUANT_STOCKS
    doc = await mdb.broker_settings.find_one({"user_id": user["id"]}) or {}
    selected = doc.get("quant_selected_symbols", [])
    if not isinstance(selected, list):
        selected = []

    mode = "paper"
    if doc.get("quant_mode") in ("paper", "live"):
        mode = doc["quant_mode"]
    elif doc.get("paper") is False:
        mode = "live"

    source = doc.get("quant_symbol_source", "ai")
    if source not in ("ai", "manual"):
        source = "ai"

    key = doc.get("app_key", "")
    masked = key[:4] + "****" if key else ""

    return {
        "mode": mode,
        "broker": doc.get("broker", DEFAULT_BROKER),
        "connected": bool(key),
        "app_key": masked,
        "account_no": doc.get("account_no", ""),
        "paper": mode == "paper",
        "symbol_source": source,
        "selected_symbols": selected,
        "ai_top_n": int(doc.get("quant_ai_top_n", 3)),
        "per_trade_budget": float(doc.get("quant_per_trade_budget", 1_000_000)),
        "buy_ratio": float(doc.get("quant_buy_ratio", 1.0)),
        "sell_ratio": float(doc.get("quant_sell_ratio", 0.5)),
        "brokers": catalog,
        "stocks": stocks,
    }


@router.post("/quant/settings")
async def save_quant_settings(
    body: QuantSettingsBody,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    broker = (body.broker or DEFAULT_BROKER).strip().lower()
    if broker not in get_broker_codes():
        raise HTTPException(422, f"지원하지 않는 broker: {broker}")

    mode = (body.mode or "paper").strip().lower()
    if mode not in ("paper", "live"):
        raise HTTPException(422, "mode는 paper 또는 live 이어야 합니다.")

    symbol_source = (body.symbol_source or "ai").strip().lower()
    if symbol_source not in ("ai", "manual"):
        raise HTTPException(422, "symbol_source는 ai 또는 manual 이어야 합니다.")

    valid_symbols = {s["symbol"] for s in QUANT_STOCKS}
    selected = [s for s in (body.selected_symbols or []) if s in valid_symbols]

    now = datetime.now(timezone.utc).isoformat()
    await mdb.broker_settings.update_one(
        {"user_id": user["id"]},
        {"$set": {
            "broker": broker,
            "app_key": body.app_key,
            "app_secret": body.app_secret,
            "account_no": body.account_no,
            "paper": mode == "paper",
            "quant_mode": mode,
            "quant_symbol_source": symbol_source,
            "quant_selected_symbols": selected,
            "quant_ai_top_n": body.ai_top_n,
            "quant_per_trade_budget": body.per_trade_budget,
            "quant_buy_ratio": body.buy_ratio,
            "quant_sell_ratio": body.sell_ratio,
            "updated_at": now,
        }},
        upsert=True,
    )
    return {"ok": True}


async def _get_broker_client(user: dict, mdb):
    doc = await mdb.broker_settings.find_one({"user_id": user["id"]})
    if not doc:
        return get_broker_client("mock")
    return get_broker_client(
        broker     = doc.get("broker", "mock"),
        app_key    = doc.get("app_key", ""),
        app_secret = doc.get("app_secret", ""),
        paper      = doc.get("paper", True),
    )


# ── 증권사 API 실시간 조회 ────────────────────────────────────────────

@router.get("/broker/price")
async def broker_price(
    symbol: str = Query(...),
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    client = await _get_broker_client(user, mdb)
    try:
        info = await client.get_price(symbol)
        return {
            "symbol": info.symbol, "name": info.name,
            "current": info.current, "open": info.open,
            "high": info.high, "low": info.low,
            "volume": info.volume, "change": info.change, "change_pct": info.change_pct,
        }
    except Exception as e:
        raise HTTPException(502, f"증권사 API 오류: {e}")


@router.get("/broker/balance")
async def broker_balance(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    client = await _get_broker_client(user, mdb)
    doc = await mdb.broker_settings.find_one({"user_id": user["id"]}) or {}
    account_no = doc.get("account_no", "")
    try:
        bal = await client.get_balance(account_no)
        return {
            "total_eval": bal.total_eval,
            "total_buy":  bal.total_buy,
            "total_gain": bal.total_gain,
            "holdings": [
                {"symbol": h.symbol, "name": h.name, "quantity": h.quantity,
                 "avg_price": h.avg_price, "current_price": h.current_price,
                 "eval_amount": h.eval_amount, "gain_loss": h.gain_loss,
                 "gain_pct": h.gain_pct}
                for h in bal.holdings
            ],
        }
    except Exception as e:
        raise HTTPException(502, f"증권사 API 오류: {e}")


@router.get("/broker/ohlcv")
async def broker_ohlcv(
    symbol: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    client = await _get_broker_client(user, mdb)
    try:
        rows = await client.get_daily_ohlcv(symbol, start, end)
        return {"candles": rows}
    except Exception as e:
        raise HTTPException(502, f"증권사 API 오류: {e}")


class BrokerOrderBody(BaseModel):
    symbol:   str
    side:     str
    quantity: int
    price:    float


@router.post("/broker/order")
async def broker_order(
    body: BrokerOrderBody,
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    client = await _get_broker_client(user, mdb)
    doc = await mdb.broker_settings.find_one({"user_id": user["id"]}) or {}
    account_no = doc.get("account_no", "")

    # ── 매수 주문 시 예수금 사전 확인 ──────────────────────────────────────
    if body.side == "buy":
        bal_err: Exception | None = None
        try:
            bal = await client.get_balance(account_no)
        except Exception as e:
            # 잔고 조회 실패 시 주문은 계속 진행 (경고 로그만)
            logging.getLogger(__name__).warning("잔고 조회 실패 (주문 진행): %s", e)
            bal_err = e

        if bal_err is None:
            required = body.price * body.quantity
            if bal.cash < required:
                await notification.notify_insufficient_funds(
                    symbol    = body.symbol,
                    side      = body.side,
                    quantity  = body.quantity,
                    price     = body.price,
                    required  = required,
                    available = bal.cash,
                    user_id   = user["id"],
                )
                raise HTTPException(
                    422,
                    f"예수금 부족: 필요 {required:,.0f}원 / 가용 {bal.cash:,.0f}원",
                )

    try:
        result = await client.place_order(account_no, body.symbol, body.side,
                                          body.quantity, body.price)
        await notification.notify_order_placed(
            symbol   = body.symbol,
            side     = body.side,
            quantity = body.quantity,
            price    = body.price,
            user_id  = user["id"],
        )
        return {"ok": True, "result": result}
    except Exception as e:
        await notification.notify_order_error(
            symbol   = body.symbol,
            side     = body.side,
            quantity = body.quantity,
            price    = body.price,
            error    = str(e),
            user_id  = user["id"],
        )
        raise HTTPException(502, f"증권사 API 주문 오류: {e}")


@router.get("/broker/test")
async def broker_test(
    user=Depends(get_current_user),
    mdb=Depends(get_mdb),
):
    """증권사 연결 테스트용 간단 시세 조회."""
    client = await _get_broker_client(user, mdb)
    try:
        info = await client.get_price("005930.KS")
        return {"ok": True, "broker_price": {"symbol": info.symbol, "current": info.current}}
    except Exception as e:
        raise HTTPException(502, f"증권사 API 연결 테스트 오류: {e}")


# ── 자동매매 제어 ─────────────────────────────────────────────────────

@router.post("/auto-trade/start")
async def start_auto_trade(user=Depends(get_current_user)):
    started = auto_trade.start_auto_trade(user.get("id", "quant_system"))
    return {"ok": True, "started": started}


@router.post("/auto-trade/stop")
async def stop_auto_trade(user=Depends(get_current_user)):
    stopped = auto_trade.stop_auto_trade()
    return {"ok": True, "stopped": stopped}


@router.get("/auto-trade/status")
async def auto_trade_status(user=Depends(get_current_user)):
    return auto_trade.get_status()


@router.post("/quant/auto/start")
async def quant_auto_start(user=Depends(get_current_user)):
    """기존 프론트 호환 경로."""
    started = auto_trade.start_auto_trade(user.get("id", "quant_system"))
    return {"ok": True, "started": started}


@router.post("/quant/auto/stop")
async def quant_auto_stop(user=Depends(get_current_user)):
    """기존 프론트 호환 경로."""
    stopped = auto_trade.stop_auto_trade()
    return {"ok": True, "stopped": stopped}


@router.get("/quant/auto/status")
async def quant_auto_status(user=Depends(get_current_user)):
    """기존 프론트 호환 경로."""
    return {
        "running": auto_trade.is_running(),
        "logs": [],
        "signals": [],
        "message": "자동매매 상세 로그는 /api/auto-trade/status에서 확인하세요.",
    }


@router.get("/quant/pipeline")
async def quant_pipeline_indicator_backtest(
    symbol: str = Query("005930.KS"),
    period: str = Query("10y"),
    short: int = Query(5, ge=2, le=30),
    mid: int = Query(20, ge=3, le=120),
    rsi: int = Query(14, ge=5, le=40),
    buy_th: float = Query(35.0, ge=5.0, le=50.0),
    _user=Depends(get_current_user),
):
    """커스텀 인디케이터 실백테스트."""
    candle_data = await get_candles(symbol, period=period, interval="1d")
    candles = candle_data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"종목 데이터 없음: {symbol}")
    result = backtest_custom_indicator(
        candles=candles,
        short_window=short,
        mid_window=mid,
        rsi_period=rsi,
        buy_threshold=buy_th,
    )
    if "error" in result:
        raise HTTPException(422, result["error"])
    result["symbol"] = symbol
    result["period"] = period
    return result
