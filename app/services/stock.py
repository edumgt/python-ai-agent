"""주가 데이터 서비스: Yahoo Finance API 기반."""
import httpx
from datetime import datetime, timezone
from typing import Any

YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FinAgent/1.0)"}

# 대표 국내 상장사 5종목 (퀀트 대상)
QUANT_STOCKS = [
    {"symbol": "005930.KS", "name": "삼성전자", "sector": "IT/반도체"},
    {"symbol": "000660.KS", "name": "SK하이닉스", "sector": "IT/반도체"},
    {"symbol": "035420.KS", "name": "NAVER", "sector": "IT/인터넷"},
    {"symbol": "005380.KS", "name": "현대자동차", "sector": "자동차"},
    {"symbol": "051910.KS", "name": "LG화학", "sector": "화학"},
]

MARKET_INDICES = [
    {"symbol": "^KS11", "name": "KOSPI"},
    {"symbol": "^KQ11", "name": "KOSDAQ"},
    {"symbol": "KRW=X", "name": "USD/KRW"},
]


async def _yahoo_chart(symbol: str, interval: str, range_: str) -> dict | None:
    url = f"{YAHOO_CHART}/{symbol}"
    params = {"interval": interval, "range": range_}
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            return result[0] if result else None
        except Exception:
            return None


async def get_quote(symbol: str) -> dict:
    """현재 주가 정보."""
    data = await _yahoo_chart(symbol, "1d", "1d")
    if not data:
        return {"symbol": symbol, "error": "데이터 없음"}

    meta = data.get("meta", {})
    return {
        "symbol": symbol,
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "price": meta.get("regularMarketPrice"),
        "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
        "change": None,
        "change_pct": None,
        "currency": meta.get("currency", "KRW"),
        "market": meta.get("exchangeName"),
    }


async def get_candles(symbol: str, period: str = "1y", interval: str = "1d") -> dict:
    """캔들 차트 데이터 (OHLCV)."""
    # 10년치는 10y range로 요청
    data = await _yahoo_chart(symbol, interval, period)
    if not data:
        return {"symbol": symbol, "candles": []}

    timestamps = data.get("timestamp", [])
    indicators = data.get("indicators", {}).get("quote", [{}])[0]
    opens = indicators.get("open", [])
    highs = indicators.get("high", [])
    lows = indicators.get("low", [])
    closes = indicators.get("close", [])
    volumes = indicators.get("volume", [])

    candles = []
    for i, ts in enumerate(timestamps):
        if i >= len(closes) or closes[i] is None:
            continue
        candles.append({
            "time": ts,
            "open": opens[i] if i < len(opens) else None,
            "high": highs[i] if i < len(highs) else None,
            "low": lows[i] if i < len(lows) else None,
            "close": closes[i],
            "volume": volumes[i] if i < len(volumes) else None,
        })

    return {"symbol": symbol, "interval": interval, "period": period, "candles": candles}


async def get_market_summary() -> list[dict]:
    """시장 지수 요약."""
    results = []
    for idx in MARKET_INDICES:
        q = await get_quote(idx["symbol"])
        prev = q.get("prev_close")
        price = q.get("price")
        change_pct = None
        if prev and price and prev != 0:
            change_pct = round((price - prev) / prev * 100, 2)
        results.append({
            "symbol": idx["symbol"],
            "name": idx["name"],
            "price": price,
            "change_pct": change_pct,
        })
    return results


# ── 기술적 지표 계산 ──────────────────────────────────────────────────
def _calc_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    rsi = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period, len(closes)):
        if i > period:
            diff = closes[i] - closes[i - 1]
            avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
            avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi[i] = round(100 - (100 / (1 + rs)), 2)
    return rsi


def _calc_sma(closes: list[float], period: int) -> list[float | None]:
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = round(sum(closes[i - period + 1:i + 1]) / period, 2)
    return result


def _calc_bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0):
    upper, lower, mid = [None] * len(closes), [None] * len(closes), [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        m = sum(window) / period
        std = (sum((x - m) ** 2 for x in window) / period) ** 0.5
        mid[i] = round(m, 2)
        upper[i] = round(m + std_mult * std, 2)
        lower[i] = round(m - std_mult * std, 2)
    return upper, mid, lower


async def get_quant_indicators(symbol: str, period: str = "2y") -> dict:
    """기술적 지표 + AI 매매 시그널."""
    data = await get_candles(symbol, period=period)
    candles = data.get("candles", [])
    if len(candles) < 20:
        return {"symbol": symbol, "error": "데이터 부족"}

    closes = [c["close"] for c in candles if c["close"] is not None]
    times = [c["time"] for c in candles if c["close"] is not None]

    rsi = _calc_rsi(closes)
    ma5 = _calc_sma(closes, 5)
    ma20 = _calc_sma(closes, 20)
    ma60 = _calc_sma(closes, 60)
    bb_upper, bb_mid, bb_lower = _calc_bollinger(closes)

    # 매매 시그널 (규칙 기반 + AI 해석)
    signal = _generate_signal(closes, rsi, ma5, ma20, ma60, bb_upper, bb_lower)

    return {
        "symbol": symbol,
        "times": times[-100:],
        "closes": closes[-100:],
        "rsi": rsi[-100:],
        "ma5": ma5[-100:],
        "ma20": ma20[-100:],
        "ma60": ma60[-100:],
        "bb_upper": bb_upper[-100:],
        "bb_mid": bb_mid[-100:],
        "bb_lower": bb_lower[-100:],
        "signal": signal,
        "current_price": closes[-1],
        "current_rsi": rsi[-1],
    }


def _generate_signal(closes, rsi, ma5, ma20, ma60, bb_upper, bb_lower) -> dict:
    """규칙 기반 매매 시그널 생성."""
    n = len(closes) - 1
    signals = []
    score = 0  # + = 매수, - = 매도

    # RSI 시그널
    if rsi[n] is not None:
        if rsi[n] < 30:
            signals.append("RSI 과매도 (매수 신호)")
            score += 2
        elif rsi[n] > 70:
            signals.append("RSI 과매수 (매도 신호)")
            score -= 2
        else:
            signals.append(f"RSI 중립 ({rsi[n]:.1f})")

    # 이동평균 골든/데드크로스
    if ma5[n] and ma20[n] and ma5[n-1] and ma20[n-1]:
        if ma5[n] > ma20[n] and ma5[n-1] <= ma20[n-1]:
            signals.append("골든크로스 (강력 매수)")
            score += 3
        elif ma5[n] < ma20[n] and ma5[n-1] >= ma20[n-1]:
            signals.append("데드크로스 (강력 매도)")
            score -= 3
        elif ma5[n] > ma20[n]:
            signals.append("단기 이평 > 중기 이평 (매수 우위)")
            score += 1
        else:
            signals.append("단기 이평 < 중기 이평 (매도 우위)")
            score -= 1

    # 볼린저밴드
    if bb_upper[n] and bb_lower[n]:
        if closes[n] < bb_lower[n]:
            signals.append("볼린저 하단 이탈 (반등 가능)")
            score += 1
        elif closes[n] > bb_upper[n]:
            signals.append("볼린저 상단 돌파 (조정 가능)")
            score -= 1

    # 최종 판단
    if score >= 3:
        action, color = "강력 매수", "green"
    elif score >= 1:
        action, color = "매수", "lightgreen"
    elif score <= -3:
        action, color = "강력 매도", "red"
    elif score <= -1:
        action, color = "매도", "salmon"
    else:
        action, color = "관망", "gray"

    return {"action": action, "color": color, "score": score, "reasons": signals}
