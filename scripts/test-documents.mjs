import assert from "node:assert/strict";
import { applyDocumentLinks, googleDocumentId } from "./documents.mjs";
import { compareDocuments, documentHash } from "./watch-documents.mjs";

assert.equal(
  documentHash("\uFEFFPhase 1\r\nmaxAmount: 5 million\r\n"),
  documentHash("Phase 1\nmaxAmount: 5 million\n"),
);
assert.equal(googleDocumentId("https://evil.example/document/d/aaaaaaaaaaaaaaaaaaaa/edit"), null);
assert.equal(googleDocumentId("https://docs.google.com/document/d/aaaaaaaaaaaaaaaaaaaa/edit"), "aaaaaaaaaaaaaaaaaaaa");

const plans = { primes: [{ tracks: [{ id: "one", sources: [{ label: "Plan", visibility: "restricted", url: "old" }] }] }] };
const registry = {
  schema_version: 1,
  documents: [{ id: "sample", name: "Sample", url: "https://docs.google.com/document/d/aaaaaaaaaaaaaaaaaaaa/edit", track_sources: [{ track_id: "one", label: "Plan" }] }],
};
assert.deepEqual(applyDocumentLinks(plans, registry), []);
assert.equal(plans.primes[0].tracks[0].sources[0].url, registry.documents[0].url);
const current = documentHash("Phase 1");
assert.equal(compareDocuments(registry, { documents: { sample: { sha256: current } } }, new Map([["sample", "Phase 1"]]))[0].status, "unchanged");
assert.equal(compareDocuments(registry, { documents: { sample: { sha256: current } } }, new Map([["sample", "Phase 2"]]))[0].status, "changed");
console.log("Document registry and revision checks passed.");
