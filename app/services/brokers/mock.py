"""
증권사 API 연결 실패 시 Mockup 클라이언트 (개발/테스트용)
실제 API 키 없이도 UI 테스트 가능.
"""
import random
from datetime import date, timedelta
from .base import BrokerClient, TokenInfo, PriceInfo, AccountBalance, BalanceItem

_NAMES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "005380": "현대자동차",
    "051910": "LG화학",
}
_PRICES = {
    "005930": 76500, "000660": 158000, "035420": 178000,
    "005380": 234000, "051910": 312000,
}


class MockBrokerClient(BrokerClient):
    """API 키 없이 Mockup 데이터 반환 – 개발/데모 전용."""

    async def get_token(self) -> TokenInfo:
        return TokenInfo(access_token="mock-token-xxxx", expires_in=86400)

    async def get_price(self, symbol: str) -> PriceInfo:
        code  = symbol.replace(".KS", "").replace(".KQ", "")
        base  = _PRICES.get(code, 50000)
        noise = random.uniform(-0.02, 0.02)
        curr  = round(base * (1 + noise))
        chg   = curr - base
        return PriceInfo(
            symbol     = symbol,
            name       = _NAMES.get(code, code),
            current    = curr,
            open       = round(base * random.uniform(0.99, 1.01)),
            high       = round(base * random.uniform(1.00, 1.025)),
            low        = round(base * random.uniform(0.975, 1.00)),
            volume     = random.randint(5_000_000, 20_000_000),
            change     = chg,
            change_pct = round(chg / base * 100, 2),
        )

    async def get_balance(self, account_no: str) -> AccountBalance:
        holdings = [
            BalanceItem("005930.KS", "삼성전자",  10, 73000, 76500, 765000,  35000, 4.79),
            BalanceItem("000660.KS", "SK하이닉스", 5, 145000, 158000, 790000, 65000, 8.97),
        ]
        return AccountBalance(
            total_eval = 1_555_000,
            total_buy  = 1_455_000,
            total_gain = 100_000,
            holdings   = holdings,
        )

    async def place_order(self, account_no, symbol, side, quantity, price) -> dict:
        return {
            "rt_cd":  "0",
            "msg_cd": "MOCK",
            "msg1":   f"[MOCK] {side.upper()} {symbol} {quantity}주 @{price:,}",
            "output": {"KRX_FWDG_ORD_ORGNO": "MOCK001", "ORNO": "MOCK12345"},
        }

    async def get_daily_ohlcv(self, symbol: str, start: str, end: str) -> list[dict]:
        code  = symbol.replace(".KS", "").replace(".KQ", "")
        base  = _PRICES.get(code, 50000)
        rows  = []
        d     = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}")
        e     = date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}")
        price = base * 0.7
        while d <= e:
            if d.weekday() < 5:
                pct   = random.uniform(-0.025, 0.03)
                close = round(price * (1 + pct))
                rows.append({
                    "date":   d.strftime("%Y%m%d"),
                    "open":   round(price),
                    "high":   round(price * random.uniform(1.00, 1.03)),
                    "low":    round(price * random.uniform(0.97, 1.00)),
                    "close":  close,
                    "volume": random.randint(5_000_000, 20_000_000),
                })
                price = close
            d += timedelta(days=1)
        return rows
