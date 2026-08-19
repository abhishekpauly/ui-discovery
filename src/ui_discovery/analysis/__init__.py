"""V2 analysis layer.

Turns the raw, immutable crawl (`crawl.json`) into structure — element
fingerprints, UI regions, repeated/shared components, navigation menus — without
re-crawling. Reads the V1 model; writes a separate `Analysis` alongside it.
"""

from .engine import analyze_crawl

__all__ = ["analyze_crawl"]
