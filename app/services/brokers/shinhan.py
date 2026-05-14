"""신한투자증권 자동감시주문 연동용 클라이언트 (Mock 기반)."""

from .mock import MockBrokerClient


class ShinhanClient(MockBrokerClient):
    def __init__(self, app_key: str, app_secret: str):
        super().__init__()
        self.app_key = app_key
        self.app_secret = app_secret

    async def place_order(self, account_no, symbol, side, quantity, price) -> dict:
        result = await super().place_order(account_no, symbol, side, quantity, price)
        result["msg1"] = f"[SHINHAN-MOCK] 자동감시주문 시뮬레이션: {side.upper()} {symbol} {quantity}주 @{price:,}"
        result["watch_order"] = True
        return result
