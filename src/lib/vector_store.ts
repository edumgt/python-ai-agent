/**
 * Abstract VectorStore interface + factory.
 *
 * Currently supported backends:
 *   - "sqlite"  (default) — in-process SQLite + cosine similarity
 *   - "qdrant"            — Qdrant REST API
 *
 * Choose via env:  VECTOR_STORE=qdrant  QDRANT_URL=http://qdrant:6333
 */

export type VectorStoreBackend = "sqlite" | "qdrant";

export function resolveVectorStoreBackend(): VectorStoreBackend {
  const v = (process.env.VECTOR_STORE || "sqlite").toLowerCase();
  if (v === "qdrant") return "qdrant";
  return "sqlite";
}

export function resolveQdrantUrl(): string {
  return process.env.QDRANT_URL || "http://localhost:6333";
}

export function resolveQdrantCollection(): string {
  return process.env.QDRANT_COLLECTION || "law_chunks";
}
