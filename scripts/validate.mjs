import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function readJson(relativePath) {
  return JSON.parse(await readFile(path.join(root, relativePath), "utf8"));
}

function validDate(value) {
  return typeof value === "string" && !Number.isNaN(new Date(value).valueOf());
}

function finiteNonNegative(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function semanticValidation(plans, attestations) {
  const errors = [];
  const fail = (location, message) => errors.push(`${location}: ${message}`);
  const primeIds = new Set();
  const tracks = new Map();
  const programs = new Map();

  const defaults = plans.policy_defaults;
  if (!(defaults.hop_seconds > 0)) fail("policy_defaults.hop_seconds", "must be positive");
  if (!(Number(defaults.max_change) >= 1)) fail("policy_defaults.max_change", "must be at least 1");
  if (!(defaults.operating_cadence_seconds >= defaults.hop_seconds)) {
    fail("policy_defaults.operating_cadence_seconds", "must not be shorter than hop_seconds");
  }

  for (const [primeIndex, prime] of plans.primes.entries()) {
    const primeAt = `primes[${primeIndex}]`;
    if (primeIds.has(prime.id)) fail(`${primeAt}.id`, `duplicate Prime id ${prime.id}`);
    primeIds.add(prime.id);
    if (prime.visible !== undefined && typeof prime.visible !== "boolean") {
      fail(`${primeAt}.visible`, "must be true or false");
    }

    for (const [trackIndex, track] of prime.tracks.entries()) {
      const trackAt = `${primeAt}.tracks[${trackIndex}]`;
      if (tracks.has(track.id)) fail(`${trackAt}.id`, `duplicate track id ${track.id}`);
      tracks.set(track.id, track);
      if (!Array.isArray(track.sources) || track.sources.length === 0) {
        fail(`${trackAt}.sources`, "must contain at least one source reference");
      }
      const phaseIds = new Set();
      for (const [phaseIndex, phase] of track.phases.entries()) {
        const phaseAt = `${trackAt}.phases[${phaseIndex}]`;
        if (phaseIds.has(phase.id)) fail(`${phaseAt}.id`, `duplicate phase id ${phase.id}`);
        phaseIds.add(phase.id);
        if (phase.earliest_transition && !validDate(phase.earliest_transition)) {
          fail(`${phaseAt}.earliest_transition`, "is not a valid date");
        }
        if (phase.transition_window_end && !validDate(phase.transition_window_end)) {
          fail(`${phaseAt}.transition_window_end`, "is not a valid date");
        }
        if (phase.earliest_transition && phase.transition_window_end
          && new Date(phase.transition_window_end) < new Date(phase.earliest_transition)) {
          fail(phaseAt, "transition_window_end precedes earliest_transition");
        }
        const labels = new Set();
        for (const [targetIndex, target] of phase.rate_limit_targets.entries()) {
          const targetAt = `${phaseAt}.rate_limit_targets[${targetIndex}]`;
          if (labels.has(target.label)) fail(`${targetAt}.label`, `duplicate rate-limit label ${target.label}`);
          labels.add(target.label);
          if (target.max_amount_usd !== "unlimited" && !finiteNonNegative(target.max_amount_usd)) {
            fail(`${targetAt}.max_amount_usd`, "must be non-negative or unlimited");
          }
          if (!finiteNonNegative(target.slope_per_day_usd)) {
            fail(`${targetAt}.slope_per_day_usd`, "must be non-negative");
          }
        }
      }
    }

    for (const program of prime.operator_programs ?? []) {
      if (programs.has(program.id)) fail(`operator_programs.${program.id}`, "duplicate program id");
      programs.set(program.id, program);
      if (!(program.cadence_seconds > 0)) fail(`operator_programs.${program.id}.cadence_seconds`, "must be positive");
      if (!validDate(program.first_slot_utc)) fail(`operator_programs.${program.id}.first_slot_utc`, "is not a valid date");
      const programLabels = new Set();
      for (const row of program.rate_limits ?? []) {
        if (!tracks.has(row.track_id)) fail(`operator_programs.${program.id}.${row.label}`, `unknown track ${row.track_id}`);
        if (programLabels.has(row.label)) fail(`operator_programs.${program.id}.${row.label}`, "duplicate rate-limit label");
        programLabels.add(row.label);
        if (!Number.isInteger(row.decimals) || row.decimals < 0) {
          fail(`operator_programs.${program.id}.${row.label}.decimals`, "must be a non-negative integer");
        }
        for (const point of ["starting", "target"]) {
          for (const field of ["max_amount", "slope_per_day"]) {
            const value = row[point]?.[field];
            if (value !== "unlimited" && !(Number(value) >= 0)) {
              fail(`operator_programs.${program.id}.${row.label}.${point}.${field}`, "must be non-negative or unlimited");
            }
          }
        }
      }
      for (const override of program.transition_overrides ?? []) {
        const track = tracks.get(override.track_id);
        const phaseIds = new Set(track?.phases?.map((phase) => phase.id) ?? []);
        if (!track) fail(`operator_programs.${program.id}.transition_overrides`, `unknown track ${override.track_id}`);
        else if (!phaseIds.has(override.from_phase_id) || !phaseIds.has(override.to_phase_id)) {
          fail(`operator_programs.${program.id}.transition_overrides`, "references an unknown phase");
        }
      }
    }
  }

  const allowedStatuses = new Set([
    "active", "eligible", "transitioning", "paused", "blocked", "needs_confirmation", "review_required",
  ]);
  for (const [trackId, attestation] of Object.entries(attestations.tracks)) {
    const track = tracks.get(trackId);
    const at = `attestations.tracks.${trackId}`;
    if (!track) {
      fail(at, "does not correspond to a configured track");
      continue;
    }
    if (!allowedStatuses.has(attestation.status)) fail(`${at}.status`, "is not supported");
    const phaseIds = track.phases.map((phase) => phase.id);
    const currentIndex = attestation.current_phase_id === null
      ? -1
      : phaseIds.indexOf(attestation.current_phase_id);
    const targetIndex = attestation.target_phase_id === null
      ? -1
      : phaseIds.indexOf(attestation.target_phase_id);
    if (attestation.current_phase_id !== null && currentIndex < 0) fail(`${at}.current_phase_id`, "is not in the track plan");
    if (attestation.target_phase_id !== null && targetIndex < 0) fail(`${at}.target_phase_id`, "is not in the track plan");
    if (currentIndex >= 0 && targetIndex >= 0 && targetIndex <= currentIndex) {
      fail(`${at}.target_phase_id`, "must follow the current phase");
    }
    if (["paused", "blocked"].includes(attestation.status) && !attestation.pause_reason) {
      fail(`${at}.pause_reason`, "is required for a paused or blocked track");
    }
    for (const [phaseId, activation] of Object.entries(attestation.phase_activations ?? {})) {
      if (!phaseIds.includes(phaseId)) fail(`${at}.phase_activations.${phaseId}`, "references an unknown phase");
      for (const field of ["expected_execution_utc", "executed_at_utc"]) {
        if (activation[field] && !validDate(activation[field])) {
          fail(`${at}.phase_activations.${phaseId}.${field}`, "is not a valid date");
        }
      }
      const trigger = activation.onchain_trigger;
      if (trigger) {
        if (!trigger.transaction_hash && !trigger.event_name) {
          fail(`${at}.phase_activations.${phaseId}.onchain_trigger`, "requires transaction_hash or event_name");
        }
        for (const field of ["not_before_utc", "not_after_utc"]) {
          if (trigger[field] && !validDate(trigger[field])) {
            fail(`${at}.phase_activations.${phaseId}.onchain_trigger.${field}`, "is not a valid date");
          }
        }
        if (trigger.not_before_utc && trigger.not_after_utc
          && new Date(trigger.not_after_utc) < new Date(trigger.not_before_utc)) {
          fail(`${at}.phase_activations.${phaseId}.onchain_trigger`, "not_after_utc precedes not_before_utc");
        }
      }
      if ((activation.expected_execution_utc || activation.executed_at_utc) && !activation.source) {
        fail(`${at}.phase_activations.${phaseId}.source`, "is required when an execution date is recorded");
      }
    }
  }

  for (const trackId of tracks.keys()) {
    if (!(trackId in attestations.tracks)) fail(`attestations.tracks.${trackId}`, "is missing");
  }
  for (const programId of Object.keys(attestations.programs)) {
    if (!programs.has(programId)) fail(`attestations.programs.${programId}`, "does not correspond to a configured program");
  }

  return errors;
}

export async function validateRepository() {
  const [plans, attestations, plansSchema, attestationsSchema] = await Promise.all([
    readJson("plans/plans.json"),
    readJson("attestations/attestations.json"),
    readJson("schemas/plans.schema.json"),
    readJson("schemas/attestations.schema.json"),
  ]);
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  const errors = [];
  for (const [name, schema, data] of [
    ["plans/plans.json", plansSchema, plans],
    ["attestations/attestations.json", attestationsSchema, attestations],
  ]) {
    const validate = ajv.compile(schema);
    if (!validate(data)) {
      for (const error of validate.errors ?? []) errors.push(`${name}${error.instancePath}: ${error.message}`);
    }
  }
  errors.push(...semanticValidation(plans, attestations));
  return { plans, attestations, errors };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { errors } = await validateRepository();
  if (errors.length) {
    console.error(`Ramp-up configuration validation failed:\n\n${errors.map((error) => `- ${error}`).join("\n")}`);
    process.exitCode = 1;
  } else {
    console.log("Ramp-up configuration validation passed.");
  }
}
