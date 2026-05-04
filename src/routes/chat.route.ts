import express from "express";
import type { Database } from "better-sqlite3";
import { requireLogin } from "../middlewares/auth";
import { createOllama } from "../lib/ollama";
import { answerWithRag } from "../services/agent.service";
import type { ConversationMessage } from "../services/agent.service";
import { audit } from "../services/audit.service";

export function createChatRouter({ db }: { db: Database }): express.Router {
  const router = express.Router();

  const ollama = createOllama({
    baseUrl: process.env.OLLAMA_BASE_URL || "http://127.0.0.1:11434",
  });
  const llmModel  = process.env.LLM_MODEL  || "llama3.1";
  const embedModel = process.env.EMBED_MODEL || "nomic-embed-text";
  const topK = Number(process.env.TOP_K || 6);

  router.post("/chat", requireLogin, async (req, res) => {
    try {
      const question = String(req.body?.question || "").trim();
      if (!question) return res.status(400).json({ error: "question is required" });

      // 대화 히스토리 (선택적) — 최대 20개 메시지, 콘텐츠 5000자 제한
      const MAX_HISTORY_MESSAGES = 20;
      const MAX_CONTENT_LENGTH = 5000;
      const rawHistory: unknown = req.body?.history;
      const history: ConversationMessage[] = Array.isArray(rawHistory)
        ? (rawHistory as unknown[])
            .slice(0, MAX_HISTORY_MESSAGES)
            .filter(
              (h): h is ConversationMessage =>
                h !== null &&
                typeof h === "object" &&
                typeof (h as ConversationMessage).content === "string" &&
                (h as ConversationMessage).content.length <= MAX_CONTENT_LENGTH &&
                (
                  (h as ConversationMessage).role === "user" ||
                  (h as ConversationMessage).role === "assistant"
                )
            )
            .map((h) => ({ role: h.role, content: h.content }))
        : [];

      const user  = req.session.user!;
      const roles = user.roles || ["user"];

      audit(db, {
        userId: user.id,
        clientId: user.clientId,
        eventType: "chat_request",
        payload: { question, roles, historyLength: history.length },
      });

      const { answer, citations, steps } = await answerWithRag({
        db,
        ollama,
        llmModel,
        embedModel,
        question,
        topK,
        userRoles: roles,
        history,
        auditCtx: { userId: user.id, clientId: user.clientId },
      });

      db.prepare(
        "INSERT INTO chats (mongo_user_id, client_id, question, answer, citations_json, steps_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
      ).run(
        user.id,
        user.clientId,
        question,
        answer,
        JSON.stringify(citations),
        JSON.stringify(steps),
        new Date().toISOString()
      );

      audit(db, {
        userId: user.id,
        clientId: user.clientId,
        eventType: "chat_response",
        payload: { citationsCount: citations.length, stepsCount: steps.length },
      });

      res.json({ ok: true, answer, citations, steps });
    } catch (e) {
      const err = e as Error;
      res.status(500).json({ error: err.message || "chat error" });
    }
  });

  return router;
}
