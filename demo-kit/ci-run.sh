#!/bin/sh
# Used by the demo-check / demo-audit / demo-capture composite actions:
#   ci-run.sh <check|audit|capture> [args...]
# Installs the kit's dependencies once (inside the Playwright image), then runs
# the kit script against the caller's workspace with no network.
set -eu
KIT=$(cd "$(dirname "$0")" && pwd)
WORK=${GITHUB_WORKSPACE:-$PWD}
PW_IMAGE=mcr.microsoft.com/playwright:v1.64.1-noble
ENGINE=${ENGINE:-docker}
# SELinux hosts (podman on Fedora) need relabelled mounts; GitHub's docker does not.
Z=; [ "$ENGINE" = podman ] && Z=z

job=$1; shift
case "$job" in check|audit|capture) ;; *) echo "ci-run.sh: unknown job $job" >&2; exit 2 ;; esac

if [ ! -d "$KIT/node_modules/playwright" ]; then
  $ENGINE run --rm -v "$KIT:/kit${Z:+:$Z}" -w /kit "$PW_IMAGE" npm ci --no-audit --no-fund
fi
exec $ENGINE run --rm --network none -v "$KIT:/kit:ro${Z:+,$Z}" -v "$WORK:/work${Z:+:$Z}" -w /work \
  "$PW_IMAGE" node "/kit/$job.mjs" "$@"
