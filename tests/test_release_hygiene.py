"""Guard: the repo must not misdescribe itself.

The version and changelog have gone stale twice. The first time, the project
claimed 0.8.0 while containing eleven roadmap items; the second, 0.12.0 while
containing eight features and a regression fix. Both times the fix was manual
and both times it rotted again, because nothing failed when it did.

So: fail. A repo whose own metadata is wrong undermines every other claim in
it — including the careful ones about what a capture does and does not
contain.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"


def _declared_version() -> str:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def _changelog_versions() -> list[str]:
    return re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG.read_text(encoding="utf-8"),
                      flags=re.M)


def test_version_matches_between_pyproject_and_package():
    init = (ROOT / "src" / "ui_discovery" / "__init__.py").read_text(encoding="utf-8")
    in_package = re.search(r'__version__ = "([^"]+)"', init).group(1)
    assert in_package == _declared_version(), (
        "pyproject.toml and __init__.py disagree about the version")


def test_current_version_has_a_changelog_entry():
    version = _declared_version()
    assert version in _changelog_versions(), (
        f"version {version} has no CHANGELOG entry. Bump one or write the "
        f"other — a release nobody described is a release nobody can review.")


def test_changelog_newest_entry_is_the_current_version():
    versions = _changelog_versions()
    assert versions, "CHANGELOG has no version entries at all"
    assert versions[0] == _declared_version(), (
        f"CHANGELOG's newest entry is {versions[0]} but the project claims "
        f"{_declared_version()}")


def _commits_since_release() -> list[str]:
    """Commits touching src/ since the release commit that set this version."""
    version = _declared_version()
    try:
        release = subprocess.run(
            ["git", "log", "-1", "--format=%H", "-S", f'version = "{version}"',
             "--", "pyproject.toml"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        if not release:
            return []
        out = subprocess.run(
            ["git", "log", "--format=%s", "--no-merges", f"{release}..HEAD",
             "--", "src/"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        ).stdout
        return [ln for ln in out.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


# How much unreleased source churn is tolerable before the version is simply
# wrong. A couple of commits is work in progress; six substantive features is
# a release that never happened. Set deliberately tight: the previous drift
# was exactly six, and a limit that would have let it through is not a guard.
DRIFT_LIMIT = 3


def test_source_has_not_drifted_far_past_the_declared_version():
    commits = _commits_since_release()
    if not commits:
        pytest.skip("no git history available (or version never released)")
    assert len(commits) <= DRIFT_LIMIT, (
        f"{len(commits)} commits have changed src/ since {_declared_version()} "
        f"was released:\n  "
        + "\n  ".join(commits[:12])
        + f"\n\nBump the version and add a CHANGELOG entry. This guard exists "
          f"because the version has silently gone stale twice already."
    )
