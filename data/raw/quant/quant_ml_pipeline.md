# 퀀트 매매 ML 파이프라인 (Quant Trading ML Pipeline)

## 개요

퀀트 자동매매 시스템은 원시 시장 데이터(OHLCV)에서 시작하여 머신러닝/딥러닝 모델을 통해 매매 시그널을 생성하고, 백테스트로 성과를 검증한 뒤 Alpaca API로 실시간 주문을 실행하는 파이프라인이다.

---

## 1단계: 원시 시장 데이터 수집 (OHLCV)

OHLCV(Open, High, Low, Close, Volume)는 시계열 금융 데이터의 기본 단위다.

- **소스**: Yahoo Finance API (httpx 비동기 요청)
- **종목**: 삼성전자(005930.KS), SK하이닉스(000660.KS), NAVER(035420.KS), 현대자동차(005380.KS), LG화학(051910.KS)
- **기간**: 1y~10y (일봉 기준)
- **포맷**: Unix timestamp + 가격/거래량 배열

```python
{
  "time": 1704067200,   # Unix timestamp
  "open": 72800,
  "high": 73500,
  "low":  72100,
  "close": 73200,
  "volume": 12345678
}
```

---

## 2단계: 데이터 전처리 (Preprocessing)

원시 데이터의 품질 문제를 해결한다.

### 결측치 처리
- `ffill()`: 이전 값으로 채움 (가격 데이터에 적합)
- `bfill()`: 다음 값으로 채움 (시작 구간 처리)
- 종가 0 이하 데이터 제거 (상장폐지, 오류 데이터)

### 정규화
- 수익률(Return) 기반 피처는 이미 스케일 무관
- MLP 모델 적용 시 StandardScaler로 표준화 (평균 0, 분산 1)

### 데이터 분할
- 시계열 특성 유지: 시간 순서대로 학습(80%) / 검증(20%) 분리
- 일반 K-Fold 교차검증 금지 (미래 데이터 누수 방지)

---

## 3단계: 피처 엔지니어링 (Feature Engineering)

기술적 지표를 피처로 변환한다.

### 수익률 (Returns)
- `ret_1`: 1일 수익률 = (close_t - close_{t-1}) / close_{t-1}
- `ret_5`: 5일 수익률
- `ret_20`: 20일 수익률 (월간 모멘텀)

### 이동평균 비율 (Moving Average Ratio)
- `ma5_ratio` = close / MA(5): 단기 추세 대비 현재 위치
- `ma20_ratio` = close / MA(20): 중기 추세 대비 현재 위치

### RSI (Relative Strength Index, 상대강도지수)
- 기간: 14일
- 계산: 100 - 100 / (1 + 평균상승폭/평균하락폭)
- 해석: RSI < 30 → 과매도(매수 신호), RSI > 70 → 과매수(매도 신호)

### MACD (Moving Average Convergence Divergence)
- MACD = EMA(12) - EMA(26)
- Signal = EMA(MACD, 9)
- `macd_hist` = MACD - Signal (히스토그램, 방향성 확인)

### 볼린저밴드 (Bollinger Bands)
- 중간: MA(20)
- 상단/하단: MA(20) ± 2σ
- `bb_width` = (상단 - 하단) / 중간: 변동성 지표
- `bb_pos` = (close - 하단) / (상단 - 하단): 밴드 내 위치 (0~1)

### 거래량 비율 (Volume Ratio)
- `vol_ratio` = volume / MA(volume, 20)
- 1.0 이상: 평균 이상 거래량 (추세 신뢰도 높음)

### ATR (Average True Range, 평균실제범위)
- True Range = max(H-L, |H-PrevC|, |L-PrevC|)
- ATR(14) = TR의 14일 이동평균
- 변동성 및 손절 기준 설정에 활용

### 타깃 레이블
- 5일 후 수익률 기준 3-class 분류:
  - `+1` (매수): 5일 후 +2% 이상 상승
  - `0` (관망): -2% ~ +2% 횡보
  - `-1` (매도): 5일 후 -2% 이하 하락

---

## 4단계: ML 모델 - LightGBM (Light Gradient Boosting Machine)

### 특징
- Gradient Boosting 계열 앙상블 모델
- 트리 기반, 범주형/수치형 혼용 처리 우수
- 빠른 학습 속도, 메모리 효율적
- 금융 시계열 피처 중요도 자동 계산

### 하이퍼파라미터
```python
params = {
    "objective": "multiclass",   # 3-class 분류
    "num_class": 3,
    "num_leaves": 31,            # 트리 복잡도
    "learning_rate": 0.05,       # 학습률
    "feature_fraction": 0.8,     # 피처 서브샘플링
    "num_boost_round": 200,      # 최대 부스팅 횟수
    "early_stopping": 20,        # 조기 종료
}
```

### 피처 중요도 (Feature Importance)
- `gain` 기준 계산: 각 피처가 손실 감소에 기여한 양
- RSI, MACD 히스토그램, 볼린저 위치가 통상 상위권

---

## 4단계 대안: DL 모델 - MLP Neural Net (scikit-learn)

### 구조
- 입력층: 12 피처
- 은닉층 1: 64 뉴런 (ReLU 활성화)
- 은닉층 2: 32 뉴런 (ReLU 활성화)
- 출력층: 3 클래스 (-1, 0, +1)
- 옵티마이저: Adam (lr=0.001)

