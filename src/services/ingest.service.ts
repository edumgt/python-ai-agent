import fs from "fs";
import path from "path";
import type { Database } from "better-sqlite3";
import { chunkText } from "../lib/chunker";
import { sha256 } from "../lib/hash";
import { loadManifest } from "./manifest.service";
import { parseLawMeta, parseCaseMeta } from "./metadata_parser.service";
import { audit } from "./audit.service";
import type { OllamaClient } from "../lib/ollama";
import type { AuditParams } from "./audit.service";
import {
  resolveVectorStoreBackend,
  resolveQdrantUrl,
  resolveQdrantCollection,
} from "../lib/vector_store";
import { createQdrantStore } from "../lib/qdrant";

function walkFiles(dir: string): string[] {
  const out: string[] = [];
  const items = fs.readdirSync(dir, { withFileTypes: true });
  for (const it of items) {
    const p = path.join(dir, it.name);
    if (it.isDirectory()) out.push(...walkFiles(p));
    else if (it.isFile()) out.push(p);
  }
  return out;
}

function inferDocType(filepath: string): string {
  const p = filepath.replace(/\\/g, "/");
  if (p.includes("/law/")) return "law";
  if (p.includes("/cases/")) return "case";
  return "misc";
}

function readTextFile(filepath: string): string {
  const raw = fs.readFileSync(filepath);
  return raw.toString("utf-8");
}

interface DocEntry {
  docId: string;
  docType: string;
  title: string | null;
  jurisdiction: string;
  effectiveDate: string | null;
  version: number;
  allowedRoles: string[];
  absPath: string;
  source: string;
  extra: Record<string, unknown>;
}

interface DocRow {
  id: number;
}

