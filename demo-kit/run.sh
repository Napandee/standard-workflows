#!/bin/sh
[ -z "${HEAVY_SLOT:-}" ] && [ -x ~/.claude/skills/test-chain/heavy.sh ] && exec env HEAVY_CLASS=large ~/.claude/skills/test-chain/heavy.sh sh "$0" "$@"
# Runs a demo-kit job in the Playwright image, with no network:
#   run.sh test                                         the kit's own tests
#   run.sh check   --project <dir> [siteDir]            site check
#   run.sh audit   --project <dir> [siteDir] [--update-baseline]
#   run.sh capture --project <dir> [siteDir] [outDir]   release screenshots
# <dir> is the project checkout holding _site, demo/demo.config.json and
# demo/audit-baseline.json; siteDir defaults to _site, outDir to shots (both
# relative to <dir>). Local runs gate themselves through the test chain's
# heavy.sh (one large slot); where heavy.sh is absent (CI) this runs directly.
set -eu

KIT=$(cd "$(dirname "$0")" && pwd)
PW_IMAGE=mcr.microsoft.com/playwright:v1.64.1-noble

if [ -n "${ENGINE:-}" ]; then :
elif command -v podman >/dev/null 2>&1; then ENGINE=podman
else ENGINE=docker
fi

[ -d "$KIT/node_modules/playwright" ] || {
  echo "$KIT/node_modules missing: run 'npm ci' in the kit (in the Playwright image) first" >&2
  exit 1
}

job=${1:-}
[ $# -gt 0 ] && shift
PROJECT=
if [ "${1:-}" = --project ]; then PROJECT=$(cd "$2" && pwd); shift 2; fi

case "$job" in
  test)
    exec $ENGINE run --rm --network none -v "$KIT:/kit:z" -w /kit \
      -e HEAVY_CORES="${HEAVY_CORES:-}" "$PW_IMAGE" \
      sh -c 'exec ${HEAVY_CORES:+taskset -c "$HEAVY_CORES"} node --test "test/*.test.mjs"' ;;
  check|audit)
    case "${1:-}" in ""|--*) set -- _site "$@" ;; esac
    set -- node "/kit/$job.mjs" "$@" ;;
  capture)
    [ $# -ge 1 ] || set -- _site
    [ $# -ge 2 ] || set -- "$1" shots
    set -- node /kit/capture.mjs "$@" ;;
  *) echo "usage: run.sh test | check|audit|capture --project <dir> [siteDir] [...]" >&2; exit 2 ;;
esac

[ -n "$PROJECT" ] || { echo "run.sh $job: --project <dir> is required" >&2; exit 2; }
exec $ENGINE run --rm --network none -v "$KIT:/kit:ro,z" -v "$PROJECT:/work:z" -w /work \
  -e HEAVY_CORES="${HEAVY_CORES:-}" "$PW_IMAGE" \
  sh -c 'exec ${HEAVY_CORES:+taskset -c "$HEAVY_CORES"} "$@"' sh "$@"