### LSTM 확장 (PyTorch)
실제 LSTM(Long Short-Term Memory) 적용 시:
- 시퀀스 길이: 20 (20거래일 컨텍스트)
- Hidden size: 128
- Layer: 2개 스택
- `nn.Linear` → Softmax 출력
- PyTorch/TensorFlow 설치 필요

### Transformer 확장
- Self-Attention으로 시계열 패턴 학습
- Positional Encoding으로 순서 정보 보존
- Multi-Head Attention: 다중 시간대 패턴 동시 포착

---

## 5단계: 시그널 생성 (Signal Generation)

### ML 시그널
```
모델 예측 확률: [p_sell, p_hold, p_buy]
argmax → 0,1,2 → -1,0,+1 매핑
```

### 규칙 기반 시그널 (Fallback)
ML 라이브러리 미설치 시 점수 합산:
- RSI < 30: +2점 / RSI > 70: -2점
- MACD 히스토그램 양수: +1점 / 음수: -1점
- 볼린저 하단 근접(bb_pos < 0.1): +1점
- MA5 > MA20: +1점 / MA5 < MA20: -1점
- 합계 ≥ +3: 매수(+1) / ≤ -3: 매도(-1) / 나머지: 관망(0)

---

## 6단계: 백테스트 (Backtest)

### 전략
- 롱 전략: signal=+1 → 포지션 보유, 그 외 현금
- 룩어헤드 바이어스 방지: 전날 시그널로 오늘 매매

### 성과 지표
| 지표 | 설명 | 계산 |
|---|---|---|
| 총 수익률 | 전체 기간 누적 수익 | (최종 자산 / 초기 자산 - 1) × 100 |
| Buy&Hold 수익률 | 단순 보유 전략 비교 | 동일 조건 보유 시 수익률 |
| 샤프지수 | 위험 대비 초과 수익 | (평균 수익률 / 수익률 표준편차) × √252 |
| MDD | 최대 낙폭 | 최고점 대비 최대 하락률 |
| 승률 | 수익 거래 비율 | 수익 거래 수 / 전체 거래 수 × 100 |
| 매매 횟수 | 포지션 변화 횟수 | 신호 변화 횟수 |

### 해석 기준
- 샤프지수 > 1.0: 우수한 위험 조정 수익
- 샤프지수 > 2.0: 매우 우수 (실제 헤지펀드 수준)
- MDD < 20%: 비교적 안정적
- 승률 > 55%: 유효한 전략

---

## 7단계: 실시간 실행 - Alpaca API

### 개요
Alpaca Markets는 RESTful API 기반 미국 주식 브로커다.
Paper Trading 계정으로 실제 돈 없이 시뮬레이션 가능.

### 설정
```bash
# .env에 추가
ALPACA_API_KEY=PKXXXXXXXXXXXXXXXX
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # Paper Trading
# 실전: https://api.alpaca.markets
```

### 주문 API
```http
POST https://paper-api.alpaca.markets/v2/orders
Headers:
  APCA-API-KEY-ID: {api_key}
  APCA-API-SECRET-KEY: {secret_key}
Body:
{
  "symbol": "AAPL",
  "qty": "10",
  "side": "buy",       // buy | sell
  "type": "market",    // market | limit | stop
  "time_in_force": "day"
}
```

### 지원 주문 유형
- `market`: 시장가 (즉시 체결)
- `limit`: 지정가 (price 파라미터 필요)
- `stop`: 스탑 주문 (stop_price 필요)
- `trailing_stop`: 추적 스탑 (trail_percent 필요)

### 잔고 및 포지션 조회
```http
GET /v2/account     # 계좌 잔고
GET /v2/positions   # 현재 보유 포지션
GET /v2/orders      # 주문 내역
```

---

## 파이프라인 연동 구조

```
10분 주기 자동매매 루프 (auto_trade.py)
  ↓
get_candles(symbol, period="2y") → Yahoo Finance API
  ↓
run_pipeline(symbol, candles, model_type="lgb")
  ├── preprocess(candles) → DataFrame
  ├── feature_engineer(df) → 12 피처
  ├── train_lgb(df) → LightGBM 모델
  ├── predict_lgb(model, df) → 시그널 Series
  ├── backtest(df, signals) → 성과 지표
  └── alpaca_execute(symbol, signal) → 주문 실행
  ↓
SQLite/MongoDB: orders, portfolio 업데이트
```

---

## 실무 유의사항

1. **과적합 주의**: 백테스트 성과가 좋아도 실전에서 다를 수 있음 (Overfitting)
2. **거래 비용 미반영**: 수수료, 슬리피지를 고려한 실질 수익률은 낮을 수 있음
3. **시장 체제 변화**: 학습 기간과 실전 기간의 시장 특성이 달라질 수 있음 (Regime Change)
4. **데이터 누수**: 타깃 계산 시 미래 데이터 참조 금지 (shift(-5) 적용 후 마지막 5행 제외)
5. **포지션 사이징**: 전체 자산의 일정 비율만 투자 (Kelly Criterion, 고정 비율 등)

> **면책 고지**: 이 파이프라인은 교육·연구 목적입니다. 실제 투자 결정 전 전문 금융 상담사 자문을 받으세요.
