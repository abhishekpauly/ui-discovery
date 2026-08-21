#!/usr/bin/env python3
"""Apply `.github/labels.yml` to the repository's labels.

Labels drift. Someone adds `p0` next to `P0`, someone else colours a new area
label whatever the picker offered, and six months later the board cannot be
filtered because no two issues agree on what to call the same thing. Keeping the
set in a file and applying it from there is the cheap fix.

Idempotent: running it twice changes nothing the second time. Additive by
default — labels on GitHub that are absent from the file are reported and left
alone, because deleting a label deletes it from every issue that carries it.
Pass ``--prune`` when you actually mean that.

    python scripts/sync_labels.py --dry-run          # show what would change
    python scripts/sync_labels.py                    # apply
    python scripts/sync_labels.py --prune            # apply, and delete extras

Needs `gh` on PATH and authenticated (`gh auth login`). Uses `gh` rather than a
raw token so it works unchanged on a laptop and on a runner.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LABELS_FILE = ROOT / ".github" / "labels.yml"


class GhError(RuntimeError):
    """`gh` exited non-zero."""


def _gh(*args: str, repo: str | None = None) -> str:
    cmd = ["gh", *args]
    if repo:
        cmd += ["--repo", repo]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise GhError(f"gh {' '.join(args)} failed:\n{done.stderr.strip()}")
    return done.stdout


def _normalise(text: str | None) -> str:
    """Collapse a YAML block scalar to what GitHub will actually store."""
    return " ".join((text or "").split())


def desired_labels(path: Path = LABELS_FILE) -> list[dict[str, str]]:
    """Read the label set from the YAML file, normalised for comparison."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    labels = data.get("labels") or []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in labels:
        name = str(entry["name"])
        if name in seen:
            raise ValueError(f"{path.name} defines {name!r} twice")
        seen.add(name)
        out.append({
            "name": name,
            "color": str(entry.get("color", "ededed")).lstrip("#").lower(),
            "description": _normalise(entry.get("description")),
        })
    return out


def existing_labels(repo: str | None = None) -> dict[str, dict[str, str]]:
    raw = _gh("label", "list", "--limit", "500",
              "--json", "name,color,description", repo=repo)
    return {
        item["name"]: {
            "name": item["name"],
            "color": (item.get("color") or "").lstrip("#").lower(),
            "description": _normalise(item.get("description")),
        }
        for item in json.loads(raw or "[]")
    }


def plan(want: list[dict[str, str]], have: dict[str, dict[str, str]]):
    """Split the desired set into (create, update, extra)."""
    create = [label for label in want if label["name"] not in have]
    update = [
        label for label in want
        if label["name"] in have and have[label["name"]] != label
    ]
    wanted_names = {label["name"] for label in want}
    extra = sorted(name for name in have if name not in wanted_names)
    return create, update, extra


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", help="OWNER/NAME; defaults to the current checkout")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and change nothing")
    parser.add_argument("--prune", action="store_true",
                        help="delete labels that this file does not define")
    parser.add_argument("--file", type=Path, default=LABELS_FILE)
    args = parser.parse_args(argv)

    want = desired_labels(args.file)
    try:
        have = existing_labels(args.repo)
    except GhError as exc:
        print(exc, file=sys.stderr)
        print("\nIs `gh` installed and authenticated? `gh auth login`", file=sys.stderr)
        return 2

    create, update, extra = plan(want, have)

    for label in create:
        print(f"  + {label['name']}")
        if not args.dry_run:
            _gh("label", "create", label["name"], "--color", label["color"],
                "--description", label["description"], repo=args.repo)
    for label in update:
        before = have[label["name"]]
        changed = [k for k in ("color", "description") if before[k] != label[k]]
        print(f"  ~ {label['name']}  ({', '.join(changed)})")
        if not args.dry_run:
            _gh("label", "edit", label["name"], "--color", label["color"],
                "--description", label["description"], repo=args.repo)
    for name in extra:
        if args.prune:
            print(f"  - {name}")
            if not args.dry_run:
                _gh("label", "delete", name, "--yes", repo=args.repo)
        else:
            print(f"  ? {name}  (on GitHub, not in {args.file.name}; --prune to delete)")

    unchanged = len(want) - len(create) - len(update)
    verb = "would sync" if args.dry_run else "synced"
    print(f"\n{verb}: {len(create)} created, {len(update)} updated, "
          f"{unchanged} already correct, {len(extra)} untracked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
