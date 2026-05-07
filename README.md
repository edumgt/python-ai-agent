# 금융 AI Agent

> ⚠️ **면책 고지** — 이 프로젝트는 **투자 자문이 아닌 정보 제공·학습 목적**의 AI 데모입니다.  
> 실제 투자 결정 전 반드시 전문 투자 상담사 자문을 받으세요.

---

## 목차

1. [기능 개요](#기능-개요)
2. [기술 스택](#기술-스택)
3. [아키텍처](#아키텍처)
4. [로컬 실행 가이드](#로컬-실행-가이드)
5. [환경변수](#환경변수)
6. [AWS 이관 가이드](#aws-이관-가이드)
7. [Qdrant 구성 제안](#qdrant-구성-제안)
8. [주요 화면](#주요-화면)

---

## 기능 개요

| GNB | 기능 |
|---|---|
| **금융정보 Agent** | ReAct 루프 기반 AI 챗봇. 개인CB / 기업CB / 금융상품 CSV를 SQLite로 집계 후 자연어 질의 |
| **크롤링** | GitHub docs (python-quant) 크롤링 → Qdrant RAG. URL 직접 크롤링 지원 |
| **직접매매** | 가상 포트폴리오 관리, 매수/매도 주문, 키움증권·토스증권 API Mockup |
| **퀀트자동매매** | RSI·SMA·볼린저밴드 시그널, 10분 주기 Agentic AI 자동매매 Mockup, 10년 백테스트 |

---

## 기술 스택

### Backend
| 항목 | 기술 |
|---|---|
| 언어 / 프레임워크 | Python 3.12 / FastAPI (async) |
| LLM / 임베딩 | Ollama (`llama3.1` / `nomic-embed-text`) |
| 벡터 DB | Qdrant |
| 사용자 인증 DB | MongoDB (motor async driver) |
| 세션 | Redis (`redis.asyncio`) + HTTP-only 쿠키 |
| 관계형 / 시계열 | aiosqlite (CB 통계, 금융상품, 포트폴리오, 주문) |
| 외부 HTTP | httpx (async) – Yahoo Finance, Ollama API |
| HTML 파싱 | BeautifulSoup4 |
| 환경변수 | pydantic-settings |

### Frontend
| 항목 | 기술 |
|---|---|
| 빌드 | Vanilla JS (ES Modules, CDN-only, 빌드 툴 없음) |
| 스타일 | Tailwind CSS v3 (CDN) |
| 차트 | TradingView Lightweight Charts v4 (CDN) |

### Infra (로컬 Docker)
```
MongoDB 8  ·  Redis 8  ·  Ollama  ·  Qdrant latest
```

---

## 아키텍처

```
Browser
  │
  ▼
FastAPI (Uvicorn)
  ├── /api/auth/*         → MongoDB (motor)
  ├── /api/chat           → ReAct Agent → SQLite + Qdrant RAG
  ├── /api/stocks/*       → Yahoo Finance API (httpx)
  ├── /api/portfolio/*    → SQLite (aiosqlite)
  ├── /api/orders/*       → SQLite + 포트폴리오 동기화
  ├── /api/crawl/*        → GitHub API + Qdrant upsert
  ├── /api/quant/*        → Yahoo Finance + 기술지표 계산
  └── /api/admin/*        → 관리자 전용 초기화
  │
  ├── Redis ──── 세션 (fin_session:{uuid})
  ├── MongoDB ── users collection
  ├── SQLite ─── 10개 테이블
  │               personal_cb_stats, corporate_cb_stats,
  │               bank_products, fund_products, chats,
  │               portfolio, orders, broker_settings,
  │               crawled_docs, audit_events
  ├── Qdrant ─── fin_chunks collection (크롤링 문서)
  └── Ollama ─── llama3.1 (chat) + nomic-embed-text (embed)
```

---

## 로컬 실행 가이드

### 사전 요구사항
- Docker Desktop + Docker Compose v2
- Python 3.12 (로컬 개발 시)

### 1. 인프라 기동

```bash
# Ollama 모델 포함 전체 기동
docker compose up -d

# 모델 준비 대기 (약 1~5분)
docker compose logs -f model-pull
```

### 2. Python 앱 로컬 실행

```bash
# 가상환경
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# 환경변수
cp .env.example .env.dev

# DB 초기화 + CSV 인제스트
python -m app.services.financial_ingest

# 앱 실행
uvicorn app.main:app --reload --port 8000
```

### 3. Docker 전체 실행

```bash
docker compose up -d --build

# CSV 인제스트 (최초 1회)
docker compose run --rm ingest
```

브라우저: `http://localhost:8000`

---

## 환경변수

`.env.example` 참고. 핵심 변수:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 서버 주소 |
| `LLM_MODEL` | `llama3.1` | 채팅 모델 |
| `EMBED_MODEL` | `nomic-embed-text` | 임베딩 모델 |
| `MONGO_URI` | — | MongoDB 연결 문자열 |
| `REDIS_URL` | `redis://localhost:6379` | Redis 연결 문자열 |
| `SQLITE_PATH` | `./data/app.db` | SQLite 파일 경로 |
| `DATA_DIR` | `./data` | CSV 파일 루트 디렉토리 |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant 서버 주소 |
| `QDRANT_COLLECTION` | `fin_chunks` | Qdrant 컬렉션명 |
| `GITHUB_TOKEN` | — | GitHub API rate limit 완화 |

---

## AWS 이관 가이드

### 권장 아키텍처

```
Internet
    │
    ▼
Route 53 → CloudFront (정적 자산 캐싱)
    │
    ▼
ALB (HTTPS, ACM 인증서)
    │
    ▼
ECS Fargate (fin-ai-app)
  ├── Task: FastAPI + Uvicorn
  ├── ECR: Docker 이미지
  └── EFS: data/ 볼륨 (SQLite, CSV)
    │
    ├── DocumentDB (MongoDB 호환) or Atlas
    ├── ElastiCache for Redis (Serverless 또는 r7g.large)
    ├── Ollama on EC2 (G4dn / G5 GPU 인스턴스)
    │     └── EFS 마운트: /root/.ollama
    └── Qdrant (아래 섹션 참고)
```

### ECS Fargate 태스크 정의 요점

```json
{
  "cpu": "1024",
  "memory": "2048",
  "portMappings": [{"containerPort": 8000}],
  "environment": [
    {"name": "OLLAMA_BASE_URL", "value": "http://<ollama-ec2-private-ip>:11434"},
    {"name": "QDRANT_URL",      "value": "http://<qdrant-ec2-private-ip>:6333"}
  ],
  "secrets": [
    {"name": "MONGO_URI",   "valueFrom": "arn:aws:secretsmanager:..."},
    {"name": "REDIS_URL",   "valueFrom": "arn:aws:secretsmanager:..."}
  ]
}
```

### CI/CD (GitHub Actions 예시)

```yaml
- name: Build & push to ECR
  run: |
    docker build -t $ECR_REPO:$GITHUB_SHA .
    docker push $ECR_REPO:$GITHUB_SHA
- name: Deploy to ECS
  run: aws ecs update-service --cluster fin-ai --service app --force-new-deployment
```

---

## Qdrant 구성 제안

### 옵션 비교

| 방식 | 비용 | 관리 | 권장 케이스 |
|---|---|---|---|
| **EC2 자가 호스팅** | EC2 비용만 | 직접 | 데이터 외부 전송 불가 / 비용 최적화 |
| **Qdrant Cloud** | 무료 1GB ~ 유료 | 관리형 | 빠른 PoC / 소규모 |
| **EKS on EC2** | 중간 | K8s 관리 | 대규모 고가용성 |

### EC2 자가 호스팅 (권장 시작점)

```bash
# r7g.large (ARM, 16GB RAM) 또는 m7i.large
docker run -d \
  -p 6333:6333 -p 6334:6334 \
  -v /data/qdrant:/qdrant/storage \
  qdrant/qdrant:latest
```

### 컬렉션 설계

```python
# fin_chunks – 크롤링 문서 RAG
VectorParams(size=768, distance=Distance.COSINE)
# payload 필드: source_url, chunk_index, doc_type, crawled_at

# 권장 인덱스
create_payload_index("fin_chunks", "doc_type", PayloadSchemaType.KEYWORD)
create_payload_index("fin_chunks", "crawled_at", PayloadSchemaType.DATETIME)
```

### 스케일링 시 고려사항

- **Qdrant Cluster 모드**: shard 수 = (총 벡터 수 / 200만) × replication factor
- **메모리**: 768차원 float32 × 벡터 수 × 1.5 (HNSW 오버헤드)
- **스냅샷 백업**: S3에 주기적 스냅샷 (`POST /collections/{name}/snapshots`)

---

## 주요 화면

> Playwright로 캡처한 주요 화면입니다 (한글 폰트 적용, API Mock 기반).

### 1. 메인 랜딩
![메인](screenshots/01_landing.png)

### 2. 회원가입
![회원가입](screenshots/02_register.png)

### 3. 로그인
![로그인](screenshots/03_login.png)

### 4. AI 금융 채팅 (ReAct Agent 답변)
![챗](screenshots/04_agent_chat.png)

### 5. CB 분석 대시보드
![CB분석](screenshots/05_cb_analysis.png)

### 6. 크롤링 현황 (GitHub docs → Qdrant RAG)
![크롤링](screenshots/06_crawl.png)

### 7. 주가 캔들차트 (TradingView)
![주가](screenshots/07_stock_chart.png)

### 8. 포트폴리오 관리
![포트폴리오](screenshots/08_portfolio.png)

### 9. 주문/매매 내역
![주문](screenshots/09_orders.png)

### 10. 퀀트 시그널 대시보드 (RSI·SMA·볼린저)
![퀀트](screenshots/10_quant_signals.png)

### 11. 10분 자동매매 Agentic AI 로그
![자동매매](screenshots/11_auto_trade.png)

### 12. 퀀트 백테스트 (10년 데이터)
![백테스트](screenshots/12_backtest.png)

### 13. GNB/LNB 구조 + 푸터 (에듀엠지티)
![GNB](screenshots/13_gnb_overview.png)

### 14. 시스템 관리 대시보드 (CPU·메모리·Docker 컨테이너)
![시스템](screenshots/14_system_dashboard.png)

### 15. 증권사 Open API 설정 (KIS/eBest)
![증권사API](screenshots/15_broker_api_settings.png)