export async function ingestDirectory({
  db,
  ollama,
  rawDir,
  embedModel,
  chunkSize,
  overlap,
  log,
  manifestPath = "data/manifest.json",
  auditCtx = null,
}: {
  db: Database;
  ollama: OllamaClient;
  rawDir: string;
  embedModel: string;
  chunkSize: number;
  overlap: number;
  log: string[];
  manifestPath?: string;
  auditCtx?: Omit<AuditParams, "eventType" | "payload"> | null;
}): Promise<{ ok: boolean; docs: number; chunks: number }> {
  const manifest = loadManifest(manifestPath);
  const docs: DocEntry[] = [];

  if (manifest?.documents?.length) {
    for (const d of manifest.documents) {
      docs.push({
        docId: d.doc_id,
        docType: d.doc_type,
        title: d.title,
        jurisdiction: d.jurisdiction || manifest.default?.jurisdiction || "KR",
        effectiveDate: d.effective_date || null,
        version: Number(d.version || 1),
        allowedRoles: d.allowed_roles || manifest.default?.allowed_roles || ["user", "admin"],
        absPath: path.join(process.cwd(), d.path),
        source: d.path.replace(/\\/g, "/"),
        extra: d as unknown as Record<string, unknown>,
      });
    }
    log.push(`[MANIFEST] loaded ${docs.length} docs from ${manifestPath}`);
  } else {
    const files = walkFiles(rawDir).filter((p) =>
      [".md", ".txt"].includes(path.extname(p).toLowerCase())
    );
    for (const fp of files) {
      const rel = path.relative(process.cwd(), fp).replace(/\\/g, "/");
      const docType = inferDocType(fp);
      const docId = sha256(rel).slice(0, 12).toUpperCase();
      docs.push({
        docId: `DOC-${docId}`,
        docType,
        title: path.basename(fp),
        jurisdiction: "KR",
        effectiveDate: null,
        version: 1,
        allowedRoles: ["user", "admin"],
        absPath: fp,
        source: rel,
        extra: {},
      });
    }
    log.push(`[MANIFEST] not found. fallback walk: ${docs.length} files`);
  }

  if (!docs.length) {
    log.push(`[WARN] no ingestable docs`);
    return { ok: true, docs: 0, chunks: 0 };
  }

  // Upsert docs + replace chunks per doc/version
  const delChunksByDoc = db.prepare(`DELETE FROM chunks WHERE doc_id = ? AND doc_version = ?`);
  const delDoc = db.prepare(`DELETE FROM docs WHERE doc_id = ? AND doc_version = ?`);

  const insDoc = db.prepare(`
    INSERT INTO docs (
      doc_id, doc_version, doc_type, title, source, jurisdiction, effective_date,
      allowed_roles_json, meta_json, content_hash, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const getDocRow = db.prepare(`SELECT id FROM docs WHERE doc_id = ? AND doc_version = ?`);

  const insChunk = db.prepare(`
    INSERT INTO chunks (
      source, doc_type, title, text, embedding_json, meta_json, created_at,
      doc_id, doc_version, effective_date, jurisdiction, content_hash, chunk_index, doc_row_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  // Qdrant backend (optional)
  const vectorBackend = resolveVectorStoreBackend();
  const qdrant =
    vectorBackend === "qdrant"
      ? createQdrantStore(resolveQdrantUrl(), resolveQdrantCollection())
      : null;

  let chunkTotal = 0;

  for (const d of docs) {
    if (!fs.existsSync(d.absPath)) {
      log.push(`[WARN] missing file: ${d.source}`);
      continue;
    }
    const text = readTextFile(d.absPath);
    const fileHash = sha256(text);

    // parse meta
    let parsed: Record<string, unknown> = {};
    if (d.docType === "law") parsed = parseLawMeta(text);
    if (d.docType === "case") parsed = { ...parseCaseMeta(text), ...parsed };

    const meta = { ...d.extra, parsed };

    // Replace doc row + related chunks for that doc/version
    delChunksByDoc.run(d.docId, d.version);
    delDoc.run(d.docId, d.version);

    insDoc.run(
      d.docId,
      d.version,
      d.docType,
      d.title || null,
      d.source,
      d.jurisdiction,
      d.effectiveDate,
      JSON.stringify(d.allowedRoles),
      JSON.stringify(meta),
      fileHash,
      new Date().toISOString()
    );

    const docRow = getDocRow.get(d.docId, d.version) as DocRow | undefined;
    const docRowId = docRow?.id;

    const chunks = chunkText(text, { chunkSize, overlap });
    log.push(
      `[DOC] ${d.docId}@v${d.version} roles=${d.allowedRoles.join(",")} ${d.source} -> ${chunks.length} chunks`
    );

    // Qdrant: delete existing points for this doc/version before upserting
    if (qdrant) {
      await qdrant.deleteByDocVersion(d.docId, d.version);
    }

    const qdrantBatch: Array<{
      id: string;
      vector: number[];
      payload: import("../lib/qdrant").QdrantPayload;
    }> = [];

    for (let idx = 0; idx < chunks.length; idx++) {
      const c = chunks[idx];
      const contentHash = sha256(`${d.docId}|${d.version}|${idx}|${c}`);
      const emb = await ollama.embed({ model: embedModel, input: c });
      // Capture timestamp after the (potentially slow) embed call
      const now = new Date().toISOString();

      const chunkMeta = {
        docId: d.docId,
        version: d.version,
        effectiveDate: d.effectiveDate,
        jurisdiction: d.jurisdiction,
        source: d.source,
        title: d.title,
        docType: d.docType,
        allowedRoles: d.allowedRoles,
        docRowId,
        chunkIndex: idx,
      };

      if (qdrant) {
        // Store vector in Qdrant; keep lightweight row in SQLite for doc linkage
        qdrantBatch.push({
          id: contentHash,
          vector: emb,
          payload: {
            source: d.source,
            doc_type: d.docType,
            title: d.title ?? null,
            text: c,
            doc_id: d.docId,
            doc_version: d.version,
            effective_date: d.effectiveDate ?? null,
            jurisdiction: d.jurisdiction,
            allowed_roles: d.allowedRoles,
            chunk_index: idx,
            content_hash: contentHash,
            created_at: now,
          },
        });
        insChunk.run(
          d.source,
          d.docType,
          d.title,
          c,
          "[]", // Embedding stored in Qdrant; placeholder keeps SQLite row valid for doc lineage
          JSON.stringify(chunkMeta),
          now,
          d.docId,
          d.version,
          d.effectiveDate,
          d.jurisdiction,
          contentHash,
          idx,
          docRowId
        );
      } else {
        insChunk.run(
          d.source,
          d.docType,
          d.title,
          c,
          JSON.stringify(emb),
          JSON.stringify(chunkMeta),
          new Date().toISOString(),
          d.docId,
          d.version,
          d.effectiveDate,
          d.jurisdiction,
          contentHash,
          idx,
          docRowId
        );
      }
      chunkTotal++;
    }

    // Flush Qdrant batch for this document
    if (qdrant && qdrantBatch.length) {
      if (qdrantBatch[0].vector.length > 0) {
        await qdrant.ensureCollection(qdrantBatch[0].vector.length);
      }
      await qdrant.upsertPoints(qdrantBatch);
    }
  }

  if (auditCtx) {
    audit(db, {
      ...auditCtx,
      eventType: "ingest_demo",
      payload: { docs: docs.length, chunks: chunkTotal },
    });
  }

  log.push(`[OK] ingest done. docs=${docs.length}, chunks=${chunkTotal}`);
  return { ok: true, docs: docs.length, chunks: chunkTotal };
}
