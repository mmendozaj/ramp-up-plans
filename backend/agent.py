"""Run the server-side Doc reconciliation worker hourly.

Keep this command running on the backend server. GitHub remains responsible for
review gates, validation on pull requests, merging, and Pages publication.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

from .api import GitHub, RemoteError, Transport, fetch_document_text, google_access_token, http_request, propose_with_fallback
from .core import (apply_document_links, apply_track_proposal, compare_documents, make_baseline,
                   next_plan_version, render_plan_changes)


ROOT = Path(__file__).resolve().parents[1]
TRACKED_INPUTS = ("plans/plans.json", "attestations/attestations.json", "documents/documents.json", "documents/baseline.json")
CHECK_INTERVAL_SECONDS = 60 * 60


def configured_env(root: Path) -> dict[str, str]:
    """Use server environment variables, with repo-root .env as a fallback."""
    file_values = {key: value for key, value in dotenv_values(root / ".env").items() if value is not None}
    return {**file_values, **os.environ}


def refresh_checkout(root: Path = ROOT) -> None:
    """Read the latest accepted plan and baseline before each server check."""
    branch = subprocess.check_output(("git", "symbolic-ref", "--quiet", "--short", "HEAD"),
                                     cwd=root, text=True).strip()
    if branch != "main":
        raise ValueError("Server checkout must be on main")
    dirty = subprocess.check_output(("git", "status", "--porcelain"), cwd=root, text=True).strip()
    if dirty:
        raise ValueError("Server checkout has uncommitted files; use a clean deployment of main")
    result = subprocess.run(("git", "pull", "--ff-only", "origin", "main"), cwd=root,
                            capture_output=True, text=True, timeout=120,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode:
        raise RuntimeError("Could not fast-forward the server checkout; check origin/main and server Git access")


def pull_body(changed: list[dict], version: str, providers: list[str], tracks: list[str]) -> str:
    rows = [
        "## AI drafted ramp-up plan reconciliation", "",
        f"Proposed plan version: `{version}`. Model provider(s): {', '.join(dict.fromkeys(providers))}.",
        f"Revised plan tracks: {', '.join(tracks) if tracks else 'none; source hashes and version only'}.",
        "", "Changed sources:",
        *(f"- [{item['name']}]({item['url']}) → {', '.join(item['tracks'])} (SHA-256: `{item['sha256']}`)" for item in changed),
        "",
        "The document text is not included in this PR. Review each phase body against its summary table and inspect every plan change. Dates, targets, CRR, exposure requirements, and qualitative KPIs are proposals until separately approved or executed.",
        "",
        "The agent did not change attestations or operator authorization. GitHub runs `npm test` and `npm run build` on this PR. Merge only after human review.",
    ]
    return "\n".join(rows)


def run(*, dry_run: bool = False, offline_dir: str | None = None, env: dict[str, str] | None = None,
        transport: Transport = http_request, root: Path = ROOT) -> dict:
    if offline_dir and not dry_run:
        raise ValueError("--offline-dir requires --dry-run")
    env = env if env is not None else configured_env(root)
    files = {relative: (root / relative).read_text(encoding="utf-8") for relative in TRACKED_INPUTS}
    github = None
    main_sha = None
    if not dry_run:
        github = GitHub(env.get("GITHUB_REPOSITORY"), env.get("GITHUB_TOKEN"), transport)
        main_sha = github.main_sha()
        local_sha = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=root, text=True).strip()
        if local_sha != main_sha:
            raise ValueError("Server checkout is not at GitHub main; update it before running the agent")
        dirty = subprocess.check_output(("git", "status", "--porcelain"), cwd=root, text=True).strip()
        if dirty:
            raise ValueError("Server checkout has uncommitted files; use a clean deployment of main")
        for relative, content in files.items():
            if github.file(relative, main_sha) != content:
                raise ValueError(f"Server {relative} differs from GitHub main")
    registry = json.loads(files["documents/documents.json"])
    baseline = json.loads(files["documents/baseline.json"])
    original = json.loads(files["plans/plans.json"])
    registry_errors = apply_document_links(copy.deepcopy(original), registry)
    if registry_errors:
        raise ValueError("; ".join(registry_errors))
    token = None if offline_dir else google_access_token(env, transport)
    text_by_id = {document["id"]: fetch_document_text(document, offline_dir=offline_dir,
                                                      access_token=token, transport=transport)
                  for document in registry["documents"]}
    checked = compare_documents(registry, baseline, text_by_id)
    changed = [item for item in checked if item["status"] != "unchanged"]
    if not changed:
        print("All document hashes match the accepted baseline.")
        return {"changed": []}
    fingerprint = hashlib.sha256("\n".join(f"{item['id']}:{item['sha256']}" for item in changed).encode()).hexdigest()[:16]
    branch = f"ramp-up-agent-{fingerprint}"
    if github and github.branch_exists(branch):
        print(f"Branch {branch} already exists; review its pull request.")
        return {"changed": changed, "branch": branch, "already_exists": True}
    plans = original
    proposed_version = next_plan_version(original["version"])
    providers: list[str] = []
    for item in changed:
        document = next(source for source in registry["documents"] if source["id"] == item["id"])

        def validate_proposal(proposal: dict) -> dict:
            candidate = apply_track_proposal(plans, document, proposal)
            versioned = copy.deepcopy(candidate)
            versioned["version"] = proposed_version
            render_plan_changes(files["plans/plans.json"], original, versioned)
            return candidate

        provider, model, next_plans = propose_with_fallback(
            document, text_by_id[item["id"]], plans, validate_proposal, env, transport)
        plans = next_plans
        providers.append(f"{provider} ({model})")
        print(f"{item['id']}: reconciled with {provider}")
    plans["version"] = proposed_version
    link_errors = apply_document_links(copy.deepcopy(plans), registry)
    if link_errors:
        raise ValueError("; ".join(link_errors))
    plan_text, tracks = render_plan_changes(files["plans/plans.json"], original, plans)
    baseline_text = json.dumps(make_baseline(checked, plans["version"]), indent=2, ensure_ascii=False) + "\n"
    print(f"Prepared plan {plans['version']}; revised tracks: {', '.join(tracks) or 'none'}.")
    if dry_run:
        return {"changed": changed, "branch": branch, "version": plans["version"], "tracks": tracks}
    if github.main_sha() != main_sha:
        raise ValueError("GitHub main advanced during reconciliation; update the checkout and retry")
    commit_sha = github.create_commit(main_sha, [
        {"path": "plans/plans.json", "text": plan_text},
        {"path": "documents/baseline.json", "text": baseline_text}],
        f"Draft ramp-up plan reconciliation {plans['version']}")
    github.create_branch(branch, commit_sha)
    pull = github.create_pull_request(branch, f"Reconcile ramp-up Docs ({plans['version']})",
                                      pull_body(changed, plans["version"], providers, tracks))
    print(f"Opened PR: {pull['url']}")
    try:
        if github.review_and_validation_required():
            github.enable_auto_merge(pull["node_id"], commit_sha)
            print("GitHub auto-merge enabled; merge waits for approval and required checks.")
        else:
            print("PR remains open: configure a required approval and the validate check on main before enabling auto-merge.")
    except (ValueError, RemoteError, OSError) as exc:
        print(f"PR remains open: {exc}")
    return {"changed": changed, "branch": branch, "version": plans["version"], "url": pull["url"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Google Docs hourly and open reviewed ramp-up plan PRs")
    parser.add_argument("--once", action="store_true", help="check once, then exit")
    parser.add_argument("--dry-run", action="store_true", help="check once without writing to GitHub")
    parser.add_argument("--offline-dir", help="directory with <document-id>.txt fixtures; requires --dry-run")
    args = parser.parse_args(argv)

    def check_once() -> None:
        if not args.dry_run:
            refresh_checkout()
        run(dry_run=args.dry_run, offline_dir=args.offline_dir)

    if args.once or args.dry_run:
        try:
            check_once()
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    print("Checking Google Docs now, then every 60 minutes.", flush=True)
    while True:
        try:
            check_once()
        except Exception as exc:
            print(f"Document check failed: {exc}", file=sys.stderr, flush=True)
        try:
            time.sleep(CHECK_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
