#!/usr/bin/env python3
"""Decide which named path groups a change touches (behind changes.yml).

Usage (run inside the checkout of the project):

  changes.py --groups TEXT [--event NAME] [--before SHA]
             [--ignore-versions true|false]
             [--workflow-path PATH | --workflow-ref REF --repository OWNER/NAME]
             [--output FILE]

TEXT holds one group per line, `name: pattern pattern ...` (git pathspecs,
shell-style quoting allowed; blank lines and `#` comments are skipped). The
result goes to FILE (normally $GITHUB_OUTPUT) as two lines:

  groups={"name": true, ...}    one flag per group
  code=true|false               anything changed outside docs/ and *.md

The base of the comparison:
  pull_request   HEAD^1, the base branch in the merge checkout
  push           --before, fetched shallow from origin when missing
                 (merge_group passes its base_sha the same way)
  anything else  none
With no usable base, every group and `code` are true and the reason is logged.
Every group is also true when the caller's own workflow file changed.

A group counts as touched when one of its files was added, deleted, had its
mode or binary content changed, or had text lines changed. With
--ignore-versions true (the default) a file whose changed lines differ only in
version numbers (1.2.3, 1.2.3-rc.1) does not count.

Exits 2 with a message on a malformed --groups or a pathspec git refuses.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from collections import Counter

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.]*)?")
ZERO_SHA = re.compile(r"^0+$")
DOCS = re.compile(r"^docs/|\.md$")


class GroupsError(ValueError):
    pass


def parse_groups(text):
    """Parse the groups input into {name: [pathspec, ...]}, in input order."""
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
    return groups


def git(*args, check=True):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=check,
        errors="surrogateescape",
    )


def is_commit(ref):
    return git("rev-parse", "-q", "--verify", f"{ref}^{{commit}}", check=False).returncode == 0


def find_base(event, before):
    """Return (base, None) or (None, reason everything runs)."""
    if event == "pull_request":
        if is_commit("HEAD^1"):
            return "HEAD^1", None
        return None, "the pull request checkout has no first parent (HEAD^1)"
    if event in ("push", "merge_group"):
        if not before or ZERO_SHA.match(before):
            return None, f"no earlier commit to compare with (before={before or 'empty'}; a new branch or tag)"
        if not is_commit(before):
            fetch = git("fetch", "-q", "--no-tags", "--depth=1", "origin", before, check=False)
            if fetch.returncode != 0 or not is_commit(before):
                return None, f"cannot fetch the commit before the push ({before}): {fetch.stderr.strip()}"
        return before, None
    return None, f"event {event or 'unknown'!r} has no base to compare with"


def changed_files(base, pathspecs=()):
    out = git("diff", "--no-renames", "--name-only", "-z", base, "HEAD", "--", *pathspecs).stdout
    return [f for f in out.split("\0") if f]


def file_diffs(base, pathspecs):
    """Yield (path-header, changed, plus lines, minus lines) per file in a -U0 patch.

    `changed` is True when the file changed in a way lines cannot show: added,
    deleted, mode or binary change.
    """
    patch = git(
        "diff", "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv",
        "-U0", base, "HEAD", "--", *pathspecs,
    ).stdout
    current = None
    in_hunk = False
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            if current:
                yield current
            current = [line[len("diff --git "):], False, [], []]
            in_hunk = False
        elif current is None:
            continue
        elif in_hunk:
            if line.startswith("+"):
                current[2].append(line[1:])
            elif line.startswith("-"):
                current[3].append(line[1:])
        elif line.startswith("@@"):
            in_hunk = True
        elif line.startswith(("new file mode", "deleted file mode", "old mode",
                              "new mode", "Binary files", "GIT binary patch")):
            current[1] = True
    if current:
        yield current


def touched(base, pathspecs, ignore_versions):
    """Return the files that count as a change for this group."""
    if not ignore_versions:
        return changed_files(base, pathspecs)
    hits = []
    for header, structural, plus, minus in file_diffs(base, pathspecs):
        if structural:
            hits.append(header.rsplit(" b/", 1)[-1])
            continue
        norm = lambda lines: Counter(VERSION.sub("X", ln) for ln in lines)  # noqa: E731
        if norm(plus) != norm(minus):
            hits.append(header.rsplit(" b/", 1)[-1])
    return hits


def workflow_path(path, ref, repository):
    if path == "-":
        return None
    if path:
        return path
    if ref and repository and ref.startswith(repository + "/"):
        return ref[len(repository) + 1:].split("@", 1)[0]
    return None


def bool_arg(value):
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--groups", required=True)
    p.add_argument("--event", default="")
    p.add_argument("--before", default="")
    p.add_argument("--ignore-versions", type=bool_arg, default=True)
    p.add_argument("--workflow-path", default="")
    p.add_argument("--workflow-ref", default="")
    p.add_argument("--repository", default="")
    p.add_argument("--output", help="file to append name=value lines to")
    args = p.parse_args(argv)

    try:
        groups = parse_groups(args.groups)
    except GroupsError as err:
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

    base, reason = find_base(args.event, args.before)
    if base is None:
        return everything(reason)
    print(f"comparing HEAD with {base} ({git('rev-parse', base).stdout.strip()})")

    files = changed_files(base)
    own = workflow_path(args.workflow_path, args.workflow_ref, args.repository)
    if own and own in files:
        return everything(f"the calling workflow {own} changed")

    flags = {}
    for name, pathspecs in groups.items():
        try:
            hits = touched(base, pathspecs, args.ignore_versions)
        except subprocess.CalledProcessError as err:
            print(f"error: group {name!r}: git refused its patterns {pathspecs}: "
                  f"{err.stderr.strip()}", file=sys.stderr)
            return 2
        flags[name] = bool(hits)
        shown = ", ".join(hits[:5]) + (f" and {len(hits) - 5} more" if len(hits) > 5 else "")
        print(f"{name}: {'true' if hits else 'false'}" + (f" ({shown})" if hits else ""))

    code = any(not DOCS.search(f) for f in files)
    print(f"code: {'true' if code else 'false'} ({len(files)} files changed)")
    return finish(flags, code)


if __name__ == "__main__":
    sys.exit(main())
