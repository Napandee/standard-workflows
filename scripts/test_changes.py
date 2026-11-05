"""Tests for scripts/changes.py (the change filter behind changes.yml).

Each test builds a small throwaway git repo, so the script is tested through
its command line exactly as the workflow runs it.
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "changes.py"
sys.path.insert(0, str(SCRIPT.parent))

from changes import parse_groups, fetch_env, GroupsError  # noqa: E402

ZERO = "0" * 40
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, env={**os.environ, **GIT_ENV},
    ).stdout.strip()


def write(repo, files):
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def commit(repo, files, msg="c"):
    write(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, {
        "web/app.js": "let a = 1;\n",
        "web/package.json": '{"version": "1.2.3"}\n',
        "server/main.py": "print('hi')\n",
        "docs/guide.md": "# Guide\n",
        "README.md": "# Readme\n",
        ".github/workflows/ci.yml": "name: CI\n",
    }, "base")
    return r


GROUPS = """
web: web
server: server/*.py
"""


def run(repo, *args, groups=GROUPS, env=None):
    out = Path(repo).parent / "github_output"
    out.write_text("")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--groups", groups, "--output", str(out), *args],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, **GIT_ENV, **(env or {})},
    )
    result = {}
    for line in out.read_text().splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    if "groups" in result:
        result["groups"] = json.loads(result["groups"])
    return proc, result


def push(repo, before, *extra, groups=GROUPS):
    return run(repo, "--event", "push", f"--before={before}", *extra, groups=groups)


def change(repo, files, *extra, groups=GROUPS):
    """Commit `files` on top of HEAD and run the filter as a push from HEAD."""
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, files)
    return push(repo, before, *extra, groups=groups)


# ---- parsing the groups input -------------------------------------------

def test_parse_groups_reads_names_and_patterns():
    text = "# comment\n\nui: web 'scripts/check *.mjs'\n  api : server/**  \n"
    assert parse_groups(text) == {"ui": ["web", "scripts/check *.mjs"], "api": ["server/**"]}


def test_parse_groups_none_means_code_only():
    assert parse_groups("  none \n") == {}


@pytest.mark.parametrize("text,needle", [
    ("ui web", "line 1"),                      # no colon
    ("ui:", "no patterns"),                   # no patterns
    ("ui: web\n9x: a", "line 2"),             # bad name
    ("ui: web\nui: other", "twice"),          # duplicate
    ("ui: 'unclosed", "line 1"),             # bad quoting
    ("my group: a", "line 1"),                # space in name
    ("", "none"),                             # empty: say 'none' for code only
    ("# only a comment\n\n", "none"),
])
def test_parse_groups_rejects_malformed_input(text, needle):
    with pytest.raises(GroupsError, match=needle):
        parse_groups(text)


def test_malformed_groups_fail_the_run_with_a_clear_message(repo):
    proc, result = push(repo, git(repo, "rev-parse", "HEAD"), groups="ui web")
    assert proc.returncode != 0
    assert "groups" in proc.stderr and "line 1" in proc.stderr
    assert result == {}


def test_empty_groups_fail_the_run(repo):
    proc, result = push(repo, git(repo, "rev-parse", "HEAD"), groups="")
    assert proc.returncode != 0
    assert "none" in proc.stderr
    assert result == {}


def test_groups_none_gives_code_only(repo):
    proc, result = change(repo, {"web/app.js": "let a = 9;\n"}, groups="none")
    assert proc.returncode == 0, proc.stderr
    assert result == {"groups": {}, "code": "true"}


# ---- patterns must match something at HEAD ---------------------------------

@pytest.mark.parametrize("pattern", ["does-not-exist", "'web/*.ts'", "':!web/gone.js'"])
def test_pattern_matching_no_file_fails_naming_group_and_pattern(repo, pattern):
    proc, result = change(repo, {"web/app.js": "let a = 9;\n"},
                          groups=f"web: web {pattern}")
    assert proc.returncode != 0
    assert "web" in proc.stderr and pattern.strip("'") in proc.stderr
    assert result == {}


def test_version_files_pattern_matching_no_file_fails(repo):
    proc, _ = change(repo, {"web/app.js": "let a = 9;\n"},
                     "--version-files", "web/nope.json")
    assert proc.returncode != 0
    assert "version-files" in proc.stderr and "web/nope.json" in proc.stderr


def test_bad_pathspec_fails_clearly(repo):
    proc, _ = change(repo, {"web/app.js": "let a = 9;\n"},
                     groups="web: ':(nosuchmagic)web'")
    assert proc.returncode != 0
    assert "web" in proc.stderr


# ---- push: compare with the commit before the push -------------------------

def test_push_flags_only_the_touched_group(repo):
    proc, result = change(repo, {"web/app.js": "let a = 2;\n"})
    assert proc.returncode == 0, proc.stderr
    assert "--end-of-options" not in proc.stdout
    assert result == {"groups": {"web": True, "server": False}, "code": "true"}


def test_glob_pattern_matches(repo):
    _, result = change(repo, {"server/main.py": "print('bye')\n"})
    assert result["groups"] == {"web": False, "server": True}


def test_exclude_pathspec_is_honoured(repo):
    _, result = change(repo, {"web/app.js": "let a = 3;\n"},
                       groups="web: web ':!web/app.js'")
    assert result["groups"] == {"web": False}


def test_docs_only_change_is_not_code(repo):
    _, result = change(repo, {"docs/guide.md": "# Guide 2\n", "web/NOTES.md": "x\n"})
    assert result == {"groups": {"web": True, "server": False}, "code": "false"}


def test_compares_with_before_across_several_commits(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"server/main.py": "print(1)\n"})
    commit(repo, {"docs/guide.md": "more\n"})
    _, result = push(repo, before)
    assert result == {"groups": {"web": False, "server": True}, "code": "true"}


# ---- every changed line counts outside version-files ------------------------

@pytest.mark.parametrize("files,group", [
    ({"web/package.json": '{"version": "1.3.0"}\n'}, "web"),                 # version bump
    ({"server/requirements.txt": "requests==2.32.0\n"}, "server"),           # requirements bump
    ({"server/Dockerfile": "FROM python:3.12.2-slim\n"}, "server"),          # base image tag
    ({"server/Dockerfile": "FROM python:3.12.1-alpine\n"}, "server"),        # image suffix
    ({"server/net.conf": "allow 198.51.100.0/16\n"}, "server"),              # CIDR
    ({"server/net.conf": "allow 198.51.200.7\n"}, "server"),                 # IP
])
def test_version_like_changes_count_by_default(repo, files, group):
    commit(repo, {"server/requirements.txt": "requests==2.31.0\n",
                  "server/Dockerfile": "FROM python:3.12.1-slim\n",
                  "server/net.conf": "allow 198.51.100.0/24\nallow 198.51.100.7\n"})
    _, result = change(repo, files, groups="web: web\nserver: server")
    assert result["groups"][group] is True


def test_lines_moved_between_files_are_a_change(repo):
    commit(repo, {"web/a.js": "one\n", "web/b.js": "two\n"})
    _, result = change(repo, {"web/a.js": "two\n", "web/b.js": "one\n"})
    assert result["groups"]["web"] is True


# ---- version-files: version-only moves there are no change ------------------

VF = ("--version-files", "web/package.json server/pins.txt")
WIDE = "web: web\nserver: server"


@pytest.fixture
def pinned(repo):
    commit(repo, {"server/pins.txt": (
        "app 1.2.3\n"
        "lib 4.5.6-rc.1\n"
        "base 1.2.3-slim\n"
        "addr 198.51.100.7\n"
        "net 198.51.100.0/24\n"
        "long 1.2.3.4\n"
        "middleware a\nmiddleware b\nmiddleware c\n"
        "last")})                         # no newline at end
    return repo


def pins(**replace):
    lines = {
        "app": "app 1.2.3\n", "lib": "lib 4.5.6-rc.1\n", "base": "base 1.2.3-slim\n",
        "addr": "addr 198.51.100.7\n", "net": "net 198.51.100.0/24\n",
        "long": "long 1.2.3.4\n",
        "mw": "middleware a\nmiddleware b\nmiddleware c\n", "last": "last",
    }
    lines.update(replace)
    return {"server/pins.txt": "".join(lines.values())}


def test_version_move_in_listed_file_is_no_change(repo):
    _, result = change(repo, {"web/package.json": '{"version": "1.3.0-rc.1"}\n'},
                       "--version-files", "web/package.json")
    assert result["groups"] == {"web": False, "server": False}
    assert result["code"] == "true"  # code is about file names, not lines


@pytest.mark.parametrize("replace", [
    {"app": "app 2.0.0\n"},
    {"lib": "lib 4.5.7-dev.3\n"},
    {"app": "app 1.2.4\n", "lib": "lib 4.6.0-beta.2\n"},
])
def test_version_moves_in_listed_file_are_no_change(pinned, replace):
    proc, result = change(pinned, pins(**replace), *VF, groups=WIDE)
    assert proc.returncode == 0, proc.stderr
    assert result["groups"]["server"] is False


@pytest.mark.parametrize("name,replace", [
    ("other suffix", {"lib": "lib 4.5.6-foo.1\n"}),
    ("image suffix", {"base": "base 1.2.3-alpine\n"}),
    ("ip address", {"addr": "addr 198.51.200.7\n"}),
    ("cidr", {"net": "net 198.51.100.0/16\n"}),
    ("four-part run", {"long": "long 1.2.3.5\n"}),
    ("version and edit", {"app": "app 1.2.4 extra\n"}),
    ("swapped pair", {"app": "lib 4.5.6-rc.1\n", "lib": "app 1.2.3\n"}),
    ("reordered list", {"mw": "middleware c\nmiddleware a\nmiddleware b\n"}),
    # one hunk whose lines swap places while their versions move
    ("swap within a hunk", {"app": "lib 4.5.7-rc.1\n", "lib": "app 1.2.4\n"}),
    ("trailing newline", {"last": "last\n"}),
])
def test_other_changes_in_listed_file_count(pinned, name, replace):
    proc, result = change(pinned, pins(**replace), *VF, groups=WIDE)
    assert proc.returncode == 0, proc.stderr
    assert result["groups"]["server"] is True, name


@pytest.mark.parametrize("vf", [(), ("--version-files", "web")])
def test_new_empty_file_is_a_change(repo, vf):
    _, result = change(repo, {"web/empty.js": ""}, *vf)
    assert result["groups"]["web"] is True


@pytest.mark.parametrize("vf", [(), ("--version-files", "web")])
def test_moved_file_is_a_change(repo, vf):
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "web/app.js", "web/main.js")
    commit(repo, {})
    _, result = push(repo, before, *vf)
    assert result["groups"]["web"] is True


@pytest.mark.parametrize("vf", [(), ("--version-files", "web")])
def test_binary_change_is_a_change(repo, vf):
    commit(repo, {"web/logo.png": b"\x89PNG\x00\x01"})
    _, result = change(repo, {"web/logo.png": b"\x89PNG\x00\x02"}, *vf)
    assert result["groups"]["web"] is True


@pytest.mark.parametrize("vf", [(), ("--version-files", "server")])
def test_mode_change_is_a_change(repo, vf):
    before = git(repo, "rev-parse", "HEAD")
    (repo / "server/main.py").chmod(0o755)
    commit(repo, {})
    _, result = push(repo, before, *vf)
    assert result["groups"]["server"] is True


def test_removed_line_that_looks_like_a_header_is_content(repo):
    commit(repo, {"web/x.txt": "-- a\n++ b\n"})
    _, result = change(repo, {"web/x.txt": "-- a\n"}, "--version-files", "web/x.txt")
    assert result["groups"]["web"] is True


# ---- a push to the default branch runs everything ---------------------------

def test_push_to_default_branch_runs_everything(repo):
    proc, result = change(repo, {"docs/guide.md": "x\n"},
                          "--ref", "refs/heads/main", "--default-branch", "main")
    assert result == {"groups": {"web": True, "server": True}, "code": "true"}
    assert "main" in proc.stdout and "every group" in proc.stdout


def test_push_to_other_branch_is_filtered(repo):
    _, result = change(repo, {"docs/guide.md": "x\n"},
                       "--ref", "refs/heads/topic", "--default-branch", "main")
    assert result == {"groups": {"web": False, "server": False}, "code": "false"}


def test_named_full_run_branches_replace_the_default(repo):
    args = ("--default-branch", "main", "--full-run-branches", "release stable")
    _, on_main = change(repo, {"docs/guide.md": "x\n"}, "--ref", "refs/heads/main", *args)
    _, on_stable = change(repo, {"docs/guide.md": "y\n"}, "--ref", "refs/heads/stable", *args)
    assert on_main["groups"] == {"web": False, "server": False}
    assert on_stable["groups"] == {"web": True, "server": True}


# ---- no usable base: everything runs ----------------------------------------

@pytest.mark.parametrize("before", ["", ZERO, "not-a-sha", "1234567", "--upload-pack=x",
                                    "HEAD~1", "main"])
def test_push_without_usable_before_runs_everything(repo, before):
    commit(repo, {"docs/guide.md": "x\n"})
    proc, result = push(repo, before)
    assert proc.returncode == 0, proc.stderr
    assert result == {"groups": {"web": True, "server": True}, "code": "true"}
    assert "every group" in proc.stdout


def test_push_with_unfetchable_before_runs_everything(repo):
    commit(repo, {"docs/guide.md": "x\n"})
    proc, result = push(repo, "1234567" * 5 + "12345")
    assert proc.returncode == 0, proc.stderr
    assert result == {"groups": {"web": True, "server": True}, "code": "true"}
    assert "every group" in proc.stdout


def test_other_events_run_everything(repo):
    proc, result = run(repo, "--event", "workflow_dispatch")
    assert result == {"groups": {"web": True, "server": True}, "code": "true"}
    assert "workflow_dispatch" in proc.stdout


def test_push_fetches_before_from_origin_when_missing(tmp_path, repo):
    # A shallow clone (as actions/checkout makes) lacks the commit before a
    # multi-commit push; the script fetches it from origin.
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"docs/guide.md": "x\n"})
    commit(repo, {"server/main.py": "print(2)\n"})
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth=1", "file://" + str(repo), str(clone))
    with pytest.raises(subprocess.CalledProcessError):
        git(clone, "cat-file", "-e", before)
    proc, result = push(clone, before)
    assert proc.returncode == 0, proc.stderr
    assert result == {"groups": {"web": False, "server": True}, "code": "true"}


def test_fetch_env_passes_the_token_as_a_header_only_when_given():
    assert fetch_env({}) == {}
    env = fetch_env({"FETCH_TOKEN": "abc"})
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
    expected = base64.b64encode(b"x-access-token:abc").decode()
    assert env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {expected}"


# ---- pull request: the merge checkout's first parent -----------------------

def test_pull_request_compares_with_first_parent(repo):
    git(repo, "switch", "-q", "-c", "topic")
    commit(repo, {"server/main.py": "print('pr')\n"})
    git(repo, "switch", "-q", "main")
    commit(repo, {"web/app.js": "let a = 5;\n"})  # main moved on meanwhile
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "topic")
    proc, result = run(repo, "--event", "pull_request", "--ref", "refs/pull/1/merge",
                       "--default-branch", "main")
    assert proc.returncode == 0, proc.stderr
    assert result == {"groups": {"web": False, "server": True}, "code": "true"}


def test_pull_request_without_parent_runs_everything(tmp_path):
    r = tmp_path / "single"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, {"web/a.js": "1\n", "server/a.py": "1\n"})
    proc, result = run(r, "--event", "pull_request")
    assert result == {"groups": {"web": True, "server": True}, "code": "true"}
    assert "every group" in proc.stdout


# ---- the caller's own workflow -------------------------------------------

def test_caller_workflow_change_runs_every_group(repo):
    proc, result = change(repo, {".github/workflows/ci.yml": "name: CI 2\n"},
                          "--workflow-path", ".github/workflows/ci.yml")
    assert result == {"groups": {"web": True, "server": True}, "code": "true"}
    assert "ci.yml" in proc.stdout


def test_caller_workflow_path_comes_from_workflow_ref_and_is_logged(repo):
    proc, result = change(
        repo, {".github/workflows/ci.yml": "name: CI 2\n"},
        "--workflow-ref", "someone/proj/.github/workflows/CI.yml@refs/pull/7/merge",
        "--repository", "someone/proj",
    )
    assert result["groups"] == {"web": True, "server": True}
    assert "caller workflow: .github/workflows/CI.yml" in proc.stdout


def test_missing_caller_workflow_is_logged(repo):
    proc, _ = change(repo, {"web/app.js": "let a = 9;\n"})
    assert "caller workflow: none found" in proc.stdout


def test_other_workflow_change_does_not_run_every_group(repo):
    _, result = change(repo, {".github/workflows/other.yml": "name: Other\n"},
                       "--workflow-path", ".github/workflows/ci.yml")
    assert result == {"groups": {"web": False, "server": False}, "code": "true"}


def test_workflow_check_can_be_turned_off(repo):
    _, result = change(
        repo, {".github/workflows/ci.yml": "name: CI 2\n"}, "--workflow-path", "-",
        "--workflow-ref", "someone/proj/.github/workflows/ci.yml@refs/heads/main",
        "--repository", "someone/proj",
    )
    assert result["groups"] == {"web": False, "server": False}
