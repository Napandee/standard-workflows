#!/usr/bin/env bash
# Publishes a release's screenshots where its README can load them.
#   MODE=release  attach to the GitHub release (public repos; README links to
#                 releases/latest/download).
#   MODE=repo     commit to docs/images on BRANCH (private repos, whose release
#                 assets a plain image link can't reach).
# --check only validates MODE, so a bad input fails before anything is published.
set -euo pipefail

case "${MODE:-}" in
  release | repo) ;;
  *)
    echo "screenshots-to must be release or repo, got: '${MODE:-}'" >&2
    exit 1
    ;;
esac
[ "${1:-}" = "--check" ] && exit 0

files=("$SHOTS/$NAME-hero.jpg" "$SHOTS/$NAME-desktop.jpg" "$SHOTS/$NAME-mobile.jpg")

if [ "$MODE" = release ]; then
  gh release upload "v$VERSION" "${files[@]}" --clobber
  exit 0
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git clone -q --depth 1 --branch "$BRANCH" "$REMOTE_URL" "$work/repo"
mkdir -p "$work/repo/docs/images"
cp "${files[@]}" "$work/repo/docs/images/"
cd "$work/repo"
git add docs/images
if git diff --cached --quiet; then
  echo "Screenshots unchanged"
  exit 0
fi
git -c user.name="github-actions[bot]" \
  -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit -q -m "chore: screenshots for v$VERSION [skip ci]"
git push -q origin "HEAD:$BRANCH"
