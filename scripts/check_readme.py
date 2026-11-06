#!/usr/bin/env python3
"""Keep a README a landing page: it says what the project is, shows it, and sends
people on. Fails when it runs over its word budget or grows a section that belongs
in CONTRIBUTING.md or the guide. Words in code blocks, HTML tags and URLs don't count.

README_WORD_LIMIT overrides the budget; record the reason in docs/decisions.md.
--filled also fails on a [bracketed] template placeholder.
"""
import argparse
import os
import re
import sys
from pathlib import Path

DEFAULT_LIMIT = 700
ALLOWED = {"why", "screenshots", "features", "quick start", "documentation",
           "licence", "license"}
CODE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
INLINE = re.compile(r"`[^`\n]*`")
HTML = re.compile(r"<[^>]+>", re.S)
LINK_TARGET = re.compile(r"(\]\([^)]*\)|^\s*\[[^\]]+\]:.*$)", re.M)
# [Text] that is not a link: not followed by ( or [, not preceded by ! or ].
PLACEHOLDER = re.compile(r"(?<![!\]\[])\[([^\]\n]+)\](?![\(\[:])")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--filled", action="store_true")
    args = parser.parse_args()
    if not args.path.is_file():
        sys.exit(f"{args.path}: not found")

    limit = int(os.environ.get("README_WORD_LIMIT") or DEFAULT_LIMIT)
    text = CODE.sub("", args.path.read_text(encoding="utf-8"))
    problems = []

    words = len(LINK_TARGET.sub("]", HTML.sub("", text)).split())
    if words > limit:
        problems.append(f"{words} words, {words - limit} over the budget of {limit}")

    for heading in re.findall(r"(?m)^## +(.+?)\s*$", text):
        name = heading.lower()
        if name not in ALLOWED and not name.startswith("why "):
            problems.append(f"section not on the landing page list: {heading}")

    if args.filled:
        for found in PLACEHOLDER.findall(INLINE.sub("", text)):
            problems.append(f"placeholder not filled in: [{found}]")

    if problems:
        sys.exit("\n".join(f"{args.path}: {p}" for p in problems))
    print(f"{args.path}: {words} words, ok")


if __name__ == "__main__":
    main()
