import type { Database } from "better-sqlite3";
import { applyGuardrails } from "../lib/guardrails";
import { buildCitations } from "../lib/citations";
import { retrieve } from "./retrieve.service";
import { audit } from "./audit.service";
import type { OllamaClient, OllamaMessage } from "../lib/ollama";
import type { AuditParams } from "./audit.service";
import type { ScoredChunk } from "./retrieve.service";
import { analyzeLegalQuery, getDocTypeLabel } from "../lib/legal_search";

// ── 에이전트 상수 ─────────────────────────────────────────────────
const MAX_AGENT_STEPS = 4;

// ── 타입 ─────────────────────────────────────────────────────────
type ToolName = "retrieve" | "final_answer" | "clarify";

interface AgentAction {
  thought: string;
  action: ToolName;
  args: Record<string, unknown>;
}

export interface AgentStep {
  thought: string;
  action: ToolName;
  args: Record<string, unknown>;
  observation?: string;
}

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
}

export interface RagResult {
  answer: string;
  citations: Array<{
    id: number;
    docId: string | null;
    docVersion: number;
    source: string;
    score: number;
  }>;
  steps: AgentStep[];
}

// ── 에이전트 시스템 프롬프트 ──────────────────────────────────────
function buildAgentSystemPrompt(): string {
  return `너는 법령/판례 전문 법률 RAG 에이전트다. 단계적 추론(ReAct)을 통해 사용자 질문에 답한다.

사용 가능한 도구:
1. retrieve - 법령/판례/해석례/결정례 지식베이스 검색
   args: { "query": "검색어", "docType": "law"|"case"|"interpretation"|"decision" (선택) }
2. final_answer - 충분한 근거를 수집한 뒤 루프 종료 신호
   args: {}
3. clarify - 질문이 불명확할 때 사용자에게 확인 요청
   args: { "question": "확인이 필요한 내용" }

응답 형식 (반드시 유효한 JSON 한 줄, 다른 텍스트 없이):
{"thought": "추론 과정을 한국어로 기술", "action": "도구이름", "args": {...}}

원칙:
- 복잡한 질문은 여러 번 retrieve를 호출해 법령/판례/해석례를 모두 수집한다.
- 근거가 부족하면 다른 검색어나 문서유형으로 재검색한다.
- 충분한 근거가 모이면 final_answer를 호출해 루프를 종료한다.
- JSON 외 텍스트는 절대 출력하지 않는다.`;
}

// ── 최종 답변 생성 프롬프트 ──────────────────────────────────────
function buildFinalAnswerSystemPrompt(): string {
  return `너는 법령/판례 중심 RAG 법률상담 에이전트다.

원칙:
- 법률 자문이 아닌 정보 제공 목적임을 항상 고지한다.
- 반드시 제공된 근거를 인용하여 답한다. (근거 없이 단정 금지)
- 검색된 근거들 사이에 충돌이 있으면 문서유형·기준일·사실관계를 비교해서 차이를 설명한다.
- 관할/시점/사실관계가 불명확하면 추가 질문을 제시한다.
답변 포맷:
  (1) 핵심 요약 (3~6줄)
  (2) 근거 기반 설명 (인용번호 [C1]..에 연결)
  (3) 체크리스트 / 다음 질문 (입증자료, 절차, 확인사항)
  (4) 면책/주의 (최신성, 관할, 전문가 상담 권장)
스타일: 한국어로, 과장 없이, 실무적으로. 불확실하면 '가능성이 큼/낮음' 등으로 표현.`;
}

