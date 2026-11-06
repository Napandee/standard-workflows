#!/usr/bin/env python3
"""Decide which named path groups a change touches (behind changes.yml).

Usage (run inside the checkout of the project):

  changes.py --groups TEXT [--event NAME] [--before SHA] [--ref REF]
             [--default-branch NAME] [--full-run-branches "NAME ..."]
             [--version-files "PATHSPEC ..."]
             [--workflow-path PATH | --workflow-ref REF --repository OWNER/NAME]
             [--output FILE]

TEXT holds one group per line, `name: pattern pattern ...` (git pathspecs,
shell-style quoting allowed; blank lines and `#` comments are skipped), or
just `none` for `code` alone. Every pattern must match a file at HEAD. The
result goes to FILE (normally $GITHUB_OUTPUT) as two lines:

  groups={"name": true, ...}    one flag per group
  code=true|false               anything changed outside docs/ and *.md

Every group and `code` are true, with the reason logged, when:
  - the event is a push to a full-run branch (--full-run-branches, else
    --default-branch), or a push with neither known;
  - there is no usable base: pull_request compares with HEAD^1 (the base
    branch in the merge checkout); push and merge_group with --before, fetched
    shallow from origin when missing (FETCH_TOKEN in the environment is sent
    as the auth header for that fetch only); any other event has none;
  - the caller's own workflow file changed.

A group counts as touched when any of its files changed. Only in a file
matched by --version-files does a change that moves version numbers and
nothing else (1.2.3, 1.2.3-rc.4; never part of a 4-or-more-part run like an
IP address) count as no change; lines are compared in order, hunk by hunk.

Exits 2 with a message on a malformed --groups, a pattern that matches no file
at HEAD, or one git refuses.
"""
import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
VERSION = re.compile(r"(?<![\d.])\d+\.\d+\.\d+(?:-(?:rc|dev|alpha|beta)\.\d+)?(?![\d.]*\d)")
SHA = re.compile(r"^[0-9a-f]{40,64}$")
DOCS = re.compile(r"^docs/|\.md$")
NO_NEWLINE = "\\ No newline at end of file"


class GroupsError(ValueError):
    pass


def parse_groups(text):
    """Parse the groups input into {name: [pathspec, ...]}, in input order."""
    if text.strip() == "none":
        return {}
    groups = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, colon, rest = line.partition(":")
        name = name.strip()
        where = f"groups line {number} ({raw.strip()!r})"
        if not colon:
            raise GroupsError(f"{where}: expected 'name: pattern pattern ...'")
        if not NAME.match(name):
            raise GroupsError(
                f"{where}: group name must be letters, digits, '_' or '-', "
                "starting with a letter or '_'")
        if name in groups:
            raise GroupsError(f"{where}: group {name!r} is defined twice")
        try:
            patterns = shlex.split(rest)
        except ValueError as err:
            raise GroupsError(f"{where}: {err}") from None
        if not patterns:
            raise GroupsError(f"{where}: group {name!r} has no patterns")
        groups[name] = patterns
    if not groups:
        raise GroupsError("no groups given; list at least one, or write 'none' for code only")
    return groups


def git(*args, check=True, env=None):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=check,
        errors="surrogateescape", env={**os.environ, **(env or {})},
    )


def fetch_env(environ, url):
    """Git config, as environment, that sends FETCH_TOKEN as the auth header
    to `url` (origin) only, added after any GIT_CONFIG_* entries already set."""
    token = environ.get("FETCH_TOKEN")
    if not token:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    n = int(environ.get("GIT_CONFIG_COUNT") or 0)
    return {"GIT_CONFIG_COUNT": str(n + 1), f"GIT_CONFIG_KEY_{n}": f"http.{url}.extraheader",
            f"GIT_CONFIG_VALUE_{n}": f"AUTHORIZATION: basic {basic}"}


def is_commit(ref):
    return git("rev-parse", "-q", "--verify", "--end-of-options",
               f"{ref}^{{commit}}", check=False).returncode == 0


def positive(spec):
    """The pathspec with exclude magic removed, to check it matches a file."""
    if spec.startswith((":!", ":^")):
        return spec[2:] or "."
    long = re.match(r"^:\(([^)]*)\)(.*)$", spec, re.S)
    if long:
        magic = [m for m in long.group(1).split(",") if m.strip() not in ("exclude", "")]
        rest = long.group(2) or "."
        return f":({','.join(magic)}){rest}" if magic else rest
    return spec


def check_patterns(owner, patterns):
    """Fail unless every pattern matches at least one file at HEAD."""
    for pattern in patterns:
        listed = git("ls-files", "-z", "--", positive(pattern), check=False)
        if listed.returncode != 0:
            raise GroupsError(f"{owner}: git refused the pattern {pattern!r}: {listed.stderr.strip()}")
        if not listed.stdout:
            raise GroupsError(f"{owner}: the pattern {pattern!r} matches no file at HEAD; "
                              "remove or update the pattern in the caller's groups")


def find_base(event, before):
    """Return (base, None) or (None, reason everything runs)."""
    if event == "pull_request":
        if is_commit("HEAD^1"):
            return "HEAD^1", None
        return None, "the pull request checkout has no first parent (HEAD^1)"
    if event in ("push", "merge_group"):
        if not before or not before.strip("0"):
            return None, f"no earlier commit to compare with (before={before or 'empty'}; a new branch or tag)"
        if not SHA.match(before):
            return None, f"before={before!r} is not a full commit sha"
        if not is_commit(before):
            url = git("remote", "get-url", "origin", check=False).stdout.strip()
            fetch = git("fetch", "-q", "--no-tags", "--depth=1", "--end-of-options",
                        "origin", before, check=False, env=fetch_env(os.environ, url))
            if fetch.returncode != 0 or not is_commit(before):
                return None, f"cannot fetch the commit before the push ({before}): {fetch.stderr.strip()}"
        return before, None
    return None, f"event {event or 'unknown'!r} has no base to compare with"


