# law-rag-agent (Ollama + RAG + Legal 상담 에이전트)

> ⚠️ **면책 고지(중요)**  
> 이 프로젝트는 **법률 자문이 아닌 정보 제공/학습 목적**의 RAG 데모입니다.  
> 실제 사건 적용 전에는 **관할/시점(최신성)/사실관계**를 확인하고, 필요 시 **변호사 자문**을 받으세요.

## 구성 요약
- **LLM/Embeddings**: Ollama (로컬)
- **Backend**: Node.js + Express (CommonJS)
- **Auth/Session**: `express-session` + SQLite (세션 쿠키 기반)
- **Client ID**: 회원가입 시 유저별 `client_id` 부여 → 로그인 시 세션에 유지
- **RAG**: 문서 chunking → Ollama embeddings → **SQLite(기본) 또는 Qdrant** 벡터 검색
- **Vector DB**: SQLite(소규모) ↔ **Qdrant**(대규모) 선택 가능 — `VECTOR_STORE=qdrant`로 전환
- **Frontend**: Tailwind CDN + Offcanvas(좌측 슬라이딩) + 로그인/회원가입 + Chat UI
- **Sample 데이터셋**: `Sample.zip` — 결정례/법령/판결문/해석례 (원천 CSV + AI 라벨 QA/SUM JSON)

---

## 0) 요구사항
- **Docker** (풀 스택: app + Qdrant + Ollama)
- Node.js 24+ (로컬 개발)

---

## 1) 빠른 시작 — Docker 풀 스택 (권장)

### 1-1. 환경 설정
```bash
cp .env.example .env
# 필요 시 .env 수정 (SESSION_SECRET 등)
```

### 1-2. SQLite 벡터 스토어로 전체 스택 실행
```bash
docker compose up -d ollama qdrant app
```

모델 자동 pull (첫 실행 1회):
```bash
docker compose run --rm model-pull
```

### 1-3. Sample 데이터 인덱싱 (첫 실행 1회)
```bash
# Sample.zip을 압축 해제
unzip Sample.zip

# Docker 컨테이너에서 sample 인제스트
docker compose run --rm ingest
```

- 웹: http://localhost:8000
- Qdrant Dashboard: http://localhost:6333/dashboard

### 1-4. Qdrant 벡터 DB로 전환 (대용량 데이터 권장)
`.env`에서 설정:
```
VECTOR_STORE=qdrant
QDRANT_URL=http://qdrant:6333
```
그 후 재실행:
```bash
docker compose up -d
docker compose run --rm ingest
```

---

## 2) 로컬 개발 (Docker 없이)

### 2-1. Ollama 실행 (Docker)
```bash
docker compose up -d ollama
```

### 2-2. 모델 준비 (1회)
```bash
# LLM (답변용)
docker exec -it ollama ollama pull llama3.1

# Embedding (검색용)
docker exec -it ollama ollama pull nomic-embed-text
```

### 2-3. API 서버 실행
```bash
cp .env.example .env
npm install
npm run dev
```

- 웹: http://localhost:8000
- API health: http://localhost:8000/api/health

---

## 3) 사용 흐름
1) 브라우저 접속 → 회원가입 → 로그인  
2) (앱 화면) **Ingest** 메뉴에서 `데모 문서 인덱싱` 실행  
3) **Chat** 메뉴에서 질문 → 근거 인용된 답변 확인  
4) Settings 메뉴에서 **Client ID / 세션 정보** 확인

---

## 4) Sample 데이터셋 (법령/판결/해석/결정 AI 라벨)

`Sample.zip`은 **국가 법령 AI 학습 데이터셋** 샘플입니다:

| 폴더 | 유형 | 파일 형식 |
|------|------|-----------|
| `01.원천데이터/법령` | 법령 전문 | CSV |
| `01.원천데이터/판결문` | 판결 전문 | CSV |
| `01.원천데이터/해석례` | 법제처 해석례 | CSV |
| `01.원천데이터/결정례` | 헌재 결정례 | CSV |
| `02.라벨링데이터/*/QA` | 질의응답 쌍 | JSON |
| `02.라벨링데이터/*/SUM` | 요약 레이블 | JSON |

### 파싱 → 인덱싱
```bash
# 1) Sample.zip 압축 해제 (루트 폴더에 Sample/ 생성)
unzip Sample.zip

# 2) CSV/JSON → 마크다운 변환 + manifest.json 업데이트
npm run parse:sample

# 3) 임베딩 + 벡터 DB 인덱싱
npm run ingest:sample
```

`parse:sample`은 `data/raw/sample/` 아래 문서 유형별 마크다운을 생성하고  
`data/manifest.json`에 항목을 자동 추가합니다.

---

## 5) RAG 인덱싱(서버에서 실행)

### 방법 A) UI에서 실행
- 좌측 메뉴 Ingest → "데모 문서 인덱싱" 버튼 클릭

### 방법 B) CLI로 실행
```bash
npm run ingest
```

---

## 6) 환경변수 (.env)
`.env.example` 참고

- `OLLAMA_BASE_URL` : 기본 `http://127.0.0.1:11434`
- `LLM_MODEL` : 답변 모델
- `EMBED_MODEL` : 임베딩 모델
- `SESSION_SECRET` : 세션 암호화 키
- `SQLITE_PATH` : `./data/app.db`
- `VECTOR_STORE` : `sqlite`(기본) 또는 `qdrant`
- `QDRANT_URL` : Qdrant REST 주소 (기본 `http://localhost:6333`)
- `QDRANT_COLLECTION` : Qdrant 컬렉션 이름 (기본 `law_chunks`)

---

