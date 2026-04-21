/**
 * scripts/parse_sample.ts
 *
 * Parses Sample.zip source data (01.원천데이터) and labeling data (02.라벨링데이터)
 * and converts them into RAG-ready markdown files under data/raw/sample/.
 * Also appends new entries to data/manifest.json.
 *
 * Usage:
 *   npm run parse:sample
 *   npm run parse:sample -- --sampleDir /path/to/Sample
 *
 * DocuType mapping (from JSON labels):
 *   01 → 법령   (law/statute)
 *   02 → 판결문 (court judgment)
 *   03 → 해석례 (legal interpretation)
 *   04 → 결정례 (constitutional court decision)
 */

import "dotenv/config";
import fs from "fs";
import path from "path";
import { createHash } from "crypto";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function sha256(s: string): string {
  return createHash("sha256").update(s, "utf8").digest("hex");
}

function ensureDir(p: string): void {
  fs.mkdirSync(p, { recursive: true });
}

function writeFile(p: string, content: string): void {
  ensureDir(path.dirname(p));
  fs.writeFileSync(p, content, "utf-8");
}

/** Walk a directory recursively and return all file paths. */
function walkFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  const out: string[] = [];
  for (const it of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, it.name);
    if (it.isDirectory()) out.push(...walkFiles(p));
    else if (it.isFile()) out.push(p);
  }
  return out;
}

// ---------------------------------------------------------------------------
// CSV parsing
// ---------------------------------------------------------------------------
interface CsvRow {
  [col: string]: string;
}

function parseCsv(raw: string): CsvRow[] {
  const lines = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  if (lines.length === 0) return [];
  const header = splitCsvLine(lines[0]);
  const rows: CsvRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    if (!lines[i].trim()) continue;
    const cols = splitCsvLine(lines[i]);
    const row: CsvRow = {};
    header.forEach((h, idx) => {
      row[h.trim()] = (cols[idx] ?? "").trim();
    });
    rows.push(row);
  }
  return rows;
}

/** Minimal RFC-4180 CSV line splitter that handles quoted fields. */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuote) {
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') {
        inQuote = false;
      } else {
        cur += ch;
      }
    } else {
      if (ch === '"') {
        inQuote = true;
      } else if (ch === ",") {
        out.push(cur);
        cur = "";
      } else {
        cur += ch;
      }
    }
  }
  out.push(cur);
  return out;
}

// ---------------------------------------------------------------------------
// Typed data structures
// ---------------------------------------------------------------------------
interface LabelInfo {
  lawClass?: string;
  DocuType: string;
  // 법령
  lawId?: string;
  promulgDate?: string;
  effectDate?: string;
  title?: string;
  ministry?: string;
  smClass?: string;
  // 판결문
  precedId?: string;
  caseName?: string;
  caseNum?: string;
  sentenceDate?: string;
  courtName?: string;
  // 해석례
  interpreId?: string;
  agenda?: string;
  agendaNum?: string;
  interpreDate?: string;
  interpreMinName?: string;
  // 결정례
  determintId?: string;
  finalDate?: string;
  caseCode?: string;
  courtCode?: string;
}

interface LabelData {
  instruction: string;
  input?: string;
  output: string;
}

interface LabelFile {
  info: LabelInfo;
  label: LabelData;
}

// ---------------------------------------------------------------------------
// Document type helpers
// ---------------------------------------------------------------------------
const DOCTYPE_MAP: Record<string, string> = {
  "01": "law",
  "02": "case",
  "03": "interpretation",
  "04": "decision",
};

const DOCTYPE_KO: Record<string, string> = {
  "01": "법령",
  "02": "판결문",
  "03": "해석례",
  "04": "결정례",
};

/** Prefix used when generating stable doc_ids from DocuType codes. */
const DOCTYPE_ID_PREFIX: Record<string, string> = {
  "01": "LAW",
  "02": "CASE",
  "03": "INTERP",
  "04": "DECISION",
};

// ---------------------------------------------------------------------------
// Convert law CSV to markdown
// ---------------------------------------------------------------------------
interface LawCsvRow {
  법령일련번호: string;
  MST: string;
  구분: string;
  문장번호: string;
  내용: string;
}

