"""증권사 Open API 추상 베이스."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class TokenInfo:
    access_token: str
    expires_in: int  # seconds
    token_type: str = "Bearer"


@dataclass
class PriceInfo:
    symbol: str
    name: str
    current: float
    open: float
    high: float
    low: float
    volume: int
    change: float
    change_pct: float


@dataclass
class BalanceItem:
    symbol: str
    name: str
    quantity: int
    avg_price: float
    current_price: float
    eval_amount: float
    gain_loss: float
    gain_pct: float


@dataclass
class AccountBalance:
    total_eval: float
    total_buy: float
    total_gain: float
    holdings: list[BalanceItem]


class BrokerClient(ABC):
    """모든 증권사 클라이언트의 공통 인터페이스."""

    @abstractmethod
    async def get_token(self) -> TokenInfo: ...

    @abstractmethod
    async def get_price(self, symbol: str) -> PriceInfo: ...

    @abstractmethod
    async def get_balance(self, account_no: str) -> AccountBalance: ...

    @abstractmethod
    async def place_order(
        self, account_no: str, symbol: str, side: str, quantity: int, price: float
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_daily_ohlcv(
        self, symbol: str, start: str, end: str
    ) -> list[dict]: ...
