/**
 * Qdrant vector store client wrapper.
 *
 * Thin adapter around @qdrant/js-client-rest that mirrors the interface
 * used by ingest.service.ts and retrieve.service.ts so the two backends
 * (SQLite-cosine vs Qdrant) are interchangeable.
 */
import { QdrantClient } from "@qdrant/js-client-rest";

export interface QdrantPayload {
  source: string;
  doc_type: string;
  title: string | null;
  text: string;
  doc_id: string | null;
  doc_version: number;
  effective_date: string | null;
  jurisdiction: string | null;
  allowed_roles: string[];
  chunk_index: number;
  content_hash: string;
  created_at: string;
}

export interface QdrantScoredPoint {
  id: string | number;
  score: number;
  payload: QdrantPayload;
}

export class QdrantVectorStore {
  private client: QdrantClient;
  private collection: string;

  constructor(url: string, collection = "law_chunks") {
    this.client = new QdrantClient({ url });
    this.collection = collection;
  }

  /** Ensure the collection exists with the correct vector size. */
  async ensureCollection(vectorSize: number): Promise<void> {
    const exists = await this.client
      .getCollection(this.collection)
      .then(() => true)
      .catch(() => false);

    if (!exists) {
      await this.client.createCollection(this.collection, {
        vectors: { size: vectorSize, distance: "Cosine" },
      });
    }
  }

  /**
   * Delete all points for a specific doc_id + doc_version (upsert pattern).
   */
  async deleteByDocVersion(docId: string, docVersion: number): Promise<void> {
    await this.client.delete(this.collection, {
      filter: {
        must: [
          { key: "doc_id", match: { value: docId } },
          { key: "doc_version", match: { value: docVersion } },
        ],
      },
    });
  }

  /** Upsert a batch of vectors with their payloads. */
  async upsertPoints(
    points: Array<{
      id: string;
      vector: number[];
      payload: QdrantPayload;
    }>
  ): Promise<void> {
    if (!points.length) return;
    await this.client.upsert(this.collection, {
      wait: true,
      points: points.map((p) => ({
        id: p.id,
        vector: p.vector,
        payload: p.payload as unknown as Record<string, unknown>,
      })),
    });
  }

  /** Semantic search — returns topK scored points. */
  async search(
    queryVector: number[],
    topK: number,
    filter?: Record<string, unknown>
  ): Promise<QdrantScoredPoint[]> {
    const results = await this.client.search(this.collection, {
      vector: queryVector,
      limit: topK,
      with_payload: true,
      filter: filter as Parameters<QdrantClient["search"]>[1]["filter"],
    });

    return results.map((r) => ({
      id: r.id,
      score: r.score,
      payload: r.payload as unknown as QdrantPayload,
    }));
  }

  /** Fetch all payloads for a doc_id (all versions). Used by version-filter logic. */
  async scrollByDocId(docId: string): Promise<QdrantScoredPoint[]> {
    const result = await this.client.scroll(this.collection, {
      filter: {
        must: [{ key: "doc_id", match: { value: docId } }],
      },
      with_payload: true,
      limit: 10000,
    });
    return (result.points || []).map((p) => ({
      id: p.id,
      score: 0,
      payload: p.payload as unknown as QdrantPayload,
    }));
  }
}

export function createQdrantStore(
  url: string,
  collection?: string
): QdrantVectorStore {
  return new QdrantVectorStore(url, collection);
}
