#!/usr/bin/env python3
"""Compute the next release version from the repo's `v*` git tags.

Usage: next_version.py [--first X.Y.Z] <patch|minor|major>   (prints X.Y.Z;
--first is the version used when no vX.Y.Z tag exists yet, default 1.0.0)

Only strict `vMAJOR.MINOR.PATCH` tags count; anything else (v1.2, latest,
v1.2.3-rc.1) is ignored. Exits non-zero if the computed tag already exists.
"""
import re
import subprocess
import sys

SEMVER_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
BUMPS = ("patch", "minor", "major")


def next_version(tags, bump, first="1.0.0"):
    if bump not in BUMPS:
        raise ValueError(f"bump must be one of {BUMPS}, got {bump!r}")
    found = []
    for tag in tags:
        m = SEMVER_TAG.match(tag)
        if m:
            found.append(tuple(int(g) for g in m.groups()))
    if not found:
        return first
    major, minor, patch = max(found)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _git_tags():
    out = subprocess.run(
        ["git", "tag", "--list", "v*"], check=True, capture_output=True, text=True
    ).stdout
    return out.split()


def main(argv=None, tags=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    first = "1.0.0"
    if len(argv) >= 2 and argv[0] == "--first":
        first = argv[1]
        del argv[:2]
    if len(argv) != 1 or argv[0] not in BUMPS or not SEMVER_TAG.match(f"v{first}"):
        sys.exit(f"usage: next_version.py [--first X.Y.Z] <{'|'.join(BUMPS)}>")
    tags = _git_tags() if tags is None else tags
    version = next_version(tags, argv[0], first)
    if f"v{version}" in tags:
        sys.exit(f"refusing: tag v{version} already exists")
    print(version)


if __name__ == "__main__":
    main()
