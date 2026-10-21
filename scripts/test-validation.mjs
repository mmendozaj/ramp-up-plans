import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { semanticValidation } from "./validate.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const plans = JSON.parse(await readFile(path.join(root, "plans/plans.json"), "utf8"));
const attestations = JSON.parse(await readFile(path.join(root, "attestations/attestations.json"), "utf8"));
const withoutExistingEvidence = structuredClone(attestations);
delete withoutExistingEvidence.tracks["osero-aave"];
assert.deepEqual(semanticValidation(plans, withoutExistingEvidence), []);

withoutExistingEvidence.tracks["osero-aave"] = { status: "approved_without_evidence", current_phase_id: null, target_phase_id: null };
assert(semanticValidation(plans, withoutExistingEvidence).some((error) => error.includes("status: is not supported")));
console.log("Unattested plans stay unconfirmed; supplied attestations remain validated.");
