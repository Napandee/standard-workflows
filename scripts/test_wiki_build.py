"""Tests for wiki_build: what a reader of the published wiki gets."""
import pathlib
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).with_name("wiki_build.py")


def build(tmp_path, files, *sources, home="docs/guide/index.md"):
    repo = tmp_path / "repo"
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = tmp_path / "wiki"
    return out, subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), "--home", home,
         "--repo-url", "https://github.com/o/r/blob/main", *sources],
        cwd=repo, capture_output=True, text=True)


def test_index_becomes_home(tmp_path):
    out, r = build(tmp_path, {"docs/guide/index.md": "# Guide\n"}, "docs/guide")
    assert r.returncode == 0, r.stderr
    assert (out / "Home.md").read_text() == "# Guide\n"
    assert not (out / "index.md").exists()


def test_links_between_pages_lose_path_and_extension(tmp_path):
    out, r = build(tmp_path, {
        "docs/guide/index.md": "[a](stats.md) [b](../admin/install.md#token)\n",
        "docs/guide/stats.md": "[home](index.md)\n",
        "docs/admin/install.md": "x\n"}, "docs/guide", "docs/admin")
    assert r.returncode == 0, r.stderr
    assert (out / "Home.md").read_text() == "[a](stats) [b](install#token)\n"
    assert (out / "stats.md").read_text() == "[home](Home)\n"


def test_single_file_source(tmp_path):
    out, r = build(tmp_path, {
        "docs/guide/index.md": "[m](../mcp.md)\n", "docs/mcp.md": "m\n"},
        "docs/guide", "docs/mcp.md")
    assert r.returncode == 0, r.stderr
    assert (out / "Home.md").read_text() == "[m](mcp)\n"


def test_link_outside_the_wiki_goes_to_the_repo(tmp_path):
    out, r = build(tmp_path, {
        "docs/guide/index.md": "[r](../../README.md#x) [c](../../compose/a.yml)\n",
        "README.md": "r\n", "compose/a.yml": "a\n"}, "docs/guide")
    assert r.returncode == 0, r.stderr
    assert (out / "Home.md").read_text() == (
        "[r](https://github.com/o/r/blob/main/README.md#x) "
        "[c](https://github.com/o/r/blob/main/compose/a.yml)\n")


def test_external_anchor_and_code_links_are_untouched(tmp_path):
    text = "[w](https://x.io/a.md) [a](#here) [m](mailto:a@b.c)\n```\n[not](a.md)\n```\n`[not](b.md)`\n"
    out, r = build(tmp_path, {"docs/guide/index.md": text}, "docs/guide")
    assert r.returncode == 0, r.stderr
    assert (out / "Home.md").read_text() == text


def test_images_keep_working(tmp_path):
    out, r = build(tmp_path, {
        "docs/guide/index.md": "![s](../images/a.png)\n",
        "docs/images/a.png": "p"}, "docs/guide")
    assert r.returncode == 0, r.stderr
    assert (out / "Home.md").read_text() == (
        "![s](https://github.com/o/r/raw/main/docs/images/a.png)\n")


def test_same_page_name_twice_fails(tmp_path):
    _, r = build(tmp_path, {
        "docs/guide/index.md": "x\n", "docs/guide/setup.md": "a\n",
        "docs/admin/setup.md": "b\n"}, "docs/guide", "docs/admin")
    assert r.returncode != 0
    assert "setup" in r.stderr


def test_broken_link_fails_naming_the_file(tmp_path):
    _, r = build(tmp_path, {"docs/guide/index.md": "[x](gone.md)\n"}, "docs/guide")
    assert r.returncode != 0
    assert "index.md" in r.stderr and "gone.md" in r.stderr


def test_missing_home_fails(tmp_path):
    _, r = build(tmp_path, {"docs/guide/a.md": "x\n"}, "docs/guide")
    assert r.returncode != 0
    assert "index.md" in r.stderr


def test_link_with_title_is_rewritten(tmp_path):
    out, r = build(tmp_path, {
        "docs/guide/index.md": '[a](stats.md "The stats")\n',
        "docs/guide/stats.md": "s\n"}, "docs/guide")
    assert r.returncode == 0, r.stderr
    assert (out / "Home.md").read_text() == '[a](stats "The stats")\n'


def test_reference_style_link_is_rewritten(tmp_path):
    out, r = build(tmp_path, {
        "docs/guide/index.md": "[a][s]\n\n[s]: stats.md#top\n",
        "docs/guide/stats.md": "s\n"}, "docs/guide")
    assert r.returncode == 0, r.stderr
    assert (out / "Home.md").read_text() == "[a][s]\n\n[s]: stats#top\n"


def test_html_links_and_images_are_rewritten(tmp_path):
    out, r = build(tmp_path, {
        "docs/guide/index.md": '<a href="stats.md">a</a> <img src="../i/a.png">\n',
        "docs/guide/stats.md": "s\n", "docs/i/a.png": "p"}, "docs/guide")
    assert r.returncode == 0, r.stderr
    assert (out / "Home.md").read_text() == (
        '<a href="stats">a</a> '
        '<img src="https://github.com/o/r/raw/main/docs/i/a.png">\n')


@pytest.mark.parametrize("text", [
    '[x](gone.md "title")\n', "[x][g]\n\n[g]: gone.md\n", '<a href="gone.md">x</a>\n'])
def test_broken_link_of_any_form_fails(tmp_path, text):
    _, r = build(tmp_path, {"docs/guide/index.md": text}, "docs/guide")
    assert r.returncode != 0
    assert "gone.md" in r.stderr
