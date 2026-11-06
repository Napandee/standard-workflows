"""Tests for scripts/check_readme.py, run through its command line."""
import os
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).with_name("check_readme.py")
GOOD = """# App

## Why App

Short.

## Features

Cards.

## Quick start

```bash
run --it
```

## Documentation

Links.

## Licence

MIT.
"""


def check(tmp_path, text, *args, limit=None):
    path = tmp_path / "README.md"
    path.write_text(text)
    env = dict(os.environ)
    env.pop("README_WORD_LIMIT", None)
    if limit:
        env["README_WORD_LIMIT"] = str(limit)
    return subprocess.run([sys.executable, str(SCRIPT), str(path), *args], env=env,
                          capture_output=True, text=True)


def test_good_readme_passes(tmp_path):
    r = check(tmp_path, GOOD)
    assert r.returncode == 0, r.stderr


def test_over_budget_fails_and_says_by_how_much(tmp_path):
    r = check(tmp_path, GOOD + "\nword " * 701)
    assert r.returncode == 1
    assert "over" in r.stderr and "700" in r.stderr


def test_code_html_and_urls_are_not_counted(tmp_path):
    noise = "```\n" + "code " * 900 + "\n```\n"
    noise += '<img alt="x" src="https://a.b/c.png">\n' * 900
    noise += "[word](https://a.b/" + "x" * 50 + ")\n"
    assert check(tmp_path, GOOD + noise).returncode == 0


def test_limit_override(tmp_path):
    assert check(tmp_path, GOOD + "word " * 800, limit=2000).returncode == 0


def test_section_not_on_the_list_fails_and_is_named(tmp_path):
    r = check(tmp_path, GOOD + "\n## Local development\n\nx\n")
    assert r.returncode == 1
    assert "Local development" in r.stderr


def test_screenshots_section_is_allowed(tmp_path):
    assert check(tmp_path, GOOD + "\n## Screenshots\n\nx\n").returncode == 0


def test_heading_inside_code_is_not_a_section(tmp_path):
    assert check(tmp_path, GOOD + "\n```\n## not a section\n```\n").returncode == 0


def test_placeholders_fail_only_with_filled(tmp_path):
    text = GOOD + "\n[Project Name] does things.\n"
    assert check(tmp_path, text).returncode == 0
    r = check(tmp_path, text, "--filled")
    assert r.returncode == 1
    assert "[Project Name]" in r.stderr


def test_real_links_are_not_placeholders(tmp_path):
    assert check(tmp_path, GOOD + "\n[LICENSE](LICENSE) ![a](b.png) [x][y]\n", "--filled").returncode == 0


def test_missing_file_fails(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "nope.md")],
                       capture_output=True, text=True)
    assert r.returncode != 0
