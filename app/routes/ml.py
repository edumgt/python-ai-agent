"""ML 모델 비교 · 클러스터링 · 계절성 · 회귀 API."""
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
import numpy as np
from app.lib.session import get_current_user
from app.services.stock import get_candles, QUANT_STOCKS
from app.services.ml_models import (
    compare_models,
    tune_hyperparams,
    cluster_stocks,
    seasonality_analysis,
    regression_forecast,
)

router = APIRouter(prefix="/api/ml")


@router.get("/compare")
async def ml_compare(
    symbol: str = Query("005930.KS", description="종목 코드"),
    period: str = Query("2y",        description="데이터 기간"),
    _user=Depends(get_current_user),
):
    """7가지 ML 모델 5-fold 교차검증 비교."""
    data = await get_candles(symbol, period=period, interval="1d")
    candles = data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"데이터 없음: {symbol}")
    result = compare_models(candles)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


@router.get("/tune")
async def ml_tune(
    symbol:     str = Query("005930.KS"),
    period:     str = Query("2y"),
    model_name: str = Query("rf", description="svm | rf | gb"),
    _user=Depends(get_current_user),
):
    """GridSearchCV 하이퍼파라미터 튜닝."""
    data = await get_candles(symbol, period=period, interval="1d")
    candles = data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"데이터 없음: {symbol}")
    result = tune_hyperparams(candles, model_name)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


class ClusterBody(BaseModel):
    symbols: list[str] = []
    period:  str = "2y"


class RoboAllocationBody(BaseModel):
    risk_profile: str = "moderate"  # conservative | moderate | aggressive
    horizon_years: int = 3
    amount_manwon: int = 5000


@router.post("/cluster")
async def ml_cluster(
    body: ClusterBody,
    _user=Depends(get_current_user),
):
    """종목 군집화 (KMeans)."""
    targets = body.symbols or [s["symbol"] for s in QUANT_STOCKS]
    stocks_data = []
    for sym in targets:
        data = await get_candles(sym, period=body.period, interval="1d")
        candles = data.get("candles", [])
        if candles:
            stocks_data.append({"symbol": sym, "candles": candles})
    result = cluster_stocks(stocks_data)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


@router.post("/robo/allocation")
async def robo_allocation(
    body: RoboAllocationBody,
    _user=Depends(get_current_user),
):
    """시장 데이터 기반 간단 자산배분/추천 결과."""
    risk = body.risk_profile if body.risk_profile in ("conservative", "moderate", "aggressive") else "moderate"
    horizon = max(1, min(10, int(body.horizon_years)))
    amount = max(100, int(body.amount_manwon))

    base_alloc = {
        "conservative": {"국내주식": 15, "해외주식": 10, "국내채권": 50, "대체자산": 10, "현금": 15},
        "moderate": {"국내주식": 30, "해외주식": 25, "국내채권": 30, "대체자산": 10, "현금": 5},
        "aggressive": {"국내주식": 45, "해외주식": 35, "국내채권": 10, "대체자산": 8, "현금": 2},
    }[risk].copy()

    # 투자기간 반영: 장기일수록 현금/채권 축소, 주식 확대
    horizon_boost = min(6, max(0, horizon - 3))
    base_alloc["국내주식"] += horizon_boost
    base_alloc["해외주식"] += horizon_boost
    base_alloc["국내채권"] -= horizon_boost
    base_alloc["현금"] -= horizon_boost

    # 합계 100 정규화
    total = sum(base_alloc.values()) or 100
    alloc = {k: round(v * 100 / total, 1) for k, v in base_alloc.items()}
    diff = round(100 - sum(alloc.values()), 1)
    alloc["현금"] = round(alloc["현금"] + diff, 1)

    # 국내 대표 종목의 최근 수익률/변동성으로 간단 스코어링
    picks = []
    for s in QUANT_STOCKS:
        data = await get_candles(s["symbol"], period="2y", interval="1d")
        candles = data.get("candles", [])
        if len(candles) < 80:
            continue
        closes = np.array([float(c["close"]) for c in candles if c.get("close") is not None], dtype=float)
        if len(closes) < 80:
            continue
        rets = np.diff(closes) / closes[:-1]
        ann_ret = float(np.mean(rets) * 252)
        ann_vol = float(np.std(rets) * np.sqrt(252)) if np.std(rets) > 0 else 0.0001
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
        picks.append({
            "symbol": s["symbol"],
            "name": s["name"],
            "sector": s.get("sector", ""),
            "ann_return_pct": round(ann_ret * 100, 2),
            "ann_vol_pct": round(ann_vol * 100, 2),
            "score": sharpe,
        })

    if not picks:
        raise HTTPException(422, "포트폴리오 계산용 시세 데이터가 부족합니다.")

    picks.sort(key=lambda x: x["score"], reverse=True)
    top = picks[:4 if risk == "aggressive" else 3]
    sum_score = sum(max(0.01, p["score"] + 2.0) for p in top)
    stock_bucket = alloc["국내주식"] + alloc["해외주식"]
    stock_picks = []
    for p in top:
        w = round(stock_bucket * (max(0.01, p["score"] + 2.0) / sum_score), 1)
        stock_picks.append({
            "name": p["name"],
            "code": p["symbol"],
            "weight": w,
            "reason": f"연환산 수익률 {p['ann_return_pct']}%, 변동성 {p['ann_vol_pct']}%",
        })

    exp_ret = {
        "conservative": 0.045,
        "moderate": 0.075,
        "aggressive": 0.115,
    }[risk]
    exp_vol = {
        "conservative": 0.05,
        "moderate": 0.10,
        "aggressive": 0.18,
    }[risk]
    years = [1, 3, 5, 10]
    projections = []
    for y in years:
        if y > horizon + 2:
            continue
        total_return = (1 + exp_ret) ** y - 1
        profit = int(round(amount * total_return))
        mdd = -(exp_vol * np.sqrt(y)) * 100
        projections.append({
            "years": y,
            "expected_return_pct": round(total_return * 100, 2),
            "expected_profit_manwon": profit,
            "expected_mdd_pct": round(mdd, 2),
        })

    return {
        "risk_profile": risk,
        "horizon_years": horizon,
        "amount_manwon": amount,
        "allocations": alloc,
        "stock_picks": stock_picks,
        "projections": projections,
    }


@router.get("/seasonality")
async def ml_seasonality(
    symbol: str = Query("005930.KS"),
    period: str = Query("5y"),
    _user=Depends(get_current_user),
):
    """월별·요일별·연말 계절성 분석."""
    data = await get_candles(symbol, period=period, interval="1d")
    candles = data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"데이터 없음: {symbol}")
    result = seasonality_analysis(candles)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


@router.get("/regression")
async def ml_regression(
    symbol: str = Query("005930.KS"),
    period: str = Query("2y"),
    _user=Depends(get_current_user),
):
    """선형회귀·Ridge·Lasso·SVR 수익률 예측 비교."""
    data = await get_candles(symbol, period=period, interval="1d")
    candles = data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"데이터 없음: {symbol}")
    result = regression_forecast(candles)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result
