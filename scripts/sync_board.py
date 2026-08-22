#!/usr/bin/env python3
"""Put every epic and story issue on the project board, with its backlog ID.

The board answers "who is on it and where has it got to" — but only for issues
that are actually on it. Adding them by hand is forty clicks that nobody repeats
after the first sprint, so the board quietly stops being the answer and the
question moves back to whoever remembers.

This is the same bargain `sync_labels.py` makes: keep the mapping derivable, and
apply it from a command rather than from memory. The backlog ID is not invented
here — it is read from the issue title, which already carries it because
`.github/ISSUE_TEMPLATE` requires the format.

    python scripts/sync_board.py --dry-run       # show what would change
    python scripts/sync_board.py                 # apply

Idempotent: an issue already on the board with the right `Backlog ID` is left
alone, and running twice changes nothing the second time. Never removes an item
— a card someone parked on the board deliberately is not this script's to
delete.

Needs `gh` on PATH, authenticated, **with project scope**:

    gh auth refresh -s project,read:project
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

OWNER = "abhishekpauly"
REPO = "abhishekpauly/ui-discovery"
PROJECT_TITLE = "UI Discovery Engine"
ID_FIELD = "Backlog ID"

# `M1 — Sitemap ingestion`, `O1 - Run identity`, `EPIC-QA - Real-world
# validation`, `QA.4 - The report ...`. Both dash styles are in use: the older
# issues predate the em-dash convention and renaming them would break every
# reference already written down.
TITLE_ID = re.compile(r"^\s*([A-Za-z][\w.-]*?)\s+[—-]\s+")


class GhError(RuntimeError):
    """`gh` exited non-zero."""


def _gh(*args: str) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise GhError(f"gh {' '.join(args)} failed:\n{done.stderr.strip()}")
    return done.stdout


def backlog_id(title: str) -> str | None:
    """The ID an issue title carries, or None if it carries none."""
    m = TITLE_ID.match(title)
    return m.group(1) if m else None


def project_number() -> int:
    raw = _gh("project", "list", "--owner", OWNER, "--format", "json")
    for project in json.loads(raw)["projects"]:
        if project["title"] == PROJECT_TITLE:
            return project["number"]
    raise GhError(
        f"no project titled {PROJECT_TITLE!r} for {OWNER}. "
        f"Run ./scripts/bootstrap_github.sh project first.")


def field_id(number: int, name: str) -> str:
    raw = _gh("project", "field-list", str(number), "--owner", OWNER,
              "--format", "json")
    for field in json.loads(raw)["fields"]:
        if field["name"] == name:
            return field["id"]
    raise GhError(
        f"project #{number} has no {name!r} field. "
        f"Run ./scripts/bootstrap_github.sh project first.")


def board_items(number: int) -> dict[int, dict]:
    """Issue number -> its board item, for issues already on the board."""
    raw = _gh("project", "item-list", str(number), "--owner", OWNER,
              "--format", "json", "--limit", "500")
    out = {}
    for item in json.loads(raw)["items"]:
        content = item.get("content") or {}
        if content.get("type") == "Issue" and content.get("number") is not None:
            out[content["number"]] = item
    return out


def _field_value(item: dict, name: str) -> str:
    """Read a project field off an item.

    `gh project item-list` camel-cases field names into the JSON — `Backlog ID`
    arrives as `backlogID` — and the exact casing has moved between `gh`
    releases. Match on the letters alone rather than betting on one spelling; a
    wrong guess here would silently re-write every field on every run.
    """
    wanted = re.sub(r"[^a-z0-9]", "", name.lower())
    for key, value in item.items():
        if re.sub(r"[^a-z0-9]", "", key.lower()) == wanted:
            return str(value)
    return ""


def tracked_issues() -> list[dict]:
    """Every epic or story issue, open or closed, newest first."""
    raw = _gh("issue", "list", "--repo", REPO, "--state", "all",
              "--limit", "500", "--json", "number,title,url,labels")
    issues = []
    for issue in json.loads(raw):
        names = {label["name"] for label in issue["labels"]}
        if names & {"epic", "story", "spike"}:
            issues.append(issue)
    return sorted(issues, key=lambda i: i["number"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and change nothing")
    args = parser.parse_args(argv)

    try:
        number = project_number()
        field = field_id(number, ID_FIELD)
        on_board = board_items(number)
        issues = tracked_issues()
    except GhError as exc:
        print(exc, file=sys.stderr)
        print("\nIs `gh` authenticated with project scope?\n"
              "  gh auth refresh -s project,read:project", file=sys.stderr)
        return 2

    added = updated = correct = 0
    unnamed: list[str] = []

    for issue in issues:
        num, title = issue["number"], issue["title"]
        ident = backlog_id(title)
        if not ident:
            unnamed.append(f"#{num} {title}")
            continue

        item = on_board.get(num)
        if item is None:
            print(f"  + #{num:<3} {ident:<12} {title}")
            added += 1
            if args.dry_run:
                continue
            raw = _gh("project", "item-add", str(number), "--owner", OWNER,
                      "--url", issue["url"], "--format", "json")
            item = json.loads(raw)
        elif _field_value(item, ID_FIELD) == ident:
            correct += 1
            continue
        else:
            print(f"  ~ #{num:<3} {ident:<12} {title}")
            updated += 1
            if args.dry_run:
                continue

        _gh("project", "item-edit", "--id", item["id"],
            "--project-id", _project_node_id(number),
            "--field-id", field, "--text", ident)

    for line in unnamed:
        print(f"  ? {line}  (no backlog ID in the title; left alone)")

    verb = "would sync" if args.dry_run else "synced"
    print(f"\n{verb}: {added} added, {updated} updated, {correct} already "
          f"correct, {len(unnamed)} untitled")
    return 0


_NODE_ID: str | None = None


def _project_node_id(number: int) -> str:
    """The project's GraphQL node id, which `item-edit` wants instead of its
    number. Looked up once — it costs an API call and never changes."""
    global _NODE_ID
    if _NODE_ID is None:
        raw = _gh("project", "view", str(number), "--owner", OWNER,
                  "--format", "json")
        _NODE_ID = json.loads(raw)["id"]
    return _NODE_ID


if __name__ == "__main__":
    raise SystemExit(main())
