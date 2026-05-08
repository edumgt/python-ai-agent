"""10분 주기 자동매매 Agentic AI - MongoDB 기반."""
import asyncio
from datetime import datetime, timezone
from app.services.stock import get_quant_indicators, QUANT_STOCKS
from app.database.mongo import get_mongo_db
from app.config import settings

_auto_trade_task: asyncio.Task | None = None
_trade_log: list[dict] = []
_is_running = False
_INTERVAL_SEC = 600


def get_status() -> dict:
    return {
        "running":      _is_running,
        "interval_sec": _INTERVAL_SEC,
        "log":          _trade_log[-50:],
    }


def is_running() -> bool:
    """자동매매 루프 실행 상태만 반환."""
    return _is_running


async def _execute_virtual_trade(
    mdb,
    user_id: str,
    symbol: str,
    name: str,
    action: str,
    price: float,
    quantity: int,
    reason: str,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    await mdb.orders.insert_one({
        "user_id":    user_id,
        "symbol":     symbol,
        "name":       name,
        "order_type": action,
        "quantity":   quantity,
        "price":      price,
        "status":     "filled",
        "broker":     "quant_ai",
        "created_at": now,
    })

    if action == "buy":
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
    elif action == "sell":
        existing = await mdb.portfolio.find_one({"user_id": user_id, "symbol": symbol})
        if existing:
            new_qty = max(0, existing["quantity"] - quantity)
            if new_qty == 0:
                await mdb.portfolio.delete_one({"user_id": user_id, "symbol": symbol})
            else:
                await mdb.portfolio.update_one(
                    {"user_id": user_id, "symbol": symbol},
                    {"$set": {"quantity": new_qty, "updated_at": now}},
                )

    return {
        "time": now, "symbol": symbol, "name": name,
        "action": action, "quantity": quantity, "price": price, "reason": reason,
    }


async def _run_quant_cycle(user_id: str = "quant_system") -> None:
    global _trade_log
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cycle_log: dict = {"time": now_str, "trades": [], "signals": []}

    try:
        mdb = get_mongo_db()
    except Exception:
        return  # MongoDB 미연결 시 스킵

    for stock in QUANT_STOCKS:
        try:
            indicators = await get_quant_indicators(stock["symbol"], "2y")
            signal = indicators.get("signal", {})
            price = indicators.get("current_price")
            if not price:
                continue

            action = signal.get("action", "관망")
            reasons = signal.get("reasons", [])
            score = signal.get("score", 0)

            cycle_log["signals"].append({
                "symbol": stock["symbol"], "name": stock["name"],
                "price": price, "action": action, "score": score,
            })

            if action in ("강력 매수", "매수"):
                qty = max(1, int(1_000_000 / price))
                trade = await _execute_virtual_trade(
                    mdb, user_id, stock["symbol"], stock["name"],
                    "buy", price, qty, " | ".join(reasons),
                )
                cycle_log["trades"].append({**trade, "type": "auto"})

            elif action in ("강력 매도", "매도"):
                existing = await mdb.portfolio.find_one(
                    {"user_id": user_id, "symbol": stock["symbol"]}
                )
                if existing and existing["quantity"] > 0:
                    qty = max(1, existing["quantity"] // 2)
                    trade = await _execute_virtual_trade(
                        mdb, user_id, stock["symbol"], stock["name"],
                        "sell", price, qty, " | ".join(reasons),
                    )
                    cycle_log["trades"].append({**trade, "type": "auto"})

        except Exception as e:
            cycle_log["signals"].append({"symbol": stock["symbol"], "error": str(e)})

    _trade_log.append(cycle_log)
    if len(_trade_log) > 100:
        _trade_log = _trade_log[-100:]


async def _auto_trade_loop() -> None:
    global _is_running
    _is_running = True
    try:
        while True:
            await _run_quant_cycle()
            await asyncio.sleep(_INTERVAL_SEC)
    except asyncio.CancelledError:
        pass
    finally:
        _is_running = False


def start_auto_trade() -> bool:
    global _auto_trade_task, _is_running
    if _auto_trade_task and not _auto_trade_task.done():
        return False
    _auto_trade_task = asyncio.create_task(_auto_trade_loop())
    return True


def stop_auto_trade() -> bool:
    global _auto_trade_task
    if _auto_trade_task and not _auto_trade_task.done():
        _auto_trade_task.cancel()
        return True
    return False
