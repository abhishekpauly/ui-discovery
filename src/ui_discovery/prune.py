"""G4 — delete captures past their retention, and say what went.

A capture of an authenticated portal is a folder of screenshots of somebody's
internal screens. They land in Downloads and stay there forever, and `G3` is
explicit that the redaction guarantees cover text and not pixels — so the only
thing between a stale capture and an indefinite copy of customer data is
somebody remembering to delete it.

    python -m ui_discovery.prune                     # what would go (default)
    python -m ui_discovery.prune --days 30           # ...older than 30 days
    python -m ui_discovery.prune --days 30 --delete  # actually remove them

**Listing is the default and `--delete` is required to remove anything.** The
ROADMAP specifies the inverse — delete, with a dry-run mode — and this is a
deliberate departure. Every other destructive decision in this engine refuses
by default and has to be asked twice: the probe will not click a control unless
two independent gates agree, and forms are never submitted at all. A command
that irreversibly deletes a directory tree because someone mistyped `--output`
would be the one place that pattern did not hold.

Three rules keep it from deleting the wrong thing:

  * **A folder is a capture only if it contains `run.json`.** Anything else in
    the output root belongs to somebody, and is never touched or counted.
  * **Age comes from the manifest, never from the filesystem.** A folder whose
    manifest will not parse, or carries no timestamp, is reported as
    undetermined and kept. Guessing an age is how you delete the wrong week.
  * **`runs.jsonl` is left alone.** It is append-only history (principle #4):
    the index records that a run happened, the folder is merely its artifact.
    Rewriting it to hide a pruned run would destroy the trend `O5` exists for.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .run import MANIFEST_FILE

# Retention is off unless asked for, so no zero-config run ever loses a capture.
RETENTION_OFF = 0


@dataclass(frozen=True)
class Capture:
    """One capture folder, and whether its age can be established."""

    path: Path
    run_id: Optional[str] = None
    target: str = ""
    finished_at: Optional[str] = None
    age_days: Optional[float] = None
    size_bytes: int = 0
    # Why the age could not be determined. Set iff `age_days` is None, and the
    # reason such a capture is kept rather than deleted.
    undetermined: Optional[str] = None

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class PrunePlan:
    """What a prune would do.

    Produced before anything is deleted, and the same object whether or not
    `--delete` was passed — so the listing a person reads is provably the set
    that would be removed, rather than a separate calculation that could differ.
    """

    root: Path
    retention_days: int
    expired: list[Capture] = field(default_factory=list)
    kept: list[Capture] = field(default_factory=list)
    undetermined: list[Capture] = field(default_factory=list)
    removed: list[Capture] = field(default_factory=list)
    failed: list[tuple[Capture, str]] = field(default_factory=list)

    @property
    def reclaimed_bytes(self) -> int:
        return sum(c.size_bytes for c in self.removed)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(c.size_bytes for c in self.expired)


def _folder_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _parse_when(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    # A manifest written by an older engine may carry a naive timestamp. Treat
    # it as UTC rather than refusing: UTC is what every writer has ever used,
    # and refusing would strand exactly the old captures retention is for.
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def read_capture(folder: Path, *,
                 now: Optional[datetime] = None) -> Optional[Capture]:
    """Describe one capture folder, or `None` if it is not a capture at all.

    Returning `None` for "not a capture" rather than raising is what lets a
    scan walk an output root full of unrelated folders without either crashing
    or — far worse — deciding they are fair game.
    """
    manifest_path = folder / MANIFEST_FILE
    if not manifest_path.is_file():
        return None

    size = _folder_size(folder)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Capture(
            path=folder, size_bytes=size,
            undetermined=f"{MANIFEST_FILE} is unreadable ({type(exc).__name__})")
    if not isinstance(manifest, dict):
        return Capture(path=folder, size_bytes=size,
                       undetermined=f"{MANIFEST_FILE} is not an object")

    when = _parse_when(manifest.get("finished_at") or manifest.get("started_at"))
    common = {
        "path": folder,
        "run_id": manifest.get("run_id"),
        "target": str(manifest.get("target") or ""),
        "size_bytes": size,
    }
    if when is None:
        return Capture(
            **common,
            undetermined=f"{MANIFEST_FILE} carries no readable timestamp")

    age = ((now or datetime.now(timezone.utc)) - when).total_seconds() / 86400
    return Capture(**common, finished_at=when.isoformat(),
                   age_days=round(max(age, 0.0), 2))


def find_captures(root: str | Path, *,
                  now: Optional[datetime] = None) -> list[Capture]:
    """Every capture under `root`, across both output layouts.

    Depth is bounded to the two layouts the engine actually writes —
    `<root>/<product>`, and `<root>/<date>/<product>` when `keep_history` is on.
    An unbounded walk would find captures nested anywhere beneath the root,
    which sounds helpful right up until the root is a home directory.
    """
    root = Path(root)
    if not root.is_dir():
        return []

    found: list[Capture] = []
    seen: set[Path] = set()
    for pattern in ("*", "*/*"):
        for folder in sorted(root.glob(pattern)):
            if not folder.is_dir() or folder in seen:
                continue
            capture = read_capture(folder, now=now)
            if capture is not None:
                seen.add(folder)
                found.append(capture)
    return found


def plan(root: str | Path, retention_days: int, *,
         now: Optional[datetime] = None) -> PrunePlan:
    """Decide what would go. Pure — touches nothing on disk."""
    result = PrunePlan(root=Path(root), retention_days=retention_days)
    for capture in find_captures(root, now=now):
        if capture.age_days is None:
            result.undetermined.append(capture)
        elif retention_days > RETENTION_OFF and capture.age_days > retention_days:
            result.expired.append(capture)
        else:
            result.kept.append(capture)
    return result


def prune_captures(root: str | Path, retention_days: int, *,
          delete: bool = False, now: Optional[datetime] = None) -> PrunePlan:
    """Plan, and — only when `delete` is True — carry it out.

    A folder that will not delete is recorded and the run continues: one locked
    screenshot must not leave the rest of an expired capture sitting on disk.
    """
    result = plan(root, retention_days, now=now)
    if not delete:
        return result
    for capture in result.expired:
        try:
            shutil.rmtree(capture.path)
            result.removed.append(capture)
        except OSError as exc:
            result.failed.append((capture, str(exc)))
    return result


def _mb(size: int) -> str:
    return f"{size / 1_048_576:.1f} MB"


def render(result: PrunePlan, *, deleted: bool) -> list[str]:
    """The report a person reads.

    Says what went, what stayed, and what could not be judged. The last of
    those is the one worth surfacing: a capture nobody can date is a capture
    retention will never remove, so it would otherwise accumulate silently —
    which is the exact failure this item exists to fix.
    """
    lines = [f"[INFO] Output root: {result.root}"]
    if result.retention_days <= RETENTION_OFF:
        lines.append(
            "[INFO] Retention is off (retention_days: 0) — nothing to prune.")
        lines.append(
            f"[INFO] {len(result.kept) + len(result.undetermined)} capture(s) kept.")
        return lines

    lines.append(f"[INFO] Retention: {result.retention_days} days")
    failed_paths = {id(c) for c, _ in result.failed}
    for capture in result.expired:
        if id(capture) in failed_paths:
            mark = "FAILED"
        elif deleted:
            mark = "removed"
        else:
            mark = "would remove"
        target = f"  {capture.target}" if capture.target else ""
        lines.append(
            f"  [{mark}] {capture.name}  "
            f"({capture.age_days:.0f} days old, {_mb(capture.size_bytes)}){target}")
    for capture, error in result.failed:
        lines.append(f"  [ERROR] {capture.name}: {error}")
    for capture in result.undetermined:
        lines.append(
            f"  [kept]  {capture.name}  — age undetermined: {capture.undetermined}")

    count = len(result.removed) if deleted else len(result.expired)
    total = result.reclaimed_bytes if deleted else result.reclaimable_bytes
    lines.append(
        f"[INFO] {'Removed' if deleted else 'Would remove'} {count} capture(s), "
        f"{_mb(total)}; kept {len(result.kept)}"
        + (f"; {len(result.undetermined)} undetermined" if result.undetermined else "")
        + (f"; {len(result.failed)} failed" if result.failed else ""))
    if not deleted and result.expired:
        lines.append(
            "[INFO] Nothing was deleted. Re-run with --delete to remove them.")
    return lines


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .cliconfig import add_config_argument, load_or_exit, resolve_output_root

    parser = argparse.ArgumentParser(
        prog="ui_discovery.prune",
        description="Delete captures past their retention. Lists by default; "
                    "--delete is required to remove anything.",
    )
    parser.add_argument(
        "root", nargs="?", default=None,
        help="Output root to scan (default: from config, else Downloads)")
    add_config_argument(parser)
    parser.add_argument(
        "--days", type=int, default=None,
        help="Retention in days; overrides outputs.retention_days. 0 = off.")
    parser.add_argument(
        "--delete", action="store_true",
        help="Actually remove expired captures (default: list only)")
    args = parser.parse_args(argv)

    scope = load_or_exit(args.config)
    root = args.root or resolve_output_root(scope, None)
    days = args.days if args.days is not None else scope.outputs.retention_days

    if days < 0:
        print("[ERROR] --days must be zero or positive (0 turns retention off).",
              file=sys.stderr)
        return 2

    result = prune_captures(root, days, delete=args.delete)
    for line in render(result, deleted=args.delete):
        print(line)
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
