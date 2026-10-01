import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export async function loadDocuments() {
  return JSON.parse(await readFile(path.join(root, "documents/documents.json"), "utf8"));
}

export function googleDocumentId(url) {
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  if (parsed.protocol !== "https:" || parsed.hostname !== "docs.google.com") return null;
  return /^\/document\/d\/([A-Za-z0-9_-]{20,})\/(?:edit)?\/?$/.exec(parsed.pathname)?.[1] ?? null;
}

export function applyDocumentLinks(plans, registry) {
  const errors = [];
  if (registry?.schema_version !== 1 || !Array.isArray(registry.documents)) {
    return ["documents/documents.json: unsupported schema"];
  }
  const tracks = new Map(plans.primes.flatMap((prime) => prime.tracks.map((track) => [track.id, track])));
  const ids = new Set();
  const urls = new Set();
  const assignments = new Set();
  for (const [index, document] of registry.documents.entries()) {
    const at = `documents[${index}]`;
    const googleId = googleDocumentId(document.url);
    if (!/^[a-z0-9-]+$/.test(document.id ?? "") || ids.has(document.id)) errors.push(`${at}.id: missing or duplicate slug`);
    if (!googleId || urls.has(googleId)) errors.push(`${at}.url: must be a unique Google Doc link`);
    if (!document.name || !Array.isArray(document.track_sources) || !document.track_sources.length) {
      errors.push(`${at}: name and at least one track source are required`);
      continue;
    }
    ids.add(document.id);
    urls.add(googleId);
    for (const source of document.track_sources) {
      const assignment = `${source.track_id}:${source.label}`;
      const track = tracks.get(source.track_id);
      if (!track || !source.label || assignments.has(assignment)) {
        errors.push(`${at}.track_sources: unknown track, missing label, or duplicate mapping ${assignment}`);
        continue;
      }
      assignments.add(assignment);
      let reference = track.sources.find((item) => item.label === source.label);
      if (!reference) {
        reference = { label: source.label, visibility: "restricted" };
        track.sources.push(reference);
      }
      reference.url = document.url;
    }
  }
  return errors;
}
