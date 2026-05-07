"""10분 주기 자동매매 Agentic AI Mockup."""
import asyncio
import json
from datetime import datetime, timezone
from typing import Any
import aiosqlite
from app.services.stock import get_quant_indicators, QUANT_STOCKS
from app.config import settings

# 자동매매 상태
_auto_trade_task: asyncio.Task | None = None
_trade_log: list[dict] = []
_is_running = False
_INTERVAL_SEC = 600  # 10분


def get_status() -> dict:
    return {
        "running": _is_running,
        "interval_sec": _INTERVAL_SEC,
        "log": _trade_log[-50:],  # 최근 50건
    }


async def _execute_virtual_trade(
    db: aiosqlite.Connection,
    mongo_user_id: str,
    symbol: str,
    name: str,
    action: str,
    price: float,
    quantity: int,
    reason: str,
) -> dict:
    """가상 주문 실행 및 포트폴리오 업데이트."""
    now = datetime.now(timezone.utc).isoformat()

    # 주문 기록
    await db.execute(
        "INSERT INTO orders (mongo_user_id, symbol, name, order_type, quantity, price, status, broker, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'filled', 'quant_ai', ?)",
        (mongo_user_id, symbol, name, action, quantity, price, now),
    )

    # 포트폴리오 업데이트
    if action == "buy":
        existing = await (await db.execute(
            "SELECT quantity, avg_price FROM portfolio WHERE mongo_user_id=? AND symbol=?",
            (mongo_user_id, symbol)
        )).fetchone()
        if existing:
            old_qty = existing["quantity"]
            old_avg = existing["avg_price"]
            new_qty = old_qty + quantity
            new_avg = (old_avg * old_qty + price * quantity) / new_qty
            await db.execute(
                "UPDATE portfolio SET quantity=?, avg_price=?, updated_at=? "
                "WHERE mongo_user_id=? AND symbol=?",
                (new_qty, new_avg, now, mongo_user_id, symbol),
            )
        else:
            await db.execute(
                "INSERT INTO portfolio (mongo_user_id, symbol, name, quantity, avg_price, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mongo_user_id, symbol, name, quantity, price, now, now),
            )
    elif action == "sell":
        existing = await (await db.execute(
            "SELECT quantity FROM portfolio WHERE mongo_user_id=? AND symbol=?",
            (mongo_user_id, symbol)
        )).fetchone()
        if existing:
            new_qty = max(0, existing["quantity"] - quantity)
            if new_qty == 0:
                await db.execute(
                    "DELETE FROM portfolio WHERE mongo_user_id=? AND symbol=?",
                    (mongo_user_id, symbol),
                )
            else:
                await db.execute(
                    "UPDATE portfolio SET quantity=?, updated_at=? "
                    "WHERE mongo_user_id=? AND symbol=?",
                    (new_qty, now, mongo_user_id, symbol),
                )

    await db.commit()
    return {
        "time": now, "symbol": symbol, "name": name,
        "action": action, "quantity": quantity, "price": price, "reason": reason,
    }


async def _run_quant_cycle(db_path: str, mongo_user_id: str = "quant_system") -> None:
    """한 사이클: 5개 종목 분석 + 자동매매."""
    global _trade_log
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cycle_log = {"time": now_str, "trades": [], "signals": []}

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
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
                    "symbol": stock["symbol"],
                    "name": stock["name"],
                    "price": price,
                    "action": action,
                    "score": score,
                })

                # 매수: 강력 매수 또는 매수 신호
                if action in ("강력 매수", "매수") and price:
                    qty = max(1, int(1_000_000 / price))  # 100만원 단위
                    trade = await _execute_virtual_trade(
                        db, mongo_user_id, stock["symbol"], stock["name"],
                        "buy", price, qty, " | ".join(reasons),
                    )
                    cycle_log["trades"].append({**trade, "type": "auto"})

                # 매도: 강력 매도 신호
                elif action in ("강력 매도", "매도") and price:
                    existing = await (await db.execute(
                        "SELECT quantity FROM portfolio WHERE mongo_user_id=? AND symbol=?",
                        (mongo_user_id, stock["symbol"])
                    )).fetchone()
                    if existing and existing["quantity"] > 0:
                        qty = max(1, existing["quantity"] // 2)  # 절반 매도
                        trade = await _execute_virtual_trade(
                            db, mongo_user_id, stock["symbol"], stock["name"],
                            "sell", price, qty, " | ".join(reasons),
                        )
                        cycle_log["trades"].append({**trade, "type": "auto"})

            except Exception as e:
                cycle_log["signals"].append({"symbol": stock["symbol"], "error": str(e)})

    _trade_log.append(cycle_log)
    if len(_trade_log) > 100:
        _trade_log = _trade_log[-100:]


async def _auto_trade_loop(db_path: str) -> None:
    global _is_running
    _is_running = True
    try:
        while True:
            await _run_quant_cycle(db_path)
            await asyncio.sleep(_INTERVAL_SEC)
    except asyncio.CancelledError:
        pass
    finally:
        _is_running = False


def start_auto_trade() -> bool:
    global _auto_trade_task, _is_running
    if _auto_trade_task and not _auto_trade_task.done():
        return False  # already running
    _auto_trade_task = asyncio.create_task(
        _auto_trade_loop(settings.SQLITE_PATH)
    )
    return True


def stop_auto_trade() -> bool:
    global _auto_trade_task
    if _auto_trade_task and not _auto_trade_task.done():
        _auto_trade_task.cancel()
        return True
    return False
