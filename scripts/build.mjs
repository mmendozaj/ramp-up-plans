import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { validateRepository } from "./validate.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.join(root, "dist");
const { plans, attestations, errors } = await validateRepository();

if (errors.length) {
  console.error(`Cannot publish an invalid configuration:\n\n${errors.map((error) => `- ${error}`).join("\n")}`);
  process.exit(1);
}

const bundle = {
  schema_version: 1,
  generated_at_utc: new Date().toISOString(),
  source: {
    repository: process.env.GITHUB_REPOSITORY ?? null,
    commit_sha: process.env.GITHUB_SHA ?? null,
    ref: process.env.GITHUB_REF_NAME ?? null,
  },
  plans,
  attestations,
};

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await Promise.all([
  writeFile(path.join(output, "ramp-up-config.json"), `${JSON.stringify(bundle, null, 2)}\n`, "utf8"),
  writeFile(path.join(output, "plans.json"), `${JSON.stringify(plans, null, 2)}\n`, "utf8"),
  writeFile(path.join(output, "attestations.json"), `${JSON.stringify(attestations, null, 2)}\n`, "utf8"),
  writeFile(path.join(output, ".nojekyll"), "", "utf8"),
]);

console.log("Published files generated in dist/.");