// ── JSON 파싱 ─────────────────────────────────────────────────────
function parseAgentAction(raw: string): AgentAction | null {
  // Strip markdown code fences if present
  const text = raw.replace(/```(?:json)?\n?/g, "").trim();

  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) return null;

  try {
    const candidate = text.slice(start, end + 1);
    const parsed = JSON.parse(candidate) as {
      thought?: unknown;
      action?: unknown;
      args?: unknown;
    };
    if (!parsed || typeof parsed.action !== "string") return null;
    return {
      thought: String(parsed.thought || ""),
      action: parsed.action as ToolName,
      args:
        parsed.args && typeof parsed.args === "object" && !Array.isArray(parsed.args)
          ? (parsed.args as Record<string, unknown>)
          : {},
    };
  } catch {
    return null;
  }
}

// ── 최종 답변 생성 ────────────────────────────────────────────────
async function generateFinalAnswer(
  ollama: OllamaClient,
  llmModel: string,
  question: string,
  allDocs: ScoredChunk[],
  history: ConversationMessage[]
): Promise<string> {
  const citationsText = allDocs.length
    ? buildCitations(allDocs)
    : "검색된 근거가 없습니다. 인덱싱된 문서를 먼저 확인하세요.";

  const historyMessages: OllamaMessage[] = history.map((h) => ({
    role: h.role,
    content: h.content,
  }));

  return ollama.chat({
    model: llmModel,
    messages: [
      { role: "system", content: buildFinalAnswerSystemPrompt() },
      ...historyMessages,
      {
        role: "user",
        content: `[질문]\n${question}\n\n[근거(검색결과 인용)]\n${citationsText}`,
      },
    ],
    options: { temperature: 0.2 },
  });
}

// ── ReAct 에이전트 루프 ───────────────────────────────────────────
async function runAgentLoop({
  db,
  ollama,
  llmModel,
  embedModel,
  question,
  topK,
  userRoles,
  history,
}: {
  db: Database;
  ollama: OllamaClient;
  llmModel: string;
  embedModel: string;
  question: string;
  topK: number;
  userRoles: string[];
  history: ConversationMessage[];
}): Promise<{ answer: string; allDocs: ScoredChunk[]; steps: AgentStep[] }> {
  const profile = analyzeLegalQuery(question);

  const queryHints = [
    profile.desiredDocTypes.length
      ? `우선 문서유형: ${profile.desiredDocTypes.map(getDocTypeLabel).join(", ")}`
      : "",
    profile.articleRefs.length ? `감지 조문: ${profile.articleRefs.join(", ")}` : "",
    profile.caseNumbers.length ? `감지 사건번호: ${profile.caseNumbers.join(", ")}` : "",
    profile.wantsLatest ? "최신성 확인 필요" : "",
  ]
    .filter(Boolean)
    .join(", ");

  const historyMessages: OllamaMessage[] = history.map((h) => ({
    role: h.role,
    content: h.content,
  }));

  const messages: OllamaMessage[] = [
    { role: "system", content: buildAgentSystemPrompt() },
    ...historyMessages,
    {
      role: "user",
      content: `[질문]\n${question}${queryHints ? `\n[질문 메타 분석] ${queryHints}` : ""}`,
    },
  ];

  const steps: AgentStep[] = [];
  const allDocs: ScoredChunk[] = [];
  const seenIds = new Set<number>();

  for (let stepIndex = 0; stepIndex < MAX_AGENT_STEPS; stepIndex++) {
    const raw = await ollama.chat({
      model: llmModel,
      messages,
      options: { temperature: 0.1 },
    });

    const action = parseAgentAction(raw);

    if (!action) {
      // LLM이 유효한 JSON을 반환하지 못한 경우 루프 종료
      steps.push({
        thought: "응답 파싱 실패, 수집된 근거로 최종 답변 생성",
        action: "final_answer",
        args: {},
      });
      break;
    }

    const step: AgentStep = {
      thought: action.thought,
      action: action.action,
      args: action.args,
    };
    steps.push(step);

    if (action.action === "final_answer") {
      break;
    }

    if (action.action === "clarify") {
      const answer = String(action.args.question || "추가 정보가 필요합니다.");
      return { answer, allDocs, steps };
    }

    if (action.action === "retrieve") {
      const query = String(action.args.query || question);
      const docTypeArg =
        typeof action.args.docType === "string" ? action.args.docType : undefined;

      const docs = await retrieve({ db, ollama, embedModel, query, topK, userRoles });
      const filtered = docTypeArg ? docs.filter((d) => d.docType === docTypeArg) : docs;

      for (const d of filtered) {
        if (!seenIds.has(d.id)) {
          seenIds.add(d.id);
          allDocs.push(d);
        }
      }

      const observation = buildCitations(filtered.slice(0, topK));
      step.observation = observation;

      messages.push({ role: "assistant", content: raw });
      messages.push({
        role: "user",
        content: `[검색 결과 (${filtered.length}건)]\n${observation}\n\n지금까지 수집된 근거가 충분한가? 다음 행동을 JSON으로만 응답하라.`,
      });
    }
  }

  // 도큐먼트가 전혀 없으면 기본 검색 수행
  if (allDocs.length === 0) {
    const fallbackDocs = await retrieve({
      db,
      ollama,
      embedModel,
      query: question,
      topK,
      userRoles,
    });
    for (const d of fallbackDocs) {
      if (!seenIds.has(d.id)) {
        seenIds.add(d.id);
        allDocs.push(d);
      }
    }
  }

  const answer = await generateFinalAnswer(ollama, llmModel, question, allDocs, history);
  return { answer, allDocs, steps };
}

// ── 공개 API ─────────────────────────────────────────────────────
export async function answerWithRag({
  db,
  ollama,
  llmModel,
  embedModel,
  question,
  topK,
  userRoles,
  history = [],
  auditCtx = null,
}: {
  db: Database;
  ollama: OllamaClient;
  llmModel: string;
  embedModel: string;
  question: string;
  topK: number;
  userRoles: string[];
  history?: ConversationMessage[];
  auditCtx?: Omit<AuditParams, "eventType" | "payload"> | null;
}): Promise<RagResult> {
  const guard = applyGuardrails(question);
  if (guard.blocked) {
    if (auditCtx)
      audit(db, { ...auditCtx, eventType: "chat_blocked", payload: { question } });
    return { answer: guard.response ?? "", citations: [], steps: [] };
  }

  if (auditCtx) {
    audit(db, {
      ...auditCtx,
      eventType: "agent_start",
      payload: { question, topK, historyLength: history.length },
    });
  }

  const { answer, allDocs, steps } = await runAgentLoop({
    db,
    ollama,
    llmModel,
    embedModel,
    question,
    topK,
    userRoles,
    history,
  });

  if (auditCtx) {
    audit(db, {
      ...auditCtx,
      eventType: "agent_complete",
      payload: {
        question,
        stepsCount: steps.length,
        docsCount: allDocs.length,
      },
    });
  }

  return {
    answer,
    citations: allDocs.map((d) => ({
      id: d.id,
      docId: d.docId,
      docVersion: d.docVersion,
      source: d.source,
      score: d.score,
    })),
    steps,
  };
}
