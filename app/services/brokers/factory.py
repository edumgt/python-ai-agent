"""증권사 클라이언트 팩토리."""
from .base import BrokerClient
from .kis import KISClient
from .ebest import EBestClient
from .mock import MockBrokerClient


def get_broker_client(
    broker: str,
    app_key: str = "",
    app_secret: str = "",
    paper: bool = True,
) -> BrokerClient:
    """
    broker: "kis" | "ebest" | "mock"
    key/secret이 없으면 자동으로 MockBrokerClient 반환.
    """
    if not app_key or not app_secret:
        return MockBrokerClient()
    if broker == "kis":
        return KISClient(app_key, app_secret, paper=paper)
    if broker == "ebest":
        return EBestClient(app_key, app_secret)
    return MockBrokerClient()
