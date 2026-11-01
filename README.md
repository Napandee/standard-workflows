# standard-workflows

Shared GitHub Actions for Napandee's projects (public and private): **deliberate
releases, a fake-data demo checked on every PR, and a Trivy scan**. MIT licensed.

Callers pin `@v1`. Every workflow takes `kit-ref` (default `v1`), the ref of this repo
it checks out for the release script and demo kit: **it must match the ref you call
the workflow at** (a test branch passes its own branch name for both).

## What belongs where

| Here (`Napandee/standard-workflows`, public) | `Napandee/actions` (private) |
|---|---|
| Generic, reusable by any repo: release, demo, security scan, demo kit, demo actions | Anything that knows the homelab: k3s runners, in-cluster BuildKit, LAN paths (`build-push-image.yml`, `test-in-container.yml`) |
| Runs on GitHub-hosted runners by default (`runner` input) | Runs on the k3s ARC runners |

Rules that keep the two from drifting:

- **One home per piece.** Never copy a workflow or script between the two repos, or
  into a project. A project calls them; it doesn't vendor them.
- **No homelab detail here.** `kit-ci` fails on names and addresses of the homelab
  (`no-private-details` job). If a workflow needs one, it belongs in `actions`, or the
  value comes in as an input from the caller.
- **Projects keep only what is theirs:** the recorder (`demo/record.mjs`),
  `demo/build.sh`, `demo/seed.sql`, `demo/demo.config.json`, `demo/audit-baseline.json`,
  and thin caller workflows.
- **Versioning:** compatible change → tag `v1.x.y`, then move `v1`, only when `kit-ci`
  and `kit-selftest` are green on that commit. A breaking input change → `v2`.

Design: homelab-scripts `docs/superpowers/specs/2026-10-30-project-standard-design.md`.

## `release.yml`

Called from the project's own `workflow_dispatch` Release workflow. Refuses unless
on `main` and HEAD is untagged, computes the next `vX.Y.Z` (`scripts/next_version.py`),
builds and pushes once to `:candidate-<sha>` with SBOM + provenance, Trivy-scans that
digest (fails on fixable HIGH/CRITICAL), adds `:X.Y.Z`, `:X.Y`, `:X` and `:latest` to the
same digest, tags the commit and creates the GitHub release. Then, unless
`demo: false`: builds the demo, checks and audits it, attaches
`<name>-hero/desktop/mobile.jpg`, and (unless `pages: false`) deploys it to Pages.

```yaml
on:
  workflow_dispatch:
    inputs:
      bump: { type: choice, required: true, options: [patch, minor, major] }
concurrency: { group: release, cancel-in-progress: false }
jobs:
  release:
    permissions: { contents: write, packages: write, pages: write, id-token: write }
    uses: Napandee/standard-workflows/.github/workflows/release.yml@v1
    with:
      bump: ${{ inputs.bump }}
      # demo: false        # a project without a demo
```

The caller job must grant all four permissions even with `demo: false` or
`pages: false`: GitHub checks a reusable workflow's job permissions when it starts.
The demo build step gets `DEMO_KIT` (the kit checkout); the project's build must run
`npm ci` in it before using its scripts.

Inputs: `image` (default repo name), `context`, `file`, `build-args` (`VERSION` and
`GIT_SHA` are always passed), `first-version` (default `1.0.0`), `runner`, `demo`,
`demo-build-command` (default `sh demo/build.sh`), `demo-test-command`,
`demo-logs-path`, `demo-config`, `demo-baseline`, `pages`, `kit-ref`. Outputs
`version`, `digest`.

## `demo.yml`

Builds the demo with the project's command, runs optional project tests
(`test-command`), then the site check and layout audit; uploads `demo-site` and
`audit-shots`. The caller owns the `pull_request` trigger and path filter.

## `security-scan.yml`

Builds the image (`build-args` input) and reports fixable HIGH/CRITICAL findings
(table + SARIF artifact). Report only; the release is the gate. The caller owns the
schedule.

## `demo-check`, `demo-audit`, `demo-capture` (composite actions)

Run the demo kit (`demo-kit/`) in the pinned Playwright image against `_site`, with
no network. The project supplies `_site/pages.json` (from its recorder),
`demo/demo.config.json` (`now`, `name`, `siteLabel`) and `demo/audit-baseline.json`.
Screenshots use a dark colour scheme, so the app should follow
`prefers-color-scheme`. The kit: `shim/demo-shim.js` (replays recorded `/api` GETs
from `window.DEMO_SITE`; saves return "Demo: nothing is saved"), `serve.mjs`
(Pages-like server), `check.mjs`, `audit.mjs` (six rules at 1440x900 and 393x852),
`capture.mjs`. Local runs: `demo-kit/run.sh`.

## Before changing anything

```bash
podman run --rm -v "$PWD:/repo:Z" -w /repo docker.io/rhysd/actionlint:1.7.7
cd demo-kit && ./run.sh test
```

Third-party actions are pinned by commit SHA with the version in a comment;
Dependabot opens the updates.
