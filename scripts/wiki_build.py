#!/usr/bin/env python3
"""Turn Markdown pages from a repo's docs into GitHub wiki pages.

A wiki is one flat folder whose links have no path and no extension, so pages
are copied flat and their links rewritten. A link to anything not published
points at the file in the repo. A broken link or two pages with the same name
fails the build, so the wiki is never published half-working.
"""
import argparse
import os
import re
import sys
from pathlib import Path

# Every form a link can take: [a](b "title"), [ref]: b, <a href="b">, <img src="b">.
LINK = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")
REFERENCE = re.compile(r"(?m)^( {0,3}\[[^\]]+\]:\s*)(\S+)()")
HTML = re.compile(r"""(<(?:a|img)\b[^>]*?\b(?:href|src)=["'])([^"']+)(["'])""", re.I)
FENCE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]*`)", re.S)
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", re.I)


def collect(sources, home):
    """Map each published file (repo-relative) to its wiki page name."""
    files = []
    for source in sources:
        path = Path(source)
        if path.is_dir():
            files.extend(sorted(path.glob("*.md")))
        elif path.is_file():
            files.append(path)
        else:
            sys.exit(f"source not found: {source}")
    pages, owners = {}, {}
    root = Path.cwd().resolve()
    for path in files:
        key = os.path.normpath(path)
        # This runs with a write token on a public repo's content: a symlink or an
        # escaping path must not publish a file from outside the repo.
        if path.is_symlink() or path.suffix != ".md" or not path.resolve().is_relative_to(root):
            sys.exit(f"not a Markdown file inside the repo: {key}")
        name = "Home" if key == os.path.normpath(home) else path.stem
        if name in owners:
            sys.exit(f"two pages would be called {name}: {owners[name]} and {key}")
        owners[name] = key
        pages[key] = name
    if os.path.normpath(home) not in pages:
        sys.exit(f"home page {home} is not among the published pages")
    return pages


def rewrite(text, source, pages, repo_url, errors):
    base = os.path.dirname(source)

    def one(match):
        before, url, after = match.groups()
        image = before.startswith("!") or before.lower().startswith("<img")
        if EXTERNAL.match(url):
            return match.group(0)
        target, _, fragment = url.partition("#")
        path = os.path.normpath(os.path.join(base, target))
        if path.startswith("..") or not os.path.exists(path):
            errors.append(f"{source}: broken link {url}")
            return match.group(0)
        anchor = f"#{fragment}" if fragment else ""
        if not image and path in pages:
            return f"{before}{pages[path]}{anchor}{after}"
        # Images must come from raw, or the wiki would show a web page.
        root = repo_url.replace("/blob/", "/raw/") if image else repo_url
        return f"{before}{root}/{path}{anchor}{after}"

    def links(piece):
        for pattern in (LINK, REFERENCE, HTML):
            piece = pattern.sub(one, piece)
        return piece

    # Odd pieces of the split are code, which is left exactly as written.
    pieces = FENCE.split(text)
    pieces[::2] = [links(piece) for piece in pieces[::2]]
    return "".join(pieces)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", help="folders or .md files, repo-relative")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--home", required=True, help="the page that becomes Home")
    parser.add_argument("--repo-url", required=True,
                        help="https://github.com/<owner>/<repo>/blob/<branch>")
    args = parser.parse_args()

    pages = collect(args.sources, args.home)
    errors, output = [], {}
    for source, name in pages.items():
        text = Path(source).read_text(encoding="utf-8")
        output[name] = rewrite(text, source, pages, args.repo_url.rstrip("/"), errors)
    if errors:
        sys.exit("\n".join(errors))
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in output.items():
        (args.out / f"{name}.md").write_text(text, encoding="utf-8")
    print(f"{len(output)} pages")


if __name__ == "__main__":
    main()
