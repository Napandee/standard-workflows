"""Tests for scripts/changes.py (the change filter behind changes.yml).

Each test builds a small throwaway git repo, so the script is tested through
its command line exactly as the workflow runs it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "changes.py"
sys.path.insert(0, str(SCRIPT.parent))

from changes import parse_groups, GroupsError  # noqa: E402

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


def run(repo, *args, groups=GROUPS):
    out = Path(repo).parent / "github_output"
    out.write_text("")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--groups", groups, "--output", str(out), *args],
        cwd=repo, capture_output=True, text=True, env={**os.environ, **GIT_ENV},
    )
    result = {}
    for line in out.read_text().splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    if "groups" in result:
        result["groups"] = json.loads(result["groups"])
    return proc, result


def push(repo, before, *extra, groups=GROUPS):
    return run(repo, "--event", "push", "--before", before, *extra, groups=groups)


# ---- parsing the groups input -------------------------------------------

def test_parse_groups_reads_names_and_patterns():
    text = "# comment\n\nui: web 'scripts/check *.mjs'\n  api : server/**  \n"
    assert parse_groups(text) == {"ui": ["web", "scripts/check *.mjs"], "api": ["server/**"]}


@pytest.mark.parametrize("text,needle", [
    ("ui web", "line 1"),                      # no colon
    ("ui:", "no patterns"),                   # no patterns
    ("ui: web\n9x: a", "line 2"),             # bad name
    ("ui: web\nui: other", "twice"),          # duplicate
    ("ui: 'unclosed", "line 1"),             # bad quoting
    ("my group: a", "line 1"),                # space in name
])
def test_parse_groups_rejects_malformed_lines(text, needle):
    with pytest.raises(GroupsError, match=needle):
        parse_groups(text)


def test_malformed_groups_fail_the_run_with_a_clear_message(repo):
    proc, result = push(repo, git(repo, "rev-parse", "HEAD"), groups="ui web")
    assert proc.returncode != 0
    assert "groups" in proc.stderr and "line 1" in proc.stderr
    assert result == {}


# ---- push: compare with the commit before the push -------------------------

def test_push_flags_only_the_touched_group(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/app.js": "let a = 2;\n"})
    proc, result = push(repo, before)
    assert proc.returncode == 0, proc.stderr
    assert result == {"groups": {"web": True, "server": False}, "code": "true"}


def test_glob_pattern_matches(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"server/main.py": "print('bye')\n"})
    _, result = push(repo, before)
    assert result["groups"] == {"web": False, "server": True}


def test_exclude_pathspec_is_honoured(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/app.js": "let a = 3;\n"})
    _, result = push(repo, before, groups="web: web ':!web/app.js'")
    assert result["groups"] == {"web": False}


def test_docs_only_change_is_not_code(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"docs/guide.md": "# Guide 2\n", "web/NOTES.md": "x\n"})
    _, result = push(repo, before)
    assert result == {"groups": {"web": True, "server": False}, "code": "false"}


def test_compares_with_before_across_several_commits(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"server/main.py": "print(1)\n"})
    commit(repo, {"docs/guide.md": "more\n"})
    _, result = push(repo, before)
    assert result == {"groups": {"web": False, "server": True}, "code": "true"}


def test_empty_groups_gives_empty_object(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/app.js": "let a = 9;\n"})
    proc, result = push(repo, before, groups="")
    assert proc.returncode == 0, proc.stderr
    assert result == {"groups": {}, "code": "true"}


def test_bad_pathspec_fails_clearly(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/app.js": "let a = 9;\n"})
    proc, _ = push(repo, before, groups="web: ':(nosuchmagic)web'")
    assert proc.returncode != 0
    assert "web" in proc.stderr


# ---- version numbers -----------------------------------------------------

def test_version_only_change_counts_as_no_change(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/package.json": '{"version": "1.3.0-rc.1"}\n'})
    _, result = push(repo, before)
    assert result["groups"] == {"web": False, "server": False}
    assert result["code"] == "true"  # code is about file names, not lines


def test_version_change_counts_when_ignore_versions_is_off(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/package.json": '{"version": "1.3.0"}\n'})
    _, result = push(repo, before, "--ignore-versions", "false")
    assert result["groups"]["web"] is True


def test_version_and_other_edit_on_one_line_is_a_change(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/package.json": '{"version": "1.3.0", "x": 1}\n'})
    _, result = push(repo, before)
    assert result["groups"]["web"] is True


def test_lines_moved_between_files_are_a_change(repo):
    commit(repo, {"web/a.js": "one\n", "web/b.js": "two\n"})
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/a.js": "two\n", "web/b.js": "one\n"})
    _, result = push(repo, before)
    assert result["groups"]["web"] is True


def test_moved_file_is_a_change(repo):
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "web/app.js", "web/main.js")
    commit(repo, {})
    _, result = push(repo, before)
    assert result["groups"]["web"] is True


def test_new_empty_file_is_a_change(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/empty.js": ""})
    _, result = push(repo, before)
    assert result["groups"]["web"] is True


def test_binary_change_is_a_change(repo):
    commit(repo, {"web/logo.png": b"\x89PNG\x00\x01"})
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/logo.png": b"\x89PNG\x00\x02"})
    _, result = push(repo, before)
    assert result["groups"]["web"] is True


def test_mode_change_is_a_change(repo):
    before = git(repo, "rev-parse", "HEAD")
    (repo / "server/main.py").chmod(0o755)
    commit(repo, {})
    _, result = push(repo, before)
    assert result["groups"]["server"] is True


def test_removed_line_that_looks_like_a_header_is_content(repo):
    commit(repo, {"web/x.txt": "-- a\n++ b\n"})
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"web/x.txt": "-- a\n"})
    _, result = push(repo, before)
    assert result["groups"]["web"] is True


# ---- no usable base: everything runs ----------------------------------------

@pytest.mark.parametrize("before", ["", ZERO])
def test_push_without_before_runs_everything(repo, before):
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


# ---- pull request: the merge checkout's first parent -----------------------

def test_pull_request_compares_with_first_parent(repo):
    git(repo, "switch", "-q", "-c", "topic")
    commit(repo, {"server/main.py": "print('pr')\n"})
    git(repo, "switch", "-q", "main")
    commit(repo, {"web/app.js": "let a = 5;\n"})  # main moved on meanwhile
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "topic")
    proc, result = run(repo, "--event", "pull_request")
    assert proc.returncode == 0, proc.stderr
    assert result == {"groups": {"web": False, "server": True}, "code": "true"}


def test_pull_request_without_parent_runs_everything(tmp_path):
    r = tmp_path / "single"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, {"web/a.js": "1\n"})
    proc, result = run(r, "--event", "pull_request")
    assert result == {"groups": {"web": True, "server": True}, "code": "true"}
    assert "every group" in proc.stdout


# ---- the caller's own workflow -------------------------------------------

def test_caller_workflow_change_runs_every_group(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {".github/workflows/ci.yml": "name: CI 2\n"})
    proc, result = push(repo, before, "--workflow-path", ".github/workflows/ci.yml")
    assert result == {"groups": {"web": True, "server": True}, "code": "true"}
    assert "ci.yml" in proc.stdout


def test_caller_workflow_path_comes_from_workflow_ref(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {".github/workflows/ci.yml": "name: CI 2\n"})
    _, result = push(
        repo, before,
        "--workflow-ref", "someone/proj/.github/workflows/ci.yml@refs/pull/7/merge",
        "--repository", "someone/proj",
    )
    assert result["groups"] == {"web": True, "server": True}


def test_other_workflow_change_does_not_run_every_group(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {".github/workflows/other.yml": "name: Other\n"})
    _, result = push(repo, before, "--workflow-path", ".github/workflows/ci.yml")
    assert result == {"groups": {"web": False, "server": False}, "code": "true"}


def test_workflow_check_can_be_turned_off(repo):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {".github/workflows/ci.yml": "name: CI 2\n"})
    _, result = push(
        repo, before, "--workflow-path", "-",
        "--workflow-ref", "someone/proj/.github/workflows/ci.yml@refs/heads/main",
        "--repository", "someone/proj",
    )
    assert result["groups"] == {"web": False, "server": False}
