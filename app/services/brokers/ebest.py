"""
LS증권 eBest Open API 클라이언트
공식 문서: https://openapi.ebestsec.co.kr/
모의투자: base_url = https://openapi.ebestsec.co.kr/
실전투자: base_url = https://openapi.ebestsec.co.kr/
"""
import httpx
from .base import BrokerClient, TokenInfo, PriceInfo, AccountBalance, BalanceItem

BASE_URL = "https://openapi.ebestsec.co.kr"


class EBestClient(BrokerClient):
    def __init__(self, app_key: str, app_secret: str):
        self.app_key    = app_key
        self.app_secret = app_secret
        self._token: str | None = None

    def _headers(self, tr_cd: str, content_type: str = "application/json") -> dict:
        return {
            "content-type":  content_type,
            "authorization": f"Bearer {self._token}",
            "tr_cd":         tr_cd,
            "tr_cont":       "N",
        }

    async def get_token(self) -> TokenInfo:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(
                f"{BASE_URL}/oauth2/token",
                data={
                    "grant_type":    "client_credentials",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "scope":         "oob",
                },
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
            r.raise_for_status()
            d = r.json()
        self._token = d["access_token"]
        return TokenInfo(access_token=self._token, expires_in=d.get("expires_in", 86400))

    async def _ensure_token(self):
        if not self._token:
            await self.get_token()

    async def get_price(self, symbol: str) -> PriceInfo:
        await self._ensure_token()
        code = symbol.replace(".KS", "").replace(".KQ", "")
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(
                f"{BASE_URL}/stock/sise",
                headers=self._headers("t1102"),
                json={"t1102InBlock": {"shcode": code}},
            )
            r.raise_for_status()
            o = r.json().get("t1102OutBlock", {})
        return PriceInfo(
            symbol     = symbol,
            name       = o.get("hname", ""),
            current    = float(o.get("price", 0)),
            open       = float(o.get("open", 0)),
            high       = float(o.get("high", 0)),
            low        = float(o.get("low", 0)),
            volume     = int(o.get("volume", 0)),
            change     = float(o.get("change", 0)),
            change_pct = float(o.get("drate", 0)),
        )

    async def get_balance(self, account_no: str) -> AccountBalance:
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(
                f"{BASE_URL}/stock/accno",
                headers=self._headers("CSPAQ12300"),
                json={
                    "CSPAQ12300InBlock1": {
                        "RecCnt":      1,
                        "AcntNo":      account_no,
                        "Pwd":         "0000",
                        "BalCreTp":    "1",
                        "CmsnAppTpCode": "1",
                        "D2balBaseQryTp": "0",
                        "UprcTpCode":  "1",
                    }
                },
            )
            r.raise_for_status()
            data = r.json()

        holdings = []
        for h in data.get("CSPAQ12300OutBlock2", []):
            qty = int(h.get("BalQty", 0))
            if qty <= 0:
                continue
            avg   = float(h.get("AvrPrc",  0))
            curr  = float(h.get("CurPrc",  0))
            eval_ = qty * curr
            gain  = eval_ - qty * avg
            pct   = (gain / (qty * avg) * 100) if avg else 0
            holdings.append(BalanceItem(
                symbol        = h.get("IsuNo", ""),
                name          = h.get("IsuNm", ""),
                quantity      = qty,
                avg_price     = avg,
                current_price = curr,
                eval_amount   = eval_,
                gain_loss     = gain,
                gain_pct      = pct,
            ))

        s = data.get("CSPAQ12300OutBlock3", {})
        return AccountBalance(
            total_eval = float(s.get("BalEvalAmt", 0)),
            total_buy  = float(s.get("PchsAmt",    0)),
            total_gain = float(s.get("EvalPnlAmt", 0)),
            holdings   = holdings,
        )

    async def place_order(
        self, account_no: str, symbol: str, side: str, quantity: int, price: float
    ) -> dict:
        await self._ensure_token()
        code    = symbol.replace(".KS", "").replace(".KQ", "")
        buy_sel = "2" if side == "buy" else "1"
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(
                f"{BASE_URL}/stock/order",
                headers=self._headers("CSPAT00601"),
                json={
                    "CSPAT00601InBlock1": {
                        "AcntNo":    account_no,
                        "InptPwd":   "0000",
                        "IsuNo":     code,
                        "OrdQty":    quantity,
                        "OrdPrc":    price,
                        "BnsTpCode": buy_sel,   # 1=매도, 2=매수
                        "OrdprcPtnCode": "00",  # 지정가
                        "MgntrnCode":    "000",
                        "LoanDt":        "",
                        "OrdCndiTpCode": "0",
                    }
                },
            )
            r.raise_for_status()
        return r.json()

    async def get_daily_ohlcv(self, symbol: str, start: str, end: str) -> list[dict]:
        await self._ensure_token()
        code = symbol.replace(".KS", "").replace(".KQ", "")
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(
                f"{BASE_URL}/stock/chart",
                headers=self._headers("t8410"),
                json={
                    "t8410InBlock": {
                        "shcode":   code,
                        "gubun":    "2",    # 일봉
                        "qrycnt":   500,
                        "sdate":    start,
                        "edate":    end,
                        "cts_date": "",
                        "adjustyn": "1",
                    }
                },
            )
            r.raise_for_status()
        out = []
        for row in r.json().get("t8410OutBlock1", []):
            out.append({
                "date":   row.get("date", ""),
                "open":   float(row.get("open", 0)),
                "high":   float(row.get("high", 0)),
                "low":    float(row.get("low",  0)),
                "close":  float(row.get("close", 0)),
                "volume": int(row.get("jdiff_vol", 0)),
            })
        return out
