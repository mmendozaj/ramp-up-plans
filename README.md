# Prime Ramp-up Plans

This repository contains the reviewed operational inputs used by the Prime
ramp-up dashboard. It separates plan maintenance from the dashboard code and
provides a reviewable history of every phase, KPI, date, and rate-limit change.

## Normal update workflow

1. Edit `plans/plans.json` for a plan or parameter change.
2. Edit `attestations/attestations.json` for a current-status, approval, KPI, or
   confirmed-execution change.
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

## Connecting the dashboard

Enable GitHub Pages with **GitHub Actions** as the source. Configure the
dashboard server with:

```text
RAMP_UP_CONFIG_URL=https://OWNER.github.io/REPOSITORY/ramp-up-config.json
```

For the expected repository name under the current account, the URL is:

```text
https://mmendozaj.github.io/ramp-up-plans/ramp-up-config.json
```

The PHP gateway caches the published configuration together with its other
inputs. The browser uses the published configuration when it is valid and falls
back to the bundled configuration otherwise.

## Safety boundaries

- Planned spell dates are references, not proof that a phase is active.
- `executed_at_utc` is reserved for confirmed execution.
- Operator authorization and qualitative KPI completion are explicit
  attestations.
- Osero remains configured with `visible: false` and can be shown by changing
  that field to `true`.
- Generated files include the source commit so operators can identify exactly
  which reviewed version they are reading.
