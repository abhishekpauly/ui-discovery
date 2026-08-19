"""Framework-agnostic UI Discovery Engine.

V0: a single-page extractor. Given one URL, render it in a real browser and
emit a deterministic UI model (`page.json`) plus a screenshot. No crawling,
no Crawlee — that arrives in V1 once the extractor is trusted.
"""

# Product/package version (see CHANGELOG.md). Bump on each release.
__version__ = "0.8.0"
# Data-schema version — bump ONLY when the JSON model shape changes in a way
# that affects readers of past snapshots. Still 0.1.0: all growth so far has
# been additive (new optional fields / new models), not breaking.
SCHEMA_VERSION = "0.1.0"
