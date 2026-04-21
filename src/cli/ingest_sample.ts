#!/usr/bin/env node
/**
 * src/cli/ingest_sample.ts
 *
 * Convenience CLI that:
 *   1. Parses Sample data (runs parse_sample logic) if markdown files are missing.
 *   2. Ingests all sample docs into the configured vector store (SQLite or Qdrant).
 *
 * Usage:
 *   npm run ingest:sample
 *   VECTOR_STORE=qdrant QDRANT_URL=http://localhost:6333 npm run ingest:sample
 */
import "dotenv/config";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";
import { initDb } from "../services/db";
import { createOllama } from "../lib/ollama";
import { ingestDirectory } from "../services/ingest.service";
import { resolveVectorStoreBackend } from "../lib/vector_store";

async function main(): Promise<void> {
  const cwd = process.cwd();
  const sampleMdDir = path.join(cwd, "data", "raw", "sample");
  const manifestPath = path.join(cwd, "data", "manifest.json");

  // Auto-parse if sample markdown dir is empty / missing
  const sampleFiles = fs.existsSync(sampleMdDir)
    ? fs.readdirSync(sampleMdDir).filter((f) => f.endsWith(".md"))
    : [];

  if (sampleFiles.length === 0) {
    console.log("[ingest:sample] No sample markdown files found. Running parse:sample first...");
    const sampleDir = path.join(cwd, "Sample");
    if (!fs.existsSync(sampleDir)) {
      console.error(
        `[ingest:sample] ERROR: Sample/ directory not found.\n` +
          `Please extract Sample.zip first:\n  unzip Sample.zip`
      );
      process.exit(1);
    }
    execSync(`npx tsx scripts/parse_sample.ts`, { stdio: "inherit", cwd });
  } else {
    console.log(`[ingest:sample] Found ${sampleFiles.length} sample markdown files.`);
  }

  const backend = resolveVectorStoreBackend();
  console.log(`[ingest:sample] Vector store backend: ${backend}`);

  const db = initDb(process.env.SQLITE_PATH || "./data/app.db");
  const ollama = createOllama({
    baseUrl: process.env.OLLAMA_BASE_URL || "http://127.0.0.1:11434",
  });

  const log: string[] = [];
  const result = await ingestDirectory({
    db,
    ollama,
    rawDir: sampleMdDir,
    embedModel: process.env.EMBED_MODEL || "nomic-embed-text",
    chunkSize: Number(process.env.CHUNK_SIZE || 1200),
    overlap: Number(process.env.CHUNK_OVERLAP || 150),
    log,
    manifestPath,
    auditCtx: null,
  });

  console.log(log.join("\n"));
  console.log(JSON.stringify(result, null, 2));
  process.exit(0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
