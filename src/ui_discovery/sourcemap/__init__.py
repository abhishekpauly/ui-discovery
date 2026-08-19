"""V4 — source-code correlation.

Two halves, deliberately separable:

* `index.py` (V4.1) reads a frontend repo **statically** and builds a
  `SourceIndex` of components, routes and API call sites. The repo is never
  executed, no build is run, and nothing is installed — this is text analysis
  over files, so pointing it at unfamiliar code is safe.

* `correlate.py` (V4.2) links what the crawler observed to what the index
  found, and attaches a confidence level and the evidence behind it.

Both are framework-agnostic heuristics. They look for the shapes that any
component-based frontend produces — exported symbols in component files,
route tables, string literals passed to fetch/axios — never for React or Vue
specifically. That means they are approximate by construction, which is
exactly why every correlation carries its confidence and evidence instead of
asserting a match.
"""

from .correlate import correlate
from .index import index_repo
from .reports import write_correlation, write_source_index

__all__ = ["index_repo", "correlate", "write_correlation", "write_source_index"]
