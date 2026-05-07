"""
한국투자증권 (KIS) Open API 클라이언트
공식 문서: https://apiportal.koreainvestment.com/
모의투자 지원: base_url = https://openapivts.koreainvestment.com:29443
실전투자:     base_url = https://openapi.koreainvestment.com:9443
"""
import httpx
from datetime import datetime, timezone
from .base import BrokerClient, TokenInfo, PriceInfo, AccountBalance, BalanceItem

REAL_URL  = "https://openapi.koreainvestment.com:9443"
PAPER_URL = "https://openapivts.koreainvestment.com:29443"


class KISClient(BrokerClient):
    def __init__(self, app_key: str, app_secret: str, paper: bool = True):
        self.app_key    = app_key
        self.app_secret = app_secret
        self.base_url   = PAPER_URL if paper else REAL_URL
        self.paper      = paper
        self._token: str | None = None
        self._token_exp: datetime | None = None

    def _headers(self, tr_id: str, extra: dict | None = None) -> dict:
        h = {
            "content-type":   "application/json; charset=utf-8",
            "authorization":  f"Bearer {self._token}",
            "appkey":         self.app_key,
            "appsecret":      self.app_secret,
            "tr_id":          tr_id,
            "custtype":       "P",
        }
        if extra:
            h.update(extra)
        return h

    async def get_token(self) -> TokenInfo:
        async with httpx.AsyncClient(verify=False, timeout=10) as cli:
            r = await cli.post(
                f"{self.base_url}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey":     self.app_key,
                    "appsecret":  self.app_secret,
                },
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
        # 6자리 코드 (005930) → KIS는 종목코드만
        code = symbol.replace(".KS", "").replace(".KQ", "")
        async with httpx.AsyncClient(verify=False, timeout=10) as cli:
            r = await cli.get(
                f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self._headers("FHKST01010100"),
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            )
            r.raise_for_status()
            o = r.json()["output"]
        return PriceInfo(
            symbol    = symbol,
            name      = o.get("hts_kor_isnm", ""),
            current   = float(o.get("stck_prpr", 0)),
            open      = float(o.get("stck_oprc", 0)),
            high      = float(o.get("stck_hgpr", 0)),
            low       = float(o.get("stck_lwpr", 0)),
            volume    = int(o.get("acml_vol", 0)),
            change    = float(o.get("prdy_vrss", 0)),
            change_pct= float(o.get("prdy_ctrt", 0)),
        )

    async def get_balance(self, account_no: str) -> AccountBalance:
        await self._ensure_token()
        cano, acnt_prdt = account_no[:8], account_no[8:]
        tr_id = "VTTC8434R" if self.paper else "TTTC8434R"
        async with httpx.AsyncClient(verify=False, timeout=10) as cli:
            r = await cli.get(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance",
                headers=self._headers(tr_id),
                params={
                    "CANO":               cano,
                    "ACNT_PRDT_CD":       acnt_prdt,
                    "AFHR_FLPR_YN":       "N",
                    "OFL_YN":             "",
                    "INQR_DVSN":          "02",
                    "UNPR_DVSN":          "01",
                    "FUND_STTL_ICLD_YN":  "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN":          "01",
                    "CTX_AREA_FK100":     "",
                    "CTX_AREA_NK100":     "",
                },
            )
            r.raise_for_status()
            data = r.json()

        holdings = []
        for h in data.get("output1", []):
            qty = int(h.get("hldg_qty", 0))
            if qty <= 0:
                continue
            avg   = float(h.get("pchs_avg_pric", 0))
            curr  = float(h.get("prpr", 0))
            eval_ = float(h.get("evlu_amt", 0))
            gain  = float(h.get("evlu_pfls_amt", 0))
            pct   = float(h.get("evlu_pfls_rt", 0))
            holdings.append(BalanceItem(
                symbol        = h.get("pdno", ""),
                name          = h.get("prdt_name", ""),
                quantity      = qty,
                avg_price     = avg,
                current_price = curr,
                eval_amount   = eval_,
                gain_loss     = gain,
                gain_pct      = pct,
            ))

        s2 = data.get("output2", [{}])[0]
        return AccountBalance(
            total_eval = float(s2.get("tot_evlu_amt", 0)),
            total_buy  = float(s2.get("pchs_amt_smtl_amt", 0)),
            total_gain = float(s2.get("evlu_pfls_smtl_amt", 0)),
            holdings   = holdings,
        )

    async def place_order(
        self, account_no: str, symbol: str, side: str, quantity: int, price: float
    ) -> dict:
        await self._ensure_token()
        cano, acnt_prdt = account_no[:8], account_no[8:]
        code = symbol.replace(".KS", "").replace(".KQ", "")
        if side == "buy":
            tr_id = "VTTC0802U" if self.paper else "TTTC0802U"
        else:
            tr_id = "VTTC0801U" if self.paper else "TTTC0801U"

        async with httpx.AsyncClient(verify=False, timeout=10) as cli:
            r = await cli.post(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._headers(tr_id),
                json={
                    "CANO":         cano,
                    "ACNT_PRDT_CD": acnt_prdt,
                    "PDNO":         code,
                    "ORD_DVSN":     "00",   # 지정가
                    "ORD_QTY":      str(quantity),
                    "ORD_UNPR":     str(int(price)),
                },
            )
            r.raise_for_status()
        return r.json()

    async def get_daily_ohlcv(self, symbol: str, start: str, end: str) -> list[dict]:
        """일봉 OHLCV. start/end: YYYYMMDD"""
        await self._ensure_token()
        code = symbol.replace(".KS", "").replace(".KQ", "")
        async with httpx.AsyncClient(verify=False, timeout=10) as cli:
            r = await cli.get(
                f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
                headers=self._headers("FHKST01010400"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":          code,
                    "FID_PERIOD_DIV_CODE":     "D",
                    "FID_ORG_ADJ_PRC":         "0",
                    "FID_INPUT_DATE_1":         start,
                    "FID_INPUT_DATE_2":         end,
                },
            )
            r.raise_for_status()
        out = []
        for row in r.json().get("output", []):
            out.append({
                "date":   row.get("stck_bsop_date", ""),
                "open":   float(row.get("stck_oprc", 0)),
                "high":   float(row.get("stck_hgpr", 0)),
                "low":    float(row.get("stck_lwpr", 0)),
                "close":  float(row.get("stck_clpr", 0)),
                "volume": int(row.get("acml_vol", 0)),
            })
        return out