## 7) 문서 추가 방법
- `data/raw/law/` : 법령 발췌/요약/체크리스트
- `data/raw/cases/` : 판례 요약(사실관계/쟁점/판단/시사점)

실무에서는 사내 검증된 문서를 넣고, 문서의 **기준일/버전/관할** 메타데이터를 반드시 관리하세요.

---

## 8) API 요약
- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/me`
- `POST /api/ingest/demo` (로그인 필요)
- `POST /api/chat` (로그인 필요)
- `GET /api/library/search?q=...` (로그인 필요)

---

## 9) 보안/가드레일
- 불법행위 조장/증거조작/위법 회피/타인 권리 침해 등 요청은 거절합니다.
- 최신성/관할 불확실 시 추가 질문을 우선합니다.
- 답변에는 항상 **근거 인용** 블록이 포함됩니다.

---

## 10) 개발 팁
- 기본 벡터 스토어는 **SQLite + 메모리 cosine 계산**입니다 (소규모·로컬 개발에 적합).
- 문서가 많아지면 `VECTOR_STORE=qdrant`로 전환하세요. Qdrant는 **코사인 유사도 HNSW 인덱스**를 사용하여 수백만 벡터도 고속 검색합니다.
- Tailwind는 CDN 방식이라 빌드 없이 즉시 동작합니다.

---

## 라이선스
MIT

---

## 문서 버전업(충돌 방지) 설계 (최종)
이 레포는 실무에서 자주 발생하는 **문서 버전 충돌**을 피하기 위해 아래 원칙으로 동작합니다.

### 1) `data/manifest.json` 기반 문서 관리
- 문서는 `doc_id` + `version` + `effective_date` + `jurisdiction` 메타를 가집니다.
- 같은 `doc_id`를 가진 v1, v2… 문서가 공존할 수 있습니다.

### 2) 인덱싱은 **업서트(upsert)** 방식
- 동일 문서/버전(`doc_id`,`version`)을 다시 인덱싱하면:
  - 기존 해당 버전 chunk를 삭제 후 재삽입 → **중복/충돌 없음**

### 3) 검색은 기본적으로 **최신 버전 우선**
- 기본값: `DOC_VERSION_STRATEGY=latest`
- 필요 시 `DOC_VERSION_STRATEGY=all`로 바꾸면 모든 버전을 함께 검색합니다.

### 4) DB 마이그레이션
- `src/services/db.js`가 구동 시점에 필요한 컬럼을 **idempotent**하게 추가합니다.
- 그래서 기존 DB(`data/app.db`)가 있어도 버전업 후 바로 실행 가능합니다.



---

## RBAC(문서 접근권한) - 최종
- 각 문서는 `data/manifest.json`에서 `allowed_roles`로 접근 권한을 정의합니다.
- 로그인 유저의 role이 허용 목록에 없으면 해당 문서 chunk는 **검색/인용 대상에서 제외**됩니다.

### Admin 계정 만들기
`.env`에서 `ADMIN_EMAILS`에 이메일을 넣고 그 이메일로 회원가입하면 자동으로 admin role이 부여됩니다.
예:
```
ADMIN_EMAILS=admin@example.com
```

---

## 감사로그(Audit) - 최종
- 다음 이벤트들이 `audit_events` 테이블에 기록됩니다:
  - `chat_request`, `retrieve`, `chat_response`, `chat_blocked`, `ingest_demo`
- Admin은 API로 최근 로그를 확인할 수 있습니다:
  - `GET /api/audit/recent?limit=50` (admin only)

---

## 법령/판례 메타 파싱(데모)
- 법령(law) 문서: `제 n 조` 패턴을 간단 추출하여 docs.meta에 저장
- 판례(case) 문서: `사건번호`, `선고일` 패턴을 (있으면) 추출하여 meta에 저장

> 실무에선 정규식/전용 파서를 강화하고, doc 구조(조/항/호, 판결 요지 등)를 표준화하세요.


---

## 수집기(Collector) — 법령/판례 RAG 데이터 수집(공식 API 우선)

이 레포는 **크롤링(스크래핑)** 보다 안정적인 **공식 Open API 기반 수집기**를 먼저 제공합니다.
(필요 시 HTML/PDF 스크래핑 수집기는 별도 provider로 추가 가능합니다.)

### 0) 사전 준비
- 발급받은 키를 `.env`에 설정:
  - `LAWGO_API_KEY=...`
- 응답이 JSON이 아닌 XML로 내려오면:
  - `LAWGO_*_ENDPOINT` 또는 `format=json` 옵션을 API 스펙에 맞게 조정하거나
  - XML 파서를 provider에 추가하세요.

### 1) 법령/판례 수집
```bash
npm run collect:law -- --pages 1 --perPage 20 --concurrency 3
npm run collect:cases -- --pages 1 --perPage 20 --concurrency 3
npm run collect:sync-manifest
```

수집 결과:
- `data/collected/index.jsonl` : 수집 인덱스(append-only)
- `data/collected/lawgo/**` : 정규화 JSON/Markdown 저장

### 2) 수집한 데이터 인덱싱(RAG 반영)
```bash
npm run ingest:collected
```

### 3) 버전 충돌 방지(업서트)
- `scripts/update_manifest_from_collected.js`는 `doc_id + effective_date`를 기준으로 버전을 관리합니다.
- 같은 `effective_date`는 같은 version으로 idempotent하게 유지됩니다.
- 다른 `effective_date`가 들어오면 version을 +1로 증가시켜 **문서 버전 충돌을 방지**합니다.

### 4) 안전/준수
- 공식 API가 없는 자료를 스크래핑할 경우:
  - robots.txt/약관/요청 제한 준수
  - 과도한 트래픽 금지(동시성·rate limit 적용)
  - 저작권(전문 제공) 이슈 검토