function lawCsvToMarkdown(rows: LawCsvRow[], title: string): string {
  const lines: string[] = [`# ${title}\n`];
  let currentArticle = "";
  for (const r of rows) {
    const content = r["내용"] || "";
    const type = r["구분"] || "";
    if (!content.trim()) continue;
    if (type === "조문") {
      if (currentArticle) lines.push(""); // separator
      currentArticle = content;
      lines.push(`## ${content}`);
    } else if (type === "항") {
      lines.push(content);
    } else if (type === "호") {
      lines.push(`- ${content}`);
    } else {
      lines.push(content);
    }
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Convert judgment / decision / interpretation CSV to markdown
// ---------------------------------------------------------------------------
interface GenericCsvRow {
  구분: string;
  문장번호: string;
  내용: string;
}

function genericCsvToMarkdown(rows: GenericCsvRow[], title: string): string {
  const lines: string[] = [`# ${title}\n`];
  let lastSection = "";
  for (const r of rows) {
    const content = r["내용"] || "";
    const section = r["구분"] || "";
    if (!content.trim()) continue;
    if (section && section !== lastSection) {
      lines.push(`\n## ${section}`);
      lastSection = section;
    }
    lines.push(content);
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Main parsing logic
// ---------------------------------------------------------------------------
interface ManifestDoc {
  doc_id: string;
  doc_type: string;
  title: string;
  jurisdiction: string;
  effective_date: string | null;
  version: number;
  path: string;
  allowed_roles: string[];
  [key: string]: unknown;
}

interface Manifest {
  schema: number;
  default: {
    jurisdiction: string;
    language: string;
    version_strategy: string;
    allowed_roles: string[];
  };
  documents: ManifestDoc[];
}

function loadManifest(p: string): Manifest {
  if (!fs.existsSync(p)) {
    return {
      schema: 1,
      default: {
        jurisdiction: "KR",
        language: "ko",
        version_strategy: "latest",
        allowed_roles: ["user", "admin"],
      },
      documents: [],
    };
  }
  return JSON.parse(fs.readFileSync(p, "utf-8")) as Manifest;
}

function saveManifest(p: string, m: Manifest): void {
  fs.writeFileSync(p, JSON.stringify(m, null, 2), "utf-8");
}

/** Derive a stable doc_id from source type + id. */
function makeDocId(docuType: string, rawId: string): string {
  const prefix = DOCTYPE_ID_PREFIX[docuType] || "DOC";
  return `${prefix}-${rawId.replace(/^0+/, "") || rawId}`;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------
async function main(): Promise<void> {
  // Allow --sampleDir override
  const args = process.argv.slice(2);
  let sampleDir = "";
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--sampleDir" && args[i + 1]) sampleDir = args[i + 1];
  }

  const cwd = process.cwd();
  const rootSampleDir = sampleDir || path.join(cwd, "Sample");
  if (!fs.existsSync(rootSampleDir)) {
    console.error(
      `[parse_sample] ERROR: Sample directory not found at ${rootSampleDir}\n` +
        `Please extract Sample.zip first:\n  unzip Sample.zip\nor provide --sampleDir <path>`
    );
    process.exit(1);
  }

  const sourceDir = path.join(rootSampleDir, "01.원천데이터");
  const labelDir = path.join(rootSampleDir, "02.라벨링데이터");
  const outDir = path.join(cwd, "data", "raw", "sample");
  const manifestPath = path.join(cwd, "data", "manifest.json");

  ensureDir(outDir);

  const manifest = loadManifest(manifestPath);
  // Track existing doc_ids to avoid re-adding
  const existingDocIds = new Set(manifest.documents.map((d) => `${d.doc_id}@${d.version}`));

  const newDocs: ManifestDoc[] = [];
  let mdFilesWritten = 0;

  // ---- Collect all label JSON files ----------------------------------------
  const labelFiles = walkFiles(labelDir).filter((f) => f.endsWith(".json"));
  // Map from id → { SUM: LabelFile[], QA: LabelFile[] }
  const labelMap: Map<string, { SUM: LabelFile[]; QA: LabelFile[] }> = new Map();
  for (const f of labelFiles) {
    try {
      const lf = JSON.parse(fs.readFileSync(f, "utf-8")) as LabelFile;
      const docuType = lf.info?.DocuType;
      const rawId =
        lf.info.lawId ||
        lf.info.precedId ||
        lf.info.interpreId ||
        lf.info.determintId ||
        "";
      const key = `${docuType}:${rawId}`;
      if (!labelMap.has(key)) labelMap.set(key, { SUM: [], QA: [] });
      const entry = labelMap.get(key)!;
      const fLower = f.toLowerCase();
      if (fLower.includes("/sum/")) entry.SUM.push(lf);
      else if (fLower.includes("/qa/")) entry.QA.push(lf);
    } catch {
      // skip malformed
    }
  }

  // ---- Process each source CSV file ----------------------------------------
  const csvFiles = walkFiles(sourceDir).filter((f) => f.endsWith(".csv"));

  for (const csvPath of csvFiles) {
    const basename = path.basename(csvPath, ".csv"); // e.g. HS_B_000006
    const parts = basename.split("_"); // ['HS', 'B', '000006'] or ['HS', 'K', '77']
    if (parts.length < 3) continue;

    const typeCode = parts[1]; // B=법령, P=판결문, H=해석례, K=결정례
    const rawId = parts[2]; // e.g. 000006, 77

    // Map typeCode → DocuType
    const typeCodeToDocuType: Record<string, string> = {
      B: "01",
      P: "02",
      H: "03",
      K: "04",
    };
    const docuType = typeCodeToDocuType[typeCode];
    if (!docuType) continue;

    const docType = DOCTYPE_MAP[docuType];
    const key = `${docuType}:${rawId}`;
    const labels = labelMap.get(key) || { SUM: [], QA: [] };

    // ---- Build markdown ---------------------------------------------------
    const rawCsv = fs.readFileSync(csvPath, "utf-8");
    const rows = parseCsv(rawCsv);

    // Determine title
    let title = "";
    let effectiveDate: string | null = null;
    let extraMeta: Record<string, unknown> = {};

    // Grab title / meta from label info if available
    const firstLabel: LabelFile | undefined = labels.SUM[0] || labels.QA[0];
    if (firstLabel) {
      const info = firstLabel.info;
      if (docuType === "01") {
        title = info.title || `여권법 (${rawId})`;
        effectiveDate = info.effectDate
          ? `${info.effectDate.slice(0, 4)}-${info.effectDate.slice(4, 6)}-${info.effectDate.slice(6, 8)}`
          : null;
        extraMeta = {
          ministry: info.ministry,
          smClass: info.smClass,
          promulgDate: info.promulgDate,
        };
      } else if (docuType === "02") {
        title = info.caseName || `판결문 ${rawId}`;
        effectiveDate = info.sentenceDate ? info.sentenceDate.replace(/\./g, "-") : null;
        extraMeta = {
          caseNum: info.caseNum,
          courtName: info.courtName,
          caseTypeName: (info as unknown as Record<string, string>)["caseTypeName"],
        };
      } else if (docuType === "03") {
        title = info.agenda || `해석례 ${rawId}`;
        effectiveDate = info.interpreDate ? info.interpreDate.replace(/\./g, "-") : null;
        extraMeta = {
          agendaNum: info.agendaNum,
          interpreMinName: info.interpreMinName,
          questionMinName: (info as unknown as Record<string, string>)["questionMinName"],
        };
      } else if (docuType === "04") {
        title = info.caseName || `결정례 ${rawId}`;
        effectiveDate = info.finalDate ? info.finalDate.replace(/\./g, "-") : null;
        extraMeta = {
          caseNum: info.caseNum,
          caseCode: info.caseCode,
          courtCode: info.courtCode,
        };
      }
    }
    if (!title) title = `${DOCTYPE_KO[docuType]} ${rawId}`;

    // Build body markdown
    let body = "";
    if (docuType === "01") {
      body = lawCsvToMarkdown(rows as unknown as LawCsvRow[], title);
    } else {
      body = genericCsvToMarkdown(rows as unknown as GenericCsvRow[], title);
    }

    // Append labeled summaries
    if (labels.SUM.length > 0) {
      body += "\n\n---\n## 요약 (AI 라벨)\n";
      for (const s of labels.SUM) {
        body += `\n${s.label.output}\n`;
      }
    }

    // Append labeled QA pairs
    if (labels.QA.length > 0) {
      body += "\n\n---\n## 관련 질의응답 (AI 라벨)\n";
      for (const qa of labels.QA) {
        if (qa.label.input) {
          body += `\n**Q:** ${qa.label.input}\n\n**A:** ${qa.label.output}\n`;
        } else {
          body += `\n${qa.label.output}\n`;
        }
      }
    }

    // Write markdown file
    const safeTitle = title.replace(/[/\\:*?"<>|]/g, "_").slice(0, 60);
    const mdFileName = `${basename}_${safeTitle}.md`;
    const mdPath = path.join(outDir, mdFileName);
    writeFile(mdPath, body);
    mdFilesWritten++;

    const relPath = `data/raw/sample/${mdFileName}`;
    const docId = makeDocId(docuType, rawId);
    const version = 1;
    const manifestKey = `${docId}@${version}`;

    if (!existingDocIds.has(manifestKey)) {
      const doc: ManifestDoc = {
        doc_id: docId,
        doc_type: docType,
        title,
        jurisdiction: "KR",
        effective_date: effectiveDate,
        version,
        path: relPath,
        allowed_roles: ["user", "admin"],
        ...extraMeta,
      };
      newDocs.push(doc);
      existingDocIds.add(manifestKey);
      console.log(`  [NEW] ${docId} → ${relPath}`);
    } else {
      console.log(`  [SKIP] ${docId} already in manifest`);
    }
  }

  // ---- Persist manifest updates --------------------------------------------
  if (newDocs.length > 0) {
    manifest.documents.push(...newDocs);
    saveManifest(manifestPath, manifest);
    console.log(`\n✅  Added ${newDocs.length} document(s) to data/manifest.json`);
  } else {
    console.log("\nℹ️  No new documents to add.");
  }

  console.log(
    `\nDone. Wrote ${mdFilesWritten} markdown file(s) to data/raw/sample/\n` +
      `Run  npm run ingest:sample  to embed and index them.`
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
