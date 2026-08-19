"""Framework-agnostic UI Discovery Engine.

Every capability is importable and composable as a library, not just
runnable as a CLI — the CLIs (`python -m ui_discovery.crawl`, etc.) are thin
wrappers over the same functions exported here. See README "Use as a
library" for an end-to-end example.

`ui_discovery` (this package import) must stay AI-free — enforced by
`tests/test_no_ai_runtime.py`. The V5 functions exported below (`classify_*`,
`refine_semantics`, `generate_documentation`, `generate_qa_plan`) are
deterministic by default; they only reach for an LLM if you explicitly pass
a `provider_name`, and the provider SDK is imported lazily at that point —
importing this package never pulls one in.
"""

from __future__ import annotations

# Product/package version (see CHANGELOG.md). Bump on each release.
__version__ = "0.8.0"
# Data-schema version — bump ONLY when the JSON model shape changes in a way
# that affects readers of past snapshots. Still 0.1.0: all growth so far has
# been additive (new optional fields / new models), not breaking.
SCHEMA_VERSION = "0.1.0"

# --- Public library surface (R1) --------------------------------------------
# Imported lazily via __getattr__ below, not eagerly here: several of these
# modules (crawler, interactions) are non-trivial to import (Playwright,
# Crawlee) and eager import would slow down `import ui_discovery` for callers
# who only need e.g. `SCHEMA_VERSION`. The public names still resolve exactly
# as if they were imported at the top — `ui_discovery.crawl_site(...)` works
# either way.
_EXTRACTION = "ui_discovery.extraction"
_CRAWLER = "ui_discovery.crawler"
_ANALYSIS = "ui_discovery.analysis"
_DIFF = "ui_discovery.diff"
_CONFIG = "ui_discovery.config"
_SOURCEMAP = "ui_discovery.sourcemap"
_INTERACTIONS = "ui_discovery.interactions"
_AUTH = "ui_discovery.auth"
_SEMANTIC = "ui_discovery.semantic"
_DOCGEN = "ui_discovery.docgen"
_QAGEN = "ui_discovery.qagen"
_REPORTS = "ui_discovery.reports"

_EXPORTS = {
    # V0 — single-page extraction.
    "extract_page": _EXTRACTION,
    # V1 — multi-page crawl.
    "crawl_site": _CRAWLER,
    # V2 — structural analysis over a crawl (fingerprints, regions, components).
    "analyze_crawl": _ANALYSIS,
    # V3 — safe interaction + network probe over one page.
    "probe_page": _INTERACTIONS,
    # C1 — deterministic change diff between two analyses.
    "diff_analyses": _DIFF,
    # H5/R2/S1 — scope configuration.
    "Scope": _CONFIG,
    "load_scope": _CONFIG,
    "dump_scope": _CONFIG,
    # V4 — source indexing and runtime->source correlation.
    "index_repo": _SOURCEMAP,
    "correlate": _SOURCEMAP,
    # Session auth — capture/load a logged-in Playwright storage state.
    "capture_session": _AUTH,
    "load_storage_state": _AUTH,
    # V5.1 — semantic element classification (deterministic + optional LLM refine).
    "classify_analysis": _SEMANTIC,
    "refine_semantics": _SEMANTIC,
    # V5.2 — documentation generation.
    "generate_documentation": _DOCGEN,
    # V5.3 — QA scenario generation + Playwright test-skeleton export (C2).
    "generate_qa_plan": _QAGEN,
    "build_playwright": _QAGEN,
    # Report writers — JSON/Markdown/HTML for each model above.
    "write_reports": _REPORTS,
    "write_analysis": _REPORTS,
    "write_probe": _REPORTS,
    "write_semantics": _REPORTS,
    "write_documentation": _REPORTS,
    "write_qaplan": _REPORTS,
    "write_diff": _REPORTS,
}

# The underlying function names differ from their public alias in two cases
# (docgen.generate / qagen.generate would otherwise collide).
_ALIASES = {
    "generate_documentation": "generate",
    "generate_qa_plan": "generate",
}

__all__ = ["__version__", "SCHEMA_VERSION", *_EXPORTS]


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name)
    attr = _ALIASES.get(name, name)
    value = getattr(module, attr)
    globals()[name] = value  # cache: subsequent access skips __getattr__
    return value
