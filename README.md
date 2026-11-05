# standard-workflows

Shared GitHub Actions for Napandee's projects (public and private): **deliberate
releases, a fake-data demo checked on every PR, a Trivy scan and a change filter**. MIT
licensed.

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

## `changes.yml`

Says which named path groups a pull request touches, so slow jobs run only when needed
and hosted minutes are not spent on a docs fix. The caller lists its groups; the
outputs are `groups`, a JSON object of name → `true`/`false` (a reusable workflow's
outputs are fixed, hence JSON), and `code`, the **string** `'true'` when anything
outside `docs/` and `*.md` changed (else `'false'`; compare with `== 'true'`). The logic
is `scripts/changes.py`.

```yaml
on:
  pull_request:
  push:
    branches: [main]
jobs:
  changes:
    permissions: { contents: read }
    uses: Napandee/standard-workflows/.github/workflows/changes.yml@v1
    with:
      groups: |
        ui: web scripts/check_*.mjs .github
        installer: scripts/install.sh installer .github
  ui-checks:
    needs: changes
    if: fromJSON(needs.changes.outputs.groups).ui
    runs-on: ubuntu-latest
    steps:
      - run: echo "the slow UI checks"
  build:
    needs: changes
    if: needs.changes.outputs.code == 'true'
    runs-on: ubuntu-latest
    steps:
      - run: echo "build and test"
  # The only required check. A skipped job is fine; a failed or cancelled one,
  # or a failed filter (which would skip everything), is not.
  gate:
    if: always()
    needs: [changes, ui-checks, build]
    runs-on: ubuntu-latest
    steps:
      - env:
          FILTER: ${{ needs.changes.result }}
          BROKEN: ${{ contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled') }}
        run: |
          if [ "$FILTER" != success ] || [ "$BROKEN" = true ]; then
            echo "::error::changes: $FILTER; a needed job failed or was cancelled: $BROKEN"
            exit 1
          fi
```

- **Make `gate` the only required check** in branch protection, and list every job in
  its `needs`. A required job that is skipped counts as passed, so requiring the slow
  jobs themselves would not protect anything, and a failing `changes` job would skip
  them all.
- **Group names in `if:` are not checked:** `fromJSON(...).iu` is simply empty, and the
  job never runs. Copy names from the `groups` input.
- **Groups:** one per line, `name: pattern pattern ...`; blank lines and `#` comments
  are skipped, and a pattern with spaces can be quoted. Names are letters, digits, `_`
  and `-` (read a name with `-` as `fromJSON(...)['my-group']`). Patterns are git
  pathspecs: `web` is the folder, `*` also crosses `/` (`*.mjs` matches at any depth),
  `':!web/vendor'` excludes. The job fails, naming the line, group or pattern, on a
  malformed line, a pattern git refuses, or a pattern that matches no file at HEAD (an
  exclude is checked by what it excludes), so a stale path cannot silently turn a group
  off. An empty input fails too; write `groups: none` to use `code` alone.
- **Add `.github` to every group** (or keep a shared `ci` group that runs everything),
  so a change to a shared workflow or action runs the jobs that use it. Only the
  caller's own workflow file is caught for you (below).
- **Pull requests are filtered:** compared with their base branch (the first parent of
  the merge checkout). A `merge_group` is compared with its base commit, and a push to
  another branch with the commit before the push, fetched shallow.
- **Everything runs** (every group and `code` true, reason in the log):
  - on a push to the default branch, or to the branches named in `full-run-branches`
    (space-separated; replaces the default), so `main` is always fully tested;
  - when there is nothing to compare with: a new branch or tag, a force push whose old
    commit is gone, or any other event (`workflow_dispatch`, `schedule`,
    `pull_request_target`);
  - when the caller's own workflow file changed. The path comes from
    `github.workflow_ref` and is logged (or "none found"), compared without regard to
    case; set `workflow-path` to name another file, or `-` to turn it off.
- **What counts:** every changed line, and any file added, deleted, renamed, or with its
  mode or binary content changed. The one exception is opt-in: in a file matched by
  `version-files` (pathspecs, default none, e.g. `version-files: package.json
  chart/Chart.yaml`), a change that only moves version numbers (`1.2.3`,
  `1.2.3-rc.4`, suffixes `rc`, `dev`, `alpha`, `beta` only, never inside a dotted run
  of four or more parts such as an IP address) does not count, so a release bump runs
  nothing. Lines are compared in order, so a reorder counts. Never list files where a
  version move matters, such as requirements or a Dockerfile. `code` goes by file
  names alone.
- The caller job grants `contents: read`. No credentials are stored in the checkout;
  the token is sent only with the one shallow fetch of the commit before a push.

Inputs: `groups` (required), `version-files`, `full-run-branches`, `workflow-path`,
`runner`, `kit-ref`. Outputs `groups`, `code`.

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
# scripts/changes.py (needs git and pytest):
podman run --rm -v "$PWD:/repo:Z" -w /repo/scripts docker.io/library/python:3.14-slim sh -c \
  'apt-get update -qq && apt-get install -y -qq git >/dev/null && pip install -q pytest==8.4.2 && python -m pytest -q test_changes.py'
```

Third-party actions are pinned by commit SHA with the version in a comment;
Dependabot opens the updates.
