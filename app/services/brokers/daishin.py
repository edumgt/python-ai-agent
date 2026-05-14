"""대신증권 클라이언트 (Windows COM 제약 안내용)."""

import os
from .mock import MockBrokerClient


class DaishinClient(MockBrokerClient):
    def __init__(self, app_key: str, app_secret: str):
        super().__init__()
        self.app_key = app_key
        self.app_secret = app_secret

    def _ensure_supported(self) -> None:
        if os.name != "nt":
            raise RuntimeError(
                "대신증권 CYBOS Plus(COM)는 Windows 전용입니다. "
                "Windows 브리지 서버 또는 Windows 환경에서 실행하세요."
            )

    async def get_token(self):
        self._ensure_supported()
        return await super().get_token()

    async def get_price(self, symbol: str):
        self._ensure_supported()
        return await super().get_price(symbol)

    async def get_balance(self, account_no: str):
        self._ensure_supported()
        return await super().get_balance(account_no)

    async def place_order(self, account_no, symbol, side, quantity, price):
        self._ensure_supported()
        return await super().place_order(account_no, symbol, side, quantity, price)

    async def get_daily_ohlcv(self, symbol: str, start: str, end: str):
        self._ensure_supported()
        return await super().get_daily_ohlcv(symbol, start, end)
