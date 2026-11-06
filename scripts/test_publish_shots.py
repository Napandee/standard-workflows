"""Tests for scripts/publish_shots.sh, run through its command line."""
import os
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).with_name("publish_shots.sh")
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                           text=True, env={**os.environ, **ENV}).stdout.strip()


def origin(tmp_path):
    bare = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    seed = tmp_path / "seed"
    git("clone", "-q", str(bare), str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("r\n")
    git("add", "-A", cwd=seed)
    git("commit", "-qm", "first", cwd=seed)
    git("push", "-q", "origin", "HEAD:main", cwd=seed)
    return bare


def shots(tmp_path):
    folder = tmp_path / "shots"
    folder.mkdir(exist_ok=True)
    for kind in ("hero", "desktop", "mobile"):
        (folder / f"app-{kind}.jpg").write_text(kind)
    return folder


def run(tmp_path, mode, bare=None, gh=None):
    env = {**os.environ, **ENV, "MODE": mode, "NAME": "app", "VERSION": "1.2.3",
           "SHOTS": str(tmp_path / "shots"), "BRANCH": "main",
           "REMOTE_URL": str(bare or "")}
    if gh:
        env["PATH"] = f"{gh}:{env['PATH']}"
    return subprocess.run(["bash", str(SCRIPT)], cwd=tmp_path, env=env,
                          capture_output=True, text=True)


def test_unknown_mode_fails_and_names_the_input(tmp_path):
    shots(tmp_path)
    r = run(tmp_path, "bogus")
    assert r.returncode != 0
    assert "screenshots-to must be release or repo" in r.stderr


def test_check_mode_validates_without_publishing(tmp_path):
    env = {**os.environ, "MODE": "bogus"}
    r = subprocess.run(["bash", str(SCRIPT), "--check"], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    env["MODE"] = "repo"
    assert subprocess.run(["bash", str(SCRIPT), "--check"], env=env).returncode == 0


def test_repo_mode_commits_images_to_the_branch(tmp_path):
    bare = origin(tmp_path)
    shots(tmp_path)
    r = run(tmp_path, "repo", bare)
    assert r.returncode == 0, r.stderr
    assert git("--git-dir", str(bare), "show", "main:docs/images/app-hero.jpg", cwd=tmp_path) == "hero"
    message = git("--git-dir", str(bare), "log", "-1", "--format=%s", "main", cwd=tmp_path)
    assert message == "chore: screenshots for v1.2.3 [skip ci]"


def test_repo_mode_with_unchanged_images_succeeds_without_a_commit(tmp_path):
    bare = origin(tmp_path)
    shots(tmp_path)
    assert run(tmp_path, "repo", bare).returncode == 0
    head = git("--git-dir", str(bare), "rev-parse", "main", cwd=tmp_path)
    r = run(tmp_path, "repo", bare)
    assert r.returncode == 0, r.stderr
    assert git("--git-dir", str(bare), "rev-parse", "main", cwd=tmp_path) == head


def test_release_mode_uploads_to_the_release(tmp_path):
    shots(tmp_path)
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "gh.log"
    (fake / "gh").write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    (fake / "gh").chmod(0o755)
    r = run(tmp_path, "release", gh=fake)
    assert r.returncode == 0, r.stderr
    text = log.read_text()
    assert text.startswith("release upload v1.2.3 ") and "app-hero.jpg" in text and "--clobber" in text
