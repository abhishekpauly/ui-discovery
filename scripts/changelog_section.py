#!/usr/bin/env python3
"""Lift one version's section out of `CHANGELOG.md`.

The release notes on GitHub and the changelog in the repo must not be able to
disagree, so there is only one of them and this reads it. `release.yml` calls
this to fill in the Release body; you can call it to check what a release will
say before you push the tag.

    python scripts/changelog_section.py                 # the declared version's body
    python scripts/changelog_section.py 0.17.0          # a specific version
    python scripts/changelog_section.py --title         # "0.18.0 — Where the time went (O4-O5)"
    python scripts/changelog_section.py --list          # every version in the file
    python scripts/changelog_section.py -o notes.md     # straight to a file, UTF-8
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"

# "## [0.18.0] — Where the time went (O4-O5)"  ->  version, title
HEADING = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\](?:\s*[—-]\s*(?P<title>.*))?$")


def declared_version(pyproject: Path = PYPROJECT) -> str:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def sections(changelog: Path = CHANGELOG) -> dict[str, tuple[str, str]]:
    """Every released version in the file, as {version: (title, body)}.

    A section runs from its own heading to the next `## ` heading of any kind,
    so `[Unreleased]` correctly terminates the newest release rather than being
    swallowed by it. The `---` rule the file uses between entries is dropped.
    """
    lines = changelog.read_text(encoding="utf-8").splitlines()
    found: dict[str, tuple[str, str]] = {}
    current: str | None = None
    title = ""
    body: list[str] = []

    def close() -> None:
        if current is not None:
            text = "\n".join(body).strip()
            found[current] = (title, re.sub(r"\n*^-{3,}\s*$", "", text, flags=re.M).strip())

    for line in lines:
        match = HEADING.match(line)
        if match:
            close()
            current = match.group("version")
            title = (match.group("title") or "").strip()
            body = []
        elif line.startswith("## "):
            close()
            current = None
            body = []
        elif current is not None:
            body.append(line)
    close()
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", nargs="?",
                        help="defaults to the version declared in pyproject.toml")
    parser.add_argument("--title", action="store_true",
                        help="print '<version> — <title>' instead of the body")
    parser.add_argument("--list", action="store_true",
                        help="list every version the changelog describes")
    parser.add_argument("--changelog", type=Path, default=CHANGELOG)
    parser.add_argument("-o", "--output", type=Path,
                        help="write to this file (UTF-8) instead of stdout")
    args = parser.parse_args(argv)

    # The changelog is full of em-dashes and arrows; a Windows console defaults
    # to cp1252 and would rather raise than print them.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def emit(text: str) -> None:
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)

    found = sections(args.changelog)

    if args.list:
        emit("\n".join(f"{v}\t{title}" for v, (title, _) in found.items()))
        return 0

    version = (args.version or declared_version()).lstrip("v")
    if version not in found:
        print(f"{args.changelog.name} has no entry for {version}. "
              f"It describes: {', '.join(found) or '(nothing)'}", file=sys.stderr)
        return 1

    title, body = found[version]
    emit((f"{version} — {title}" if title else version) if args.title else body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
