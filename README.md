# Prime Ramp-up Plans

This repository contains the reviewed plans used by the Prime ramp-up plan
registry. It separates plan maintenance from the dashboard code and provides a
reviewable history of phase, KPI, date, and rate-limit target changes. The
dashboard shows observed onchain values separately as a read-only comparison.

## Normal update workflow

1. Edit `plans/plans.json` for a plan, Skyeco link, or parameter change.
2. If maintaining the separate status feed, edit `attestations/attestations.json`
   for an approval, KPI, or confirmed-execution change. The registry itself does
   not use attestations to declare a current phase.
3. Run:

   ```bash
   npm install
   npm test
   npm run build
   ```

4. Open a pull request and obtain the required review.
5. Merge the pull request. GitHub Actions validates the files and publishes
   `ramp-up-config.json` through GitHub Pages.

The dashboard keeps a bundled local copy as a safety fallback. A failed or
invalid remote update therefore does not remove the last reviewed plan.

## Watching the working Google Docs

Edit [`documents/documents.json`](documents/documents.json) to replace a
Google Doc link or add a new document and its dashboard track mapping. The
published plan bundle takes these links from that file. A new DPAU also needs
its track definition in `plans/plans.json`.

`npm run documents:check` exports the listed Docs as plain text and compares
their hashes with `documents/baseline.json`. It exits with status 2 when a
document is new or changed, and status 1 if an export fails. This command is
available for manual reconciliation; the Python server worker performs the
scheduled check and opens a review pull request when a Doc changes.

After a manual reviewer has reconciled the changed phase definitions in
`plans/plans.json`, advanced `plans.version`, and run `npm test` and
`npm run build`, capture the new baseline (replace the example version with the
new `plans.version`):

```bash
npm run documents:check -- --accept --plan-version 2026-10-02.1
```

Commit the plan and baseline in a reviewed pull request. Merging it makes
GitHub Pages publish the new `ramp-up-config.json`, which the browser reads
directly. An edited Doc does not by itself advance a phase, alter attestations,
or silently change planned rate-limit targets. If a document
becomes inaccessible to the server worker, the check fails and the last
reviewed dashboard configuration remains in place.

## Server-side reconciliation agent

`python3 -m backend.agent` is a long-running Python backend worker. It checks
every configured Doc at startup and every 60 minutes after each check against
the accepted hashes, sends the
**full text of changed Docs and their affected plan tracks** to Claude, and
uses OpenAI if Claude is unavailable or fails and an OpenAI key is configured.
The worker drafts only `plans/plans.json` and `documents/baseline.json`. It
checks track identities and source links, then creates a pull request against
`main`. GitHub Actions runs `npm test` and `npm run build` on the PR. The worker
never edits attestations or publishes. The PR includes Doc links and hashes,
not Doc text. Reviewers must check the complete source and proposed diff. A
Doc revision that changes only wording still advances the plan version so its
new hash can be reviewed in the PR.

After opening the PR, the worker asks GitHub to auto-merge it **only if** the
active `main` rules require at least one approval and the `validate` check from
`.github/workflows/publish.yml`. GitHub then merges after both gates pass, and
the push to `main` triggers the separate Pages publication workflow. If the
rules, API permission, or auto-merge setting are missing, the PR remains open
for manual handling. Approval by itself does not publish until GitHub merges.

Deploy from a clean checkout of `main`. The committed `.python-version` selects
Python 3.11 with pyenv. Set up an isolated environment on the server:

```bash
pyenv install 3.11  # skip if a 3.11 version is already installed
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cp .env.example .env
chmod 600 .env
```

Fill in `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, and `OPENAI_API_KEY` in `.env`.
The worker reads `.env` automatically; exported environment variables take
precedence. Keep `.env` private and outside version control. Node.js remains
in the GitHub validation and publishing workflows. The worker fast-forwards
its clean `main` checkout before every check so it reads newly merged plans
and accepted hashes.

| Variable | Purpose |
| --- | --- |
| `GITHUB_REPOSITORY` | Preconfigured as `mmendozaj/ramp-up-plans`. |
| `GITHUB_TOKEN` | Token from a separate GitHub user invited as a write collaborator. For this user-owned repository, use a classic personal access token with `public_repo` if the repository is public, or `repo` if it is private. GitHub currently does not support fine-grained personal access tokens for repository collaborators. |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Preferred Claude key; model preconfigured in `.env.example`. |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI fallback key; model preconfigured in `.env.example`. |

The configured Docs are public, so Google credentials are unnecessary. Run the
worker under a dedicated server account and keep it running with your server's
service manager:

```bash
.venv/bin/python -u -m backend.agent
```

Use `--once` for a single live check or `--dry-run` for a single check without
writing to GitHub. `--offline-dir DIR` can be added to a dry run for local text
fixtures named `<document-id>.txt`. A failed check is logged and retried after
60 minutes. The worker logs review metadata, not exported Doc text.

In GitHub repository settings, enable **Allow auto-merge** and **squash
merging**. Add a `main` ruleset requiring at least one approving review and the
`validate` status check. Use a bot user distinct from your reviewer account:
GitHub does not count an author's approval of their own PR. Do not give the bot
a rules bypass. Set GitHub Pages to publish from GitHub Actions. The worker
checks these gates before requesting auto-merge; it does not configure
repository rules itself. A classic branch protection rule can also work if the
token can read its settings; a write collaborator may lack that access, leaving
the PR open for manual merge.

## Connecting the dashboard

Enable GitHub Pages with **GitHub Actions** as the source. The dashboard can
override the default browser-facing registry URL at build time with:

```text
VITE_RAMP_UP_CONFIG_URL=https://OWNER.github.io/REPOSITORY/ramp-up-config.json
```

For the expected repository name under the current account, the URL is:

```text
https://mmendozaj.github.io/ramp-up-plans/ramp-up-config.json
```

The browser uses a published version when it is valid and at least as new as its
bundled reviewed version. The PHP gateway supplies the separate onchain
reference values.

## Safety boundaries

- Planned spell dates are references, not proof that a phase is active.
- `executed_at_utc` is reserved for confirmed execution.
- Operator authorization and qualitative KPI completion are explicit
  attestations.
- Osero is visible in the dashboard; visibility does not imply phase approval
  or completed PAS onboarding.
- Generated files include the source commit so operators can identify exactly
  which reviewed version they are reading.
