"""LangGraph StateGraph 기반 금융 AI 에이전트.

워크플로우:
  reason_node → (done?) → answer_node → END
                    ↓no
               tool_node → reason_node (반복)
"""
from __future__ import annotations
import json
import operator
from typing import TypedDict, Annotated, Any

from langgraph.graph import StateGraph, END

from app.lib.ollama import OllamaClient
from app.lib.guardrails import check_guardrails
from app.lib.financial_tools import (
    query_personal_cb,
    query_corporate_cb,
    search_bank_products,
    search_funds,
)

MAX_STEPS = 6

# ── 시스템 프롬프트 ────────────────────────────────────────────────────────────

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


# ── LangGraph State ───────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # operator.add: 각 노드가 반환하는 리스트가 기존 리스트에 추가(append)됨
    messages: Annotated[list[dict], operator.add]
    steps: Annotated[list[dict], operator.add]
    observations: Annotated[list[str], operator.add]
    rag_context: str
    done: bool
    iteration: int
    final_answer: str


# ── 유틸 ──────────────────────────────────────────────────────────────────────

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


def _extract_question(messages: list[dict]) -> str:
    """messages 목록에서 최초 user 질문을 추출한다."""
    for msg in messages:
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


# ── 그래프 빌더 ───────────────────────────────────────────────────────────────

def build_graph(db: Any, ollama: OllamaClient, llm_model: str):
    """
    의존성(db, ollama, llm_model)을 클로저로 바인딩하여
    LangGraph StateGraph를 생성하고 컴파일한다.
    """

    # ── 노드 정의 ─────────────────────────────────────────────────────────────

    async def reason_node(state: AgentState) -> dict:
        """추론 노드: LLM이 현재 상태를 보고 다음 행동(action)을 JSON으로 결정한다."""
        if state["iteration"] >= MAX_STEPS:
            return {"done": True}

        # 시스템 프롬프트 + 대화 히스토리 조합
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(state["messages"])

        raw = await ollama.chat(
            llm_model, messages, {"temperature": 0.1, "num_predict": 2048}
        )
        action = _parse_action(raw)

        if not action:
            return {"done": True, "iteration": state["iteration"] + 1}

        step: dict = {
            "thought": action.get("thought", ""),
            "action":  action["action"],
            "args":    action.get("args", {}),
        }
        done = action["action"] in ("final_answer", "clarify")

        return {
            "messages":  [{"role": "assistant", "content": raw}],
            "steps":     [step],
            "done":      done,
            "iteration": state["iteration"] + 1,
        }

    async def tool_node(state: AgentState) -> dict:
        """도구 실행 노드: 마지막 step의 action을 실제로 실행하고 결과를 관찰한다."""
        last_step = state["steps"][-1]
        act  = last_step["action"]
        args = last_step.get("args", {})

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

        return {
            "messages": [{
                "role":    "user",
                "content": (
                    f"[검색 결과]\n{obs}\n\n"
                    "더 필요한 정보가 있으면 계속 조회하고, 충분하면 final_answer를 호출하라. JSON으로만 응답."
                ),
            }],
            "observations": [obs],
        }

    async def answer_node(state: AgentState) -> dict:
        """최종 답변 생성 노드: 수집된 관찰(observations)을 기반으로 한국어 답변을 작성한다."""
        context  = "\n\n".join(state["observations"]) if state["observations"] else "조회된 데이터 없음"
        question = _extract_question(state["messages"])

        final_messages = [
            {"role": "system", "content": ANSWER_PROMPT},
            {
                "role":    "user",
                "content": f"[질문]\n{question}\n\n[수집된 데이터]\n{context}",
            },
        ]
        answer = await ollama.chat(
            llm_model, final_messages, {"temperature": 0.2, "num_predict": 3000}
        )
        return {"final_answer": answer}

    # ── 라우팅 ────────────────────────────────────────────────────────────────

    def route_after_reason(state: AgentState) -> str:
        """reason_node 이후 분기: done이면 answer로, 아니면 tools로."""
        if state.get("done") or state["iteration"] >= MAX_STEPS:
            return "answer"
        return "tools"

    # ── 그래프 조립 ───────────────────────────────────────────────────────────

    graph = StateGraph(AgentState)
    graph.add_node("reason", reason_node)
    graph.add_node("tools",  tool_node)
    graph.add_node("answer", answer_node)

    graph.set_entry_point("reason")
    graph.add_conditional_edges(
        "reason",
        route_after_reason,
        {"tools": "tools", "answer": "answer"},
    )
    graph.add_edge("tools",  "reason")
    graph.add_edge("answer", END)

    return graph.compile()


# ── 공개 진입점 ───────────────────────────────────────────────────────────────

async def run_agent(
    db:        Any,
    ollama:    OllamaClient,
    llm_model: str,
    question:  str,
    history:   list[dict],
    rag_context: str = "",
) -> dict[str, Any]:
    """
    LangGraph 에이전트를 실행하여 질문에 대한 답변을 반환한다.

    기존 app.services.agent.run_agent 와 동일한 시그니처를 유지하므로
    chat.py 의 import 경로만 변경하면 된다.
    """
    blocked, block_msg = check_guardrails(question)
    if blocked:
        return {"answer": block_msg, "steps": [], "citations": []}

    # 사용자 메시지 구성
    q_content = f"[질문]\n{question}"
    if rag_context:
        q_content += f"\n\n[참고 문서(RAG)]\n{rag_context[:2000]}"

    initial_messages: list[dict] = []
    for h in history[-10:]:
        initial_messages.append({"role": h["role"], "content": h["content"]})
    initial_messages.append({"role": "user", "content": q_content})

    initial_state: AgentState = {
        "messages":     initial_messages,
        "steps":        [],
        "observations": [],
        "rag_context":  rag_context,
        "done":         False,
        "iteration":    0,
        "final_answer": "",
    }

    compiled = build_graph(db, ollama, llm_model)
    result   = await compiled.ainvoke(initial_state)

    return {
        "answer":    result.get("final_answer", "답변 생성에 실패했습니다."),
        "steps":     result.get("steps", []),
        "citations": [],
    }
