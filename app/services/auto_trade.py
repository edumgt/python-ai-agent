"""10분 주기 자동매매 Agentic AI - MongoDB 기반."""
import asyncio
import logging
from datetime import datetime, timezone
from app.services.stock import get_quant_indicators, QUANT_STOCKS
from app.database.mongo import get_mongo_db
from app.config import settings
from app.services import notification

logger = logging.getLogger(__name__)

_auto_trade_task: asyncio.Task | None = None
_trade_log: list[dict] = []
_is_running = False
_INTERVAL_SEC = 600
_INITIAL_CAPITAL = 10_000_000


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
    account = await mdb.quant_virtual_accounts.find_one({"user_id": user_id})
    if not account:
        account = {
            "user_id": user_id,
            "initial_capital": float(_INITIAL_CAPITAL),
            "cash_balance": float(_INITIAL_CAPITAL),
            "created_at": now,
            "updated_at": now,
        }
        await mdb.quant_virtual_accounts.insert_one(account)

    cash_balance = float(account.get("cash_balance", _INITIAL_CAPITAL))
    executed_quantity = quantity

    if action == "buy":
        cost = price * quantity
        if cash_balance < cost:
            max_qty = int(cash_balance // price) if price > 0 else 0
            if max_qty <= 0:
                shortfall = cost - cash_balance
                return {
                    "time": now, "symbol": symbol, "name": name,
                    "action": action, "quantity": 0, "price": price,
                    "reason": (
                        f"{reason} | 잔고 부족으로 미체결 "
                        f"(필요 {cost:,.0f}원 / 부족 {shortfall:,.0f}원 / 가용현금 {cash_balance:,.0f}원)"
                    ),
                    "status": "skipped",
                    "cash_balance": round(cash_balance, 2),
                }
            executed_quantity = max_qty

    if action == "sell":
        existing = await mdb.portfolio.find_one({"user_id": user_id, "symbol": symbol})
        if not existing or existing.get("quantity", 0) <= 0:
            return {
                "time": now, "symbol": symbol, "name": name,
                "action": action, "quantity": 0, "price": price,
                "reason": f"{reason} | 보유 수량 없음",
                "status": "skipped",
                "cash_balance": round(cash_balance, 2),
            }
        executed_quantity = min(quantity, int(existing["quantity"]))

    await mdb.orders.insert_one({
        "user_id":    user_id,
        "symbol":     symbol,
        "name":       name,
        "order_type": action,
        "quantity":   executed_quantity,
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
            new_qty = old_qty + executed_quantity
            new_avg = (old_avg * old_qty + price * executed_quantity) / new_qty
            await mdb.portfolio.update_one(
                {"user_id": user_id, "symbol": symbol},
                {"$set": {"quantity": new_qty, "avg_price": new_avg, "updated_at": now}},
            )
        else:
            await mdb.portfolio.insert_one({
                "user_id": user_id, "symbol": symbol, "name": name,
                "quantity": executed_quantity, "avg_price": price,
                "created_at": now, "updated_at": now,
            })
        cash_balance -= price * executed_quantity
    elif action == "sell":
        existing = await mdb.portfolio.find_one({"user_id": user_id, "symbol": symbol})
        if existing:
            new_qty = max(0, existing["quantity"] - executed_quantity)
            if new_qty == 0:
                await mdb.portfolio.delete_one({"user_id": user_id, "symbol": symbol})
            else:
                await mdb.portfolio.update_one(
                    {"user_id": user_id, "symbol": symbol},
                    {"$set": {"quantity": new_qty, "updated_at": now}},
                )
        cash_balance += price * executed_quantity

    await mdb.quant_virtual_accounts.update_one(
        {"user_id": user_id},
        {"$set": {"cash_balance": cash_balance, "updated_at": now}},
        upsert=True,
    )

    return {
        "time": now, "symbol": symbol, "name": name,
        "action": action, "quantity": executed_quantity, "price": price, "reason": reason,
        "status": "filled",
        "cash_balance": round(cash_balance, 2),
    }


async def _run_quant_cycle(user_id: str = "quant_system") -> None:
    global _trade_log
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cycle_log: dict = {"time": now_str, "trades": [], "signals": []}

    try:
        mdb = get_mongo_db()
    except Exception:
        return  # MongoDB 미연결 시 스킵

    now_iso = datetime.now(timezone.utc).isoformat()
    await mdb.quant_virtual_accounts.update_one(
        {"user_id": user_id},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "initial_capital": float(_INITIAL_CAPITAL),
                "cash_balance": float(_INITIAL_CAPITAL),
                "created_at": now_iso,
            },
            "$set": {"updated_at": now_iso},
        },
        upsert=True,
    )

    price_map: dict[str, float] = {}

    for stock in QUANT_STOCKS:
        try:
            indicators = await get_quant_indicators(stock["symbol"], "2y")
            signal = indicators.get("signal", {})
            price = indicators.get("current_price")
            if not price:
                continue
            price_map[stock["symbol"]] = float(price)

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
                if trade.get("status") == "filled":
                    await notification.notify_auto_trade_executed(
                        symbol   = stock["symbol"],
                        name     = stock["name"],
                        action   = "buy",
                        quantity = trade.get("quantity", qty),
                        price    = price,
                        reason   = " | ".join(reasons),
                    )

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
                    if trade.get("status") == "filled":
                        await notification.notify_auto_trade_executed(
                            symbol   = stock["symbol"],
                            name     = stock["name"],
                            action   = "sell",
                            quantity = trade.get("quantity", qty),
                            price    = price,
                            reason   = " | ".join(reasons),
                        )

        except Exception as e:
            cycle_log["signals"].append({"symbol": stock["symbol"], "error": str(e)})

    account = await mdb.quant_virtual_accounts.find_one({"user_id": user_id}) or {}
    cash_balance = float(account.get("cash_balance", _INITIAL_CAPITAL))
    holdings_value = 0.0
    async for p in mdb.portfolio.find({"user_id": user_id}):
        qty = float(p.get("quantity", 0))
        if qty <= 0:
            continue
        symbol = p.get("symbol", "")
        if symbol not in price_map:
            logger.warning("현재가 미수신되어 평균단가 사용: user=%s symbol=%s", user_id, symbol)
        mark_price = float(price_map.get(symbol, p.get("avg_price", 0)))
        holdings_value += qty * mark_price

    total_equity = cash_balance + holdings_value
    initial_capital = float(account.get("initial_capital", _INITIAL_CAPITAL))
    pnl_pct = round((total_equity / initial_capital - 1) * 100, 2) if initial_capital > 0 else None
    cycle_log["account"] = {
        "initial_capital": initial_capital,
        "cash_balance": round(cash_balance, 2),
        "holdings_value": round(holdings_value, 2),
        "total_equity": round(total_equity, 2),
        "pnl_pct": pnl_pct,
    }

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
    asyncio.create_task(notification.notify_auto_trade_started())
    return True


def stop_auto_trade() -> bool:
    global _auto_trade_task
    if _auto_trade_task and not _auto_trade_task.done():
        _auto_trade_task.cancel()
        asyncio.create_task(notification.notify_auto_trade_stopped())
        return True
    return False
