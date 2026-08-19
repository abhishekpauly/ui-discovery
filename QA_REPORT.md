# QA Report — Rainy-Day & Edge-Case Testing (V0–V3)

*Engine 0.1.0 · schema 0.1.0 · all tests green*

This is a functional-hardening pass over the four built phases. It feeds the
engine deliberately adversarial input and asserts it **degrades gracefully
rather than crashing**. Two real bugs were found and fixed; every other scenario
behaved correctly.

## Test totals

| Suite | Tests |
|---|---|
| Core (V0–V3 happy path) | 61 |
| Edge / rainy-day (this pass) | 16 |
| **Total** | **77 passing** |

Run: `pytest -q` (all local fixtures over `file://` or an ephemeral localhost
server — no external network required).

## Bugs found and fixed

**1. `body_present` readiness falsely reported `false` on minimal pages.**
The readiness wait used Playwright's default `state="visible"`. An empty
`<body>` has zero height, so it is never "visible" and the wait timed out —
making the engine report the body as missing on any page with an empty or
not-yet-painted body. Fixed by waiting for `state="attached"` (presence, not
visibility) in both the sync extractor (`browser.py`) and the async crawler
(`crawler.py`).

**2. Duplicate `id`s collapsed distinct elements onto one `dom_path`.**
`cssPath` took a `#id` shortcut for *any* id. Two elements sharing an id (invalid
but common in real apps) produced identical paths, corrupting element identity.
Fixed: the shortcut is now taken only when the id is unique on the page
(`querySelectorAll('#id').length === 1`); otherwise it falls back to the
`nth-of-type` structural path.

## Scenarios covered, by phase

### V0 — extractor

| Scenario | Result |
|---|---|
| Empty page (`<body></body>`, empty title) | ✅ 0 elements, no crash, body detected (after fix) |
| Malformed HTML (unclosed tags, no `<head>`, stray text) | ✅ browser-repaired; elements still extracted |
| Duplicate `id`s | ✅ distinct `dom_path` per element (after fix) |
| Hidden variants (`display:none`, `visibility:hidden`, `opacity:0`, zero-size, `hidden` attr) | ✅ all correctly `visible:false` |
| `input type="hidden"` | ✅ excluded from inventory |
| Unicode / emoji / RTL / CJK in titles & labels | ✅ captured and serialized (`ensure_ascii=false`) |
| Deep nesting (60 levels) | ✅ no crash; `dom_path` depth-capped at 40 |

### V1 — crawler

| Scenario | Result |
|---|---|
| Broken link (404) among good links | ✅ crawl completes; 404 counted as `pages_failed`, others crawled |
| Non-HTML resource (`data.json`) linked same-domain | ✅ crawled gracefully, 0 elements, no crash |
| External domain link | ✅ never crawled (same-domain strategy) |
| `mailto:` / fragment-only / `javascript:` links | ✅ excluded |
| Self-link & revisits | ✅ deduped |
| Start URL 404 | ✅ returns a valid empty `Crawl`, no crash |
| `--max-depth 0` | ✅ start page only |
| `--max-pages` under concurrency | ✅ approximate cap (documented) |

### V2 — analysis

| Scenario | Result |
|---|---|
| Empty crawl (0 pages) | ✅ empty analysis, zeroed stats, no crash |
| Page with no catalogued elements | ✅ 0 fingerprints, no regions, no crash |
| Malformed/underspecified model | ✅ Pydantic validation guards the boundary |

### V3 — interaction probe

| Scenario | Result |
|---|---|
| Page with no interactive elements | ✅ executes nothing, no crash |
| Allow-listed control that **navigates away** | ✅ `route_changed` detected, recovered via `go_back`, ends on original URL |
| In-page **hash route** change | ✅ detected and reverted |
| Network request carrying a secret token | ✅ `access_token` value redacted; benign params preserved |
| Destructive control (`Delete`, `haspopup`) | ✅ refused despite allow-listed type (BLOCK label overrides) |
| Failed/blocked request | ✅ now captured via a `requestfailed` handler (added this pass) |

## Known limitations & environment notes (not bugs)

- **Query-parameter variants are crawled as distinct pages.** `good.html?x=1`
  and `good.html?x=2` are treated as two pages. Query-string normalization /
  canonicalization is a planned enhancement (it was scoped to V2 in the brief
  and is intentionally conservative today, since query often changes content).
- **`tldextract` public-suffix fetch in restricted networks.** On its first
  same-domain enqueue, Crawlee's `tldextract` tries to update the public-suffix
  list over the network. In a locked-down sandbox that request is blocked and a
  one-time traceback is logged — after which it falls back to its bundled
  snapshot and the crawl completes correctly. On a normal machine the list is
  fetched once and cached silently. This is environment noise, not an engine
  fault.
- **Duplicate-`id` fingerprints.** With the `dom_path` fix, duplicate-id
  elements are now structurally distinguishable, but if the `id` strategy is
  selected for fingerprinting, two same-id elements still hash alike. Duplicate
  ids are invalid HTML; the structural fallback covers the common case.
- **Coarse before/after signature.** The probe classifies *what changed* (route
  / dialog / expand / content hash), not a full DOM diff. Sufficient for safety
  and reversibility decisions; a finer diff is future work.
- **No shadow-DOM / iframe traversal** yet (unchanged from prior phases).

## Verdict

All four phases handle empty, malformed, hidden, unicode, deeply-nested,
broken-link, non-HTML, navigating, and secret-bearing inputs without crashing.
The two robustness bugs surfaced by this pass are fixed, and the full suite
(77 tests) is green.
