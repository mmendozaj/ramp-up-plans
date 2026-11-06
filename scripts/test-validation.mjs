import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { semanticValidation } from "./validate.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const plans = JSON.parse(await readFile(path.join(root, "plans/plans.json"), "utf8"));
const attestations = JSON.parse(await readFile(path.join(root, "attestations/attestations.json"), "utf8"));
const plansSchema = JSON.parse(await readFile(path.join(root, "schemas/plans.schema.json"), "utf8"));
const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);
const validatePlans = ajv.compile(plansSchema);
assert.equal(validatePlans(plans), true, JSON.stringify(validatePlans.errors));

for (const value of [0, 1000000, 1.5, "N/A"]) {
  const candidate = structuredClone(plans);
  candidate.primes[0].tracks[0].phases[0].minimum_exposure_usd = value;
  assert.equal(validatePlans(candidate), true, `Expected minimum exposure ${JSON.stringify(value)} to be valid`);
  assert.deepEqual(semanticValidation(candidate, attestations), []);
}
for (const value of [-1, "1000000", "unknown", "", null, true, {}, []]) {
  const candidate = structuredClone(plans);
  candidate.primes[0].tracks[0].phases[0].minimum_exposure_usd = value;
  assert.equal(validatePlans(candidate), false, `Expected minimum exposure ${JSON.stringify(value)} to be invalid`);
  assert(validatePlans.errors.some((error) => error.instancePath.endsWith("/minimum_exposure_usd")));
}
const withoutMinimumExposure = structuredClone(plans);
delete withoutMinimumExposure.primes[0].tracks[0].phases[0].minimum_exposure_usd;
assert.equal(validatePlans(withoutMinimumExposure), true);

const withInvalidDuration = structuredClone(plans);
withInvalidDuration.primes[0].tracks[0].phases[0].minimum_exposure_days = "N/A";
assert.equal(validatePlans(withInvalidDuration), false);
assert(validatePlans.errors.some((error) => error.instancePath.endsWith("/minimum_exposure_days")));
console.log("Minimum exposure accepts non-negative numbers or N/A; invalid amounts and durations remain rejected.");

const withoutExistingEvidence = structuredClone(attestations);
delete withoutExistingEvidence.tracks["osero-aave"];
assert.deepEqual(semanticValidation(plans, withoutExistingEvidence), []);

withoutExistingEvidence.tracks["osero-aave"] = { status: "approved_without_evidence", current_phase_id: null, target_phase_id: null };
assert(semanticValidation(plans, withoutExistingEvidence).some((error) => error.includes("status: is not supported")));
console.log("Unattested plans stay unconfirmed; supplied attestations remain validated.");

const baseVault = (candidate) => candidate.primes.find((prime) => prime.id === "osero")
  .tracks.find((track) => track.id === "osero-base-erc4626-morpho-v2");
const fallback = baseVault(plans).phases[0].conditional_rate_limit_targets[0];
assert.equal(fallback.rate_limit_targets[0].max_amount_usd, 5000000);
assert.equal(fallback.rate_limit_targets[0].slope_per_day_usd, 1000000);
const invalidFallback = structuredClone(plans);
baseVault(invalidFallback).phases[0].conditional_rate_limit_targets[0].rate_limit_targets[0].max_amount_usd = -1;
assert.equal(validatePlans(invalidFallback), false);
const invalidKey = structuredClone(plans);
baseVault(invalidKey).rate_limit_metadata[0].expected_key = "0xinvalid";
assert.equal(validatePlans(invalidKey), false);
console.log("Conditional fallback targets and exact key metadata are validated independently of normal phase targets.");