def changed_files(base, pathspecs=()):
    out = git("diff", "--no-renames", "--name-only", "-z", "--end-of-options",
              base, "HEAD", "--", *pathspecs).stdout
    return [f for f in out.split("\0") if f]


def version_only_files(base, pathspecs):
    """Files under `pathspecs` whose every hunk only moves version numbers.

    One -U0 patch is read for all of them. A file added, deleted, with a mode
    or binary change, or whose path cannot be read back from its header
    counts as changed.
    """
    if not pathspecs:
        return set()
    patch = git(
        "-c", "core.quotePath=false", "diff", "--no-renames", "--no-color",
        "--no-ext-diff", "--no-textconv", "-U0", "--end-of-options",
        base, "HEAD", "--", *pathspecs,
    ).stdout
    files = []          # [path or None, structural, [(minus, plus), ...]]
    hunk = None
    last = None         # the list the previous content line went to
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            header = line[len("diff --git "):]
            half = (len(header) - 1) // 2
            a, b = header[:half], header[half + 1:]
            path = a[2:] if a.startswith("a/") and b.startswith("b/") and a[2:] == b[2:] else None
            files.append([path, False, []])
            hunk = None
        elif not files:
            continue
        elif hunk is not None and line[:1] in ("+", "-"):
            last = hunk[0] if line[0] == "-" else hunk[1]
            last.append(line[1:])
        elif hunk is not None and line == NO_NEWLINE and last:
            last[-1] += "\0no-newline"
        elif line.startswith("@@"):
            hunk = ([], [])
            files[-1][2].append(hunk)
            last = None
        elif hunk is None and line.startswith((
                "new file mode", "deleted file mode", "old mode", "new mode",
                "Binary files", "GIT binary patch")):
            files[-1][1] = True

    def norm(lines):
        return [VERSION.sub("X", ln) for ln in lines]

    return {
        path for path, structural, hunks in files
        if path and not structural and hunks
        and all(norm(minus) == norm(plus) for minus, plus in hunks)
    }


def caller_workflow(path, ref, repository):
    if path == "-":
        return None
    if path:
        return path
    if ref and repository and ref.lower().startswith(repository.lower() + "/"):
        return ref[len(repository) + 1:].split("@", 1)[0]
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--groups", required=True)
    p.add_argument("--event", default="")
    p.add_argument("--before", default="")
    p.add_argument("--ref", default="")
    p.add_argument("--default-branch", default="")
    p.add_argument("--full-run-branches", default="")
    p.add_argument("--version-files", default="")
    p.add_argument("--workflow-path", default="")
    p.add_argument("--workflow-ref", default="")
    p.add_argument("--repository", default="")
    p.add_argument("--output", help="file to append name=value lines to")
    args = p.parse_args(argv)

    try:
        groups = parse_groups(args.groups)
        version_specs = shlex.split(args.version_files)
        for name, patterns in groups.items():
            check_patterns(f"group {name!r}", patterns)
        check_patterns("version-files", version_specs)
    except (GroupsError, ValueError) as err:
        print(f"error: invalid groups input: {err}", file=sys.stderr)
        return 2

    def finish(flags, code):
        lines = [f"groups={json.dumps(flags, separators=(',', ':'))}",
                 f"code={'true' if code else 'false'}"]
        print("\n".join(lines))
        if args.output:
            with open(args.output, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        return 0

    def everything(reason):
        print(f"{reason}: every group is true")
        return finish({name: True for name in groups}, True)

    full_run = args.full_run_branches.split() or [args.default_branch] * bool(args.default_branch)
    if args.event == "push":
        if not full_run:
            return everything("default branch unknown and no full-run-branches given")
        branch = args.ref[len("refs/heads/"):] if args.ref.startswith("refs/heads/") else None
        if branch in full_run:
            return everything(f"a push to {branch} runs everything")

    base, reason = find_base(args.event, args.before)
    if base is None:
        return everything(reason)
    sha = git("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").stdout.strip()
    print(f"comparing HEAD with {base} ({sha})")

    files = changed_files(base)
    own = caller_workflow(args.workflow_path, args.workflow_ref, args.repository)
    print(f"caller workflow: {own or 'none found'}")
    if own and own.lower() in {f.lower() for f in files}:
        return everything(f"the calling workflow {own} changed")

    version_only = version_only_files(base, version_specs)
    for path in sorted(version_only):
        print(f"{path}: only version numbers moved, not counted")

    flags = {}
    for name, pathspecs in groups.items():
        hits = [f for f in changed_files(base, pathspecs) if f not in version_only]
        flags[name] = bool(hits)
        shown = ", ".join(hits[:5]) + (f" and {len(hits) - 5} more" if len(hits) > 5 else "")
        print(f"{name}: {'true' if hits else 'false'}" + (f" ({shown})" if hits else ""))

    code = any(not DOCS.search(f) for f in files)
    print(f"code: {'true' if code else 'false'} ({len(files)} files changed)")
    return finish(flags, code)


if __name__ == "__main__":
    sys.exit(main())
