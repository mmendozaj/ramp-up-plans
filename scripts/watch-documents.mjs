import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { googleDocumentId, loadDocuments, root } from "./documents.mjs";
import { googleAccessToken } from "./google-auth.mjs";

export function normalizeDocumentText(value) {
  return value.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").normalize("NFC").trim() + "\n";
}

export function documentHash(value) {
  return createHash("sha256").update(normalizeDocumentText(value), "utf8").digest("hex");
}

export function compareDocuments(registry, baseline, textById) {
  return registry.documents.map((document) => {
    const sha256 = documentHash(textById.get(document.id));
    const previous = baseline.documents?.[document.id]?.sha256 ?? null;
    return {
      id: document.id,
      name: document.name,
      url: document.url,
      tracks: document.track_sources.map((source) => source.track_id),
      status: previous === null ? "new" : previous === sha256 ? "unchanged" : "changed",
      sha256,
      previous_sha256: previous,
    };
  });
}

export async function fetchDocumentText(document, offlineDir = null, accessToken = undefined) {
  const googleId = googleDocumentId(document.url);
  if (!googleId) throw new Error(`${document.id}: invalid Google Doc URL`);
  let body;
  if (offlineDir) {
    body = await readFile(path.join(offlineDir, `${document.id}.txt`), "utf8");
  } else {
    const token = accessToken === undefined ? await googleAccessToken() : accessToken;
    const url = token
      ? `https://www.googleapis.com/drive/v3/files/${googleId}/export?mimeType=text%2Fplain`
      : `https://docs.google.com/document/d/${googleId}/export?format=txt`;
    const response = await fetch(url, {
      signal: AbortSignal.timeout(30000),
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok || !response.headers.get("content-type")?.startsWith("text/plain")) {
      throw new Error(`export returned HTTP ${response.status} (${response.headers.get("content-type") ?? "no content type"})`);
    }
    body = await response.text();
  }
  if (body.length < 100 || body.length > 2_000_000) throw new Error("unexpected document length");
  return body;
}

async function main() {
  const arguments_ = process.argv.slice(2);
  const accept = arguments_.includes("--accept");
  const option = (name) => {
    const index = arguments_.indexOf(name);
    return index < 0 ? null : arguments_[index + 1];
  };
  const reportPath = option("--report");
  const offlineDir = option("--offline-dir");
  const planVersion = option("--plan-version");
  const registry = await loadDocuments();
  const baselinePath = path.join(root, "documents/baseline.json");
  const baseline = JSON.parse(await readFile(baselinePath, "utf8"));
  const textById = new Map();
  const errors = [];
  const accessToken = offlineDir ? null : await googleAccessToken();
  for (const document of registry.documents) {
    try {
      textById.set(document.id, await fetchDocumentText(document, offlineDir, accessToken));
    } catch (error) {
      errors.push(`${document.id}: ${error.message}`);
    }
  }
  if (errors.length) {
    console.error(`Document check failed without changing its baseline:\n${errors.join("\n")}`);
    process.exitCode = 1;
    return;
  }
  const documents = compareDocuments(registry, baseline, textById);
  const report = {
    checked_at_utc: new Date().toISOString(),
    plan_version: baseline.plan_version,
    documents,
  };
  if (reportPath) {
    await mkdir(path.dirname(path.resolve(reportPath)), { recursive: true });
    await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  }
  for (const document of documents) {
    console.log(`${document.status.padEnd(9)} ${document.id} ${document.sha256.slice(0, 12)}`);
  }
  const changed = documents.filter((document) => document.status !== "unchanged");
  if (accept) {
    const plans = JSON.parse(await readFile(path.join(root, "plans/plans.json"), "utf8"));
    if (!planVersion || planVersion !== plans.version) {
      throw new Error("--accept requires --plan-version matching the reviewed plans.version");
    }
    if (changed.length && baseline.plan_version === plans.version) {
      throw new Error("the plan version must advance before accepting changed documents");
    }
    const next = {
      schema_version: 1,
      captured_at_utc: report.checked_at_utc,
      plan_version: plans.version,
      documents: Object.fromEntries(documents.map((document) => [document.id, { sha256: document.sha256 }])),
    };
    await writeFile(baselinePath, `${JSON.stringify(next, null, 2)}\n`);
    console.log(`Captured baseline for plan version ${plans.version}.`);
  } else if (changed.length) {
    process.exitCode = 2;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
