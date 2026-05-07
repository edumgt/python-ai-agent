"""금융 AI 에이전트 - ReAct 루프 기반."""
import json
from typing import Any
import aiosqlite
from app.lib.ollama import OllamaClient
from app.lib.guardrails import check_guardrails
from app.lib.financial_tools import (
    query_personal_cb, query_corporate_cb,
    search_bank_products, search_funds,
)

MAX_STEPS = 6

SYSTEM_PROMPT = """너는 금융 전문 AI 에이전트다. ReAct(Reason+Act) 방식으로 사용자 질문에 답한다.

사용 가능한 도구:
1. query_personal_cb   - 개인 신용(CB) 통계 조회 (특정 수치/통계가 필요할 때)
   args: { "period": "202212", "gender": 1(남)/2(여), "age_band": 1~6, "group_by": "stdt,gender,age_band" }
2. query_corporate_cb  - 기업 신용(CB) 통계 조회 (기업 신용 수치가 필요할 때)
   args: { "period": "2022", "sic_cd": "C(제조업)", "wg_gb": 1(대)/2(중견)/3(중소), "group_by": "bs_dt,sic_cd" }
3. search_bank_products - 은행 수신상품 검색 (금리/상품 검색 요청 때)
   args: { "min_rate": 3.0, "bank_name": "...", "keyword": "...", "limit": 10 }
4. search_funds        - 공모펀드 검색 (펀드 추천/검색 요청 때)
   args: { "main_type": "국내주식", "max_risk_grade": 3, "min_return_1y": 5.0, "limit": 10 }
5. final_answer        - 충분히 답변 가능하면 즉시 호출 (개념 설명, 일반 금융 질문 포함)
   args: {}
6. clarify             - 질문이 극도로 불명확해서 답변이 불가능할 때만 사용
   args: { "question": "확인 내용" }

의사결정 원칙:
- 개념 설명, 용어 해설, 퀀트 방법론 등 일반 지식 질문 → 도구 없이 final_answer 즉시 호출
- DB 조회가 필요한 수치/통계 질문 → 해당 도구 1~2회 호출 후 final_answer
- clarify는 질문이 완전히 무의미할 때만 최후 수단으로 사용
- JSON 외 다른 텍스트는 절대 출력하지 않는다.

응답 형식 (반드시 유효한 JSON 한 줄):
{"thought": "추론 과정(한국어)", "action": "도구이름", "args": {...}}"""

ANSWER_PROMPT = """너는 금융 AI 에이전트다. 수집된 데이터와 전문 지식을 바탕으로 사용자 질문에 충분히 상세하게 답한다.

언어 규칙 (최우선):
- **반드시 한국어로만 답변한다.** 질문이 영어여도 답변은 한국어로 작성한다.
- 영어 전문 용어는 한국어 후 괄호에 영문 병기. 예: 자기회귀누적이동평균(ARIMA)

답변 원칙:
- 개념 설명은 정의 → 구성요소 → 활용 예시 → 한계점 순으로 충분히 서술한다.
- DB 데이터가 있으면 수치를 직접 인용한다. 없으면 전문 지식으로 답변한다.
- 투자 권유가 아닌 정보 제공임을 마지막에 한 줄로 고지한다.
- 답변 길이: 질문 복잡도에 맞게 충분히 (최소 200자 이상).

답변 형식:
## 핵심 요약
(2~4줄 핵심 내용)

## 상세 설명
(구성요소, 작동 원리, 수식/예시 포함)

## 실무 활용
(금융/투자 현장에서의 적용 방법)

## 유의사항
(한계, 주의점, 면책 한 줄)"""


def _parse_action(raw: str) -> dict | None:
    text = raw.replace("```json", "").replace("```", "").strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return None
    try:
        parsed = json.loads(text[s:e + 1])
        if isinstance(parsed.get("action"), str):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


async def run_agent(
    db: aiosqlite.Connection,
    ollama: OllamaClient,
    llm_model: str,
    question: str,
    history: list[dict],
    rag_context: str = "",
) -> dict[str, Any]:
    blocked, block_msg = check_guardrails(question)
    if blocked:
        return {"answer": block_msg, "steps": [], "citations": []}

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history[-10:]:
        messages.append({"role": h["role"], "content": h["content"]})
    q_content = f"[질문]\n{question}"
    if rag_context:
        q_content += f"\n\n[참고 문서(크롤링)]\n{rag_context[:2000]}"
    messages.append({"role": "user", "content": q_content})

    steps = []
    observations = []

    for _ in range(MAX_STEPS):
        raw = await ollama.chat(llm_model, messages, {"temperature": 0.1, "num_predict": 2048})
        action = _parse_action(raw)

        if not action:
            steps.append({"thought": "파싱 실패", "action": "final_answer", "args": {}})
            break

        step = {"thought": action.get("thought", ""), "action": action["action"],
                "args": action.get("args", {})}
        steps.append(step)

        act = action["action"]
        args = action.get("args", {})

        if act == "final_answer":
            break

        if act == "clarify":
            step["observation"] = f"질문 명확화 요청: {args.get('question', '')}"
            break

        # Execute tool
        obs = ""
        if act == "query_personal_cb":
            obs = await query_personal_cb(db, args)
        elif act == "query_corporate_cb":
            obs = await query_corporate_cb(db, args)
        elif act == "search_bank_products":
            obs = await search_bank_products(db, args)
        elif act == "search_funds":
            obs = await search_funds(db, args)
        else:
            obs = f"알 수 없는 도구: {act}"

        step["observation"] = obs
        observations.append(obs)

        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": f"[검색 결과]\n{obs}\n\n더 필요한 정보가 있으면 계속 조회하고, 충분하면 final_answer를 호출하라. JSON으로만 응답."
        })

    # 최종 답변 생성
    context = "\n\n".join(observations) if observations else "조회된 데이터 없음"
    final_messages = [
        {"role": "system", "content": ANSWER_PROMPT},
        {"role": "user", "content": f"[질문]\n{question}\n\n[수집된 데이터]\n{context}"},
    ]
    answer = await ollama.chat(llm_model, final_messages, {"temperature": 0.2, "num_predict": 3000})

    return {"answer": answer, "steps": steps, "citations": []}
