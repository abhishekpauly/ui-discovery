"""V5.4 — turn C1's diff into a readable "what changed and why it matters".

**Deterministic by default (zero tokens).** The narrative is assembled from
the diff's own structured fields by the rules below, so `diff` produces a
readable summary with no provider, no key and no network. An optional
`--provider` rewrites *the prose only*.

The diff stays the source of truth. This module never invents a change, never
edits the structured fields, and the LLM is given the already-computed
findings to phrase — not the snapshots to analyse. If the narrative and the
tables ever disagree, the tables are right.

Quarantined per architecture principle #13: importing this module loads no AI
library (the provider seam in `llm.py` imports its SDK lazily, only when a
provider is actually instantiated).
"""

from __future__ import annotations

from typing import Optional

from .models import Diff

# What a change of each kind usually means for someone who has to act on it.
# Deliberately hedged ("worth checking", "may"), because a structural diff
# cannot know intent — only that something moved.
_IMPLICATIONS = {
    "renamed": "Renamed controls break tests and docs that match on label, "
               "and are the most common cause of a suite going red after a "
               "release that changed nothing functional.",
    "removed_pages": "Removed pages break inbound links and bookmarks; check "
                     "whether they moved rather than went away.",
    "added_pages": "New pages are usually new surface area to cover — none of "
                   "it is in your existing tests.",
    "removed_elements": "Removed controls may be a deliberate simplification "
                        "or an accidental regression; the diff cannot tell "
                        "which.",
    "components": "A shared component appearing or disappearing affects every "
                  "page that used it, not just the ones listed here.",
}


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _headline(diff: Diff) -> str:
    s = diff.stats
    if not s.get("total_changes"):
        return ("Nothing changed between these two snapshots — same pages, "
                "same controls, same components.")
    parts = []
    if s["pages_added"] or s["pages_removed"]:
        bits = []
        if s["pages_added"]:
            bits.append(f"{s['pages_added']} added")
        if s["pages_removed"]:
            bits.append(f"{s['pages_removed']} removed")
        parts.append(f"{' and '.join(bits)} {'page' if s['pages_added'] + s['pages_removed'] == 1 else 'pages'}")
    if s["elements_renamed"]:
        parts.append(_plural(s["elements_renamed"], "renamed control"))
    if s["elements_added"] or s["elements_removed"]:
        parts.append(f"{s['elements_added']} added and "
                     f"{s['elements_removed']} removed controls")
    return "This release shows " + ", ".join(parts) + "."


def _sections(diff: Diff) -> list[str]:
    s = diff.stats
    out: list[str] = []

    renames = [c for c in diff.elements if c.kind == "renamed"]
    if renames:
        examples = "; ".join(
            f"“{c.previous_name}” → “{c.accessible_name}”" for c in renames[:5]
        )
        more = f" (and {len(renames) - 5} more)" if len(renames) > 5 else ""
        out.append(f"**Renames.** {examples}{more}. {_IMPLICATIONS['renamed']}")

    added_pages = [p for p in diff.pages if p.kind == "added"]
    if added_pages:
        names = ", ".join(f"`{p.url}`" for p in added_pages[:5])
        out.append(f"**New pages.** {names}. {_IMPLICATIONS['added_pages']}")

    removed_pages = [p for p in diff.pages if p.kind == "removed"]
    if removed_pages:
        names = ", ".join(f"`{p.url}`" for p in removed_pages[:5])
        out.append(f"**Removed pages.** {names}. "
                   f"{_IMPLICATIONS['removed_pages']}")

    removed = [c for c in diff.elements if c.kind == "removed"]
    if removed:
        names = ", ".join(f"“{c.accessible_name or '(unnamed)'}”"
                          for c in removed[:5])
        more = f" (and {len(removed) - 5} more)" if len(removed) > 5 else ""
        out.append(f"**Removed controls.** {names}{more}. "
                   f"{_IMPLICATIONS['removed_elements']}")

    if s.get("components_added") or s.get("components_removed"):
        out.append(f"**Components.** {s.get('components_added', 0)} gained, "
                   f"{s.get('components_removed', 0)} lost. "
                   f"{_IMPLICATIONS['components']}")

    return out


def build_narrative(diff: Diff) -> str:
    """Assemble a readable summary from the diff. Pure, deterministic, and
    the default — no provider required."""
    parts = [_headline(diff)]
    parts.extend(_sections(diff))
    if diff.stats.get("total_changes"):
        parts.append(
            "_Structural changes only. Whether each one is intended is not "
            "something two snapshots can answer._"
        )
    return "\n\n".join(parts)


_PROMPT = """\
You are summarising a UI change report for an engineer deciding what to
re-test after a release.

These findings were computed deterministically by diffing two snapshots of
the same site. Treat them as facts. Do NOT invent changes, do not speculate
about code or intent, and do not contradict the numbers.

Write 3-5 sentences: what changed, and what is worth checking as a result.
Plain prose, no bullet points, no headings.

FINDINGS
{findings}
"""


def _findings_for_prompt(diff: Diff, limit: int = 40) -> str:
    s = diff.stats
    lines = [
        f"pages: +{s['pages_added']} -{s['pages_removed']} "
        f"~{s['pages_changed']}",
        f"elements: +{s['elements_added']} -{s['elements_removed']} "
        f"renamed {s['elements_renamed']}",
        f"components: +{s.get('components_added', 0)} "
        f"-{s.get('components_removed', 0)}",
    ]
    for c in diff.elements[:limit]:
        if c.kind == "renamed":
            lines.append(f"renamed: '{c.previous_name}' -> "
                         f"'{c.accessible_name}' on {c.page_url}")
        else:
            lines.append(f"{c.kind}: '{c.accessible_name or '(unnamed)'}' "
                         f"on {c.page_url}")
    for p in diff.pages:
        if p.kind in ("added", "removed"):
            lines.append(f"page {p.kind}: {p.url}")
    return "\n".join(lines)


def narrate(diff: Diff, provider_name: str = "none",
            model: Optional[str] = None) -> Diff:
    """Attach a narrative to `diff` and return it.

    Always sets the deterministic narrative first, so a failed or refusing
    provider degrades to a useful summary rather than an empty one. Only the
    prose is replaced; every structured field is left untouched.
    """
    diff.narrative = build_narrative(diff)
    diff.narrative_source = "deterministic"

    from .llm import get_text_provider  # lazy: keeps this import AI-free

    provider = get_text_provider(provider_name, model)
    if provider is None or not diff.stats.get("total_changes"):
        return diff

    text = provider.complete(_PROMPT.format(findings=_findings_for_prompt(diff)))
    if text:
        diff.narrative = text
        diff.narrative_source = provider.name
    return diff
