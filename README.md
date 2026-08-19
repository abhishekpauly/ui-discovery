# UI Discovery Engine

A framework-agnostic UI intelligence engine. It renders a permitted web
application in a real browser and produces a structured, deterministic model of
its UI — not scraped text. The roadmap is **complete** as of 0.12.0:
extractor, crawler, structural analysis, safe interaction/network probe,
change diff, scope configs, adapters, source correlation, and generated docs
and test skeletons.

See `ui-discovery-engine-brief.md` (in the project) for the full vision and the
V0→V5 phase plan.

**New here?** Two guides:

- [`RUNBOOK.md`](RUNBOOK.md) — step-by-step, copy-pasteable commands to run a crawl.
- [`PRODUCT_GUIDE.md`](PRODUCT_GUIDE.md) — what it can do, how it works, and how far to trust the output.

Every run writes, into `output/<product>/`: `summary.md` (start here), `urls.txt`,
`elements.csv`, `endpoints.md`, `screenshots/`, plus the canonical `crawl.json`.
The browser is **visible by default** — pass `--headless` for CI.

## What's built

**V0 = the single-page extractor.** Given one URL, it renders the page and emits
a deterministic UI model (`page.json`) plus a screenshot. No crawling.

**V1 = the crawler.** Wraps the trusted V0 extractor in **Crawlee**
(`PlaywrightCrawler`) to walk a same-domain site — request queue, dedup,
retries, concurrency, depth + page budgets — feeding every discovered page
through the extractor and assembling a **page graph** + a **UI Crawl Report**
(`crawl.json` + Markdown + HTML + screenshots).

**V2 = the analysis layer.** Reads the immutable `crawl.json` (no re-crawling)
and turns raw observations into structure:

- **Element fingerprinting** — a stable per-element identity computed from the
  captured signals (human-authored `data-testid`/`id` when available, else a
  CSS-refactor-resilient structural signature + role + accessible name). This is
  the identity primitive V5 will diff across crawls.
- **UI regions** — inferred per page from accessibility landmarks (never assumed).
- **Components** — *shared* (controls recurring across pages: the global nav,
  header/footer) and *repeated* (shapes recurring within a page: table-row
  actions, list items).
- **Navigation menus** — extracted from nav landmarks, with breadcrumb flagging.

**V3 = the safe interaction + network probe.** The first phase that *interacts*
with a page. For one URL it discovers interactive elements and **executes only
structurally-safe, reversible ones** — recording a before/after state signature
and observing network/API calls (secrets redacted). Its safety model is two
deterministic gates (no LLM):

1. **Allow-list of interaction types** (primary) — only `tab`, `expander`,
   `disclosure`, `menu` are ever clicked. Everything else is observed, not
   executed. An allow-list fails toward "missed coverage", never "clicked
   something destructive".
2. **SAFE / CAUTION / BLOCK label classifier** (secondary) — runs *in addition*,
   so a control labelled "Delete"/"Pay" is refused even when its type is safe.

Execute iff *type ∈ allow-list* **and** *label == SAFE*.

The split is deliberate: **Crawlee is infrastructure, our code is the product.**
Everywhere, it observes **the browser and web standards**, never the frontend
framework — there is no React/Angular/Vue branch anywhere.

## Install

```bash
python -m venv .venv && source .venv/bin/activate    # optional
pip install -e ".[dev]"
python -m playwright install chromium                 # first time only
```

Pinned versions (recorded for reproducibility): Python 3.11, Playwright 1.56.0
(Chromium), Pydantic 2.13.3.

## Run — V0 (single page)

```bash
# Extract a single URL -> output/<slug>/page.json + screenshot.png
python -m ui_discovery.extract https://example.com

# A local fixture (file://)
python -m ui_discovery.extract "file://$(pwd)/fixtures/table.html"
```

```
output/<slug>/
  page.json        # the canonical, versioned UI model
  screenshot.png   # full-page screenshot
```

## Run — everything at once (X1)

```bash
# crawl -> analyze -> semantic -> docgen -> qagen, into one output folder
python -m ui_discovery.pipeline https://example.com --config scope.yaml

# skip stages you don't want
python -m ui_discovery.pipeline https://example.com --skip docgen --skip qagen
```

Every stage is the same function its individual CLI calls — the pipeline
orchestrates, it does not reimplement, and both it and `crawl` resolve their
settings through the same code so one config cannot mean two different crawls.
A failing *report* stage is a warning, not an abort: the crawl is the
expensive artifact and stays on disk.

### Sites where `networkidle` never fires

An SPA holding a websocket open never reaches network idle, so the generic
DOM-stability check is doing all the work — and it cannot tell "finished"
from "paused between fetches". Two crawls of an unchanged site then differ,
which makes `diff` unusable.

Give those apps a fixed settle window with the `extra_wait` adapter — see
`examples/websocket-spa.scope.yaml`:

```yaml
adapters:
  - name: extra_wait
    options: { ms: 4000 }
```

Measured on a real portal: two consecutive crawls went from differing by 594
elements to being **identical on all 8 pages**, with the only remaining diff
being genuinely dynamic content (a rotating carousel, live timestamps).

If a capture looks thin, compare `total_elements` across two runs before
trusting it — instability shows up as pages that shrink.

### Politeness (X5)

```bash
python -m ui_discovery.crawl https://portal.example.com     --max-requests-per-minute 60 --max-concurrency 4 --respect-robots-txt
```

Defaults are unchanged behavior (unlimited rate, Crawlee autoscaling,
robots.txt ignored — the engine is pointed at products you own and are
authorized to test). Set them when the target is shared infrastructure, where
a rate cap is the difference between a capture and an incident. Also
available as a `politeness:` block in the scope config.

## Run — V1 (crawl a site)

```bash
# Crawl a same-domain site -> output/<slug>/{crawl.json,report.md,report.html}
python -m ui_discovery.crawl https://example.com --max-pages 25 --max-depth 3

# Try it against the bundled multi-page fixture site:
python -m http.server 8000 --directory fixtures/site &
python -m ui_discovery.crawl http://127.0.0.1:8000/index.html --max-depth 2
```

```
output/<slug>/
  crawl.json           # canonical, versioned crawl model (source of truth)
  report.md            # Markdown UI Crawl Report
  report.html          # HTML UI Crawl Report (with screenshot thumbnails)
  screenshots/         # one full-page screenshot per crawled page
```

Budget flags: `--max-pages`, `--max-depth` (both enforced by Crawlee), plus
same-domain restriction and automatic URL dedup. The crawl records a page graph
(depth per page + navigation edges) and an aggregate UI inventory. (Note:
`--max-pages` is an *approximate* cap — a few in-flight requests may finish
after the limit is reached under concurrency.)

### Page identity (H1)

Two flags control what counts as "the same page". Both default to **off**, so
identity is unchanged unless you opt in:

```bash
# Collapse query-string variants that differ only in tracking/session noise
# (utm_*, gclid, sessionid, ...), so ?id=1&utm_source=a and ?id=1&utm_source=b
# are one page. Real params (?id=1 vs ?id=2) still separate pages.
python -m ui_discovery.crawl https://example.com --dedupe-queries

# Add your own noise params (repeatable; requires --dedupe-queries):
python -m ui_discovery.crawl https://example.com --dedupe-queries --drop-param tab

# Treat `#/route` hash fragments as distinct pages, for SPAs that route
# client-side via the hash. (A bare `#section` anchor is never a page.)
python -m ui_discovery.crawl https://example.com --hash-routes
```

Both settings are applied to the page graph *and* Crawlee's request queue, so
page counts agree, and both are recorded in `crawl.json`'s `config` block so a
snapshot always says how its page identity was computed.

## Run — V2 (analyze a crawl)

```bash
# Analyze an existing crawl -> analysis.json + analysis.md + analysis.html
python -m ui_discovery.analyze output/<slug>/
# (or point directly at the file)
python -m ui_discovery.analyze output/<slug>/crawl.json
```

```
output/<slug>/
  analysis.json        fingerprints, regions, components, nav menus (source of truth)
  analysis.md          Markdown analysis report
  analysis.html        HTML analysis report
```

V2 never re-crawls and never mutates `crawl.json` — it reads the stored model
and writes a separate analysis alongside it (append-only).

## Run — C1 (what changed between two snapshots)

```bash
# Compare two analyses of the same site (older first)
python -m ui_discovery.diff output/2026-08-01/<slug>/ output/2026-08-19/<slug>/
```

```
<newer>/
  diff.json            canonical diff model (source of truth)
  diff.md              Markdown change report
  diff.html            HTML change report
```

Reports pages added/removed/changed, elements added/removed, components
gained/lost, and — the payoff of fingerprinting — **controls renamed**:

```
## Renamed controls
- “Create customer” → “Add customer” (button) on `/index.html`  (matched by fingerprint)
- “Filter list” → “Refine list” (button) on `/customers.html`   (matched by structural)
```

A rename is reported as one change rather than an add plus a remove, because
it is a different fact about the product — a label moved, not a control
appeared. Two matching passes find them, since `fingerprint` is deliberately
name-sensitive:

- controls with a stable `data-testid` / `id` / `name` keep their fingerprint
  through a relabel → *same fingerprint, different name* (`match: fingerprint`);
- structurally-identified controls bake the name into the fingerprint, so
  those are recovered by pairing leftover adds and removes on a
  name-independent structural key (`match: structural`), **only** when the
  pairing is unambiguous.

Anything not confidently paired stays an add and a remove — the diff never
guesses a rename it cannot evidence. Fully deterministic: the same pair of
snapshots always yields the same diff, with no LLM and no network.

### Change narrative (V5.4)

`diff` also writes a readable summary of what changed — **deterministic by
default, zero tokens**, assembled from the diff's own fields:

```
This release shows 1 added page, 2 renamed controls, 7 added and 1 removed controls.

**Renames.** "Filter list" → "Refine list"; "Create customer" → "Add customer".
Renamed controls break tests and docs that match on label, and are the most
common cause of a suite going red after a release that changed nothing
functional.

_Structural changes only. Whether each one is intended is not something two
snapshots can answer._
```

`--provider anthropic|openai|mock` rewrites **the prose only**. The LLM is
handed the already-computed findings to phrase, never the snapshots to
analyse, and it cannot touch a single structured field — so the tables stay
the source of truth and are labelled *AI-drafted* when the prose is. A
provider that fails, refuses or returns nothing leaves the deterministic
summary in place rather than an empty one.

> **Snapshots must share an origin.** Pages are matched by absolute URL, and
> fingerprints embed the page URL, so this compares the *same site over time*
> (`prod` on Monday vs `prod` on Friday). Comparing across hosts (staging vs
> prod) currently reports everything as added + removed.
>
> Since re-running a crawl overwrites its output folder in place, keep each
> run separate to have two snapshots to compare — e.g.
> `--output output/$(date +%F)`.

## Run — V3 (safe interaction + network probe)

```bash
# Probe one page: click only safe/reversible controls, observe network
python -m ui_discovery.probe https://example.com --max-interactions 40

# Against the bundled interactive fixture (tabs, accordion, menu, a
# destructive dialog, and fetch calls):
python -m http.server 8000 --directory fixtures/interactive &
python -m ui_discovery.probe http://127.0.0.1:8000/index.html
```

```
output/<slug>/
  probe.json        interactions (with before/after) + network observations
  probe.md          Markdown probe report
  probe.html        HTML probe report
```

Nothing destructive is ever clicked; executed controls are reverted afterwards
(Escape / re-toggle). Network is recorded as method/url/status only — no headers
or bodies — and sensitive query values are redacted.

### Probing every page of a crawl (H2)

`probe` covers one page. To capture behavior site-wide — as the logged-in
user, in a single pass — add `--probe` to the crawl:

```bash
python -m ui_discovery.crawl https://portal.example.com \
    --auth-state session.json --probe --max-interactions 40
```

Each page is probed on the browser page the crawler already has open, so
there is no second browser and no second login. The probe runs *after*
extraction, the screenshot and link discovery, so those always see the
pristine page; anything that navigates is walked back so the crawl stays on
course. Results attach to each page as `pages[].probe` in `crawl.json`, and
the crawl report gains a probe summary, the observed API endpoints, and a
per-page probe line. The same safety rules apply as in single-page `probe`.

A probe failure never fails the crawl — the page's extraction is still valid
without it, and the failure is logged as a warning.

## Session expiry (H4)

A saved session eventually goes stale. Without a check, the engine keeps
crawling and reports "42 pages captured" — of the login screen. Every page is
checked, and the result recorded on `page.auth`:

| Signal | Meaning |
| --- | --- |
| `password-field` | A visible password input — you are not signed in |
| `login-url` | The final URL is a login/SSO path segment |
| `logged-out-title` / `-heading` | The page says "sign in", "session expired", … |
| `empty-page` | Settled, but **nothing rendered** — no headings, no controls |

That last one matters more than it looks. Some SPAs don't redirect when their
token is rejected; they render a blank screen. Observed on a real portal: a
crawl with a corrupted token reported `1 pages (0 failed)` and a white
screenshot, with nothing to indicate the capture was worthless.

Landing on a login page is only an *error* when a session was supplied —
without one it's expected. When a session was supplied and pages come back
logged-out or blank, the crawl sets `stats.auth_expired`, banners the report,
and prints the command to fix it:

```
[ERROR] Session appears REJECTED — of 1 crawled pages, 1 rendered nothing at all.
        This capture is of the login/blank state, not the product. Re-capture:
        python -m ui_discovery.login https://portal.example.com --output session.json
```

Add `--fail-on-auth-expiry` to exit non-zero (2) instead of just warning —
useful in CI, where a silent capture of login screens is worse than a failure.

Matching is on word boundaries, deliberately: substring matching would report
"De**signin**g reports" as a login page, and a false expiry turns a healthy
crawl into a spurious failure.

## Authenticated portals

For a portal behind a login, capture a session once, then reuse it. No
passwords, SSO or OTP handling lives in the tool — you log in by hand and the
browser session (cookies + localStorage) is saved.

```bash
# 1. Log in once (run LOCALLY — opens a visible browser). Log in, press Enter.
python -m ui_discovery.login https://portal.example.com/login --output session.json

# 2. Reuse the session in any command:
python -m ui_discovery.extract https://portal.example.com/dashboard --auth-state session.json
python -m ui_discovery.crawl   https://portal.example.com/         --auth-state session.json
python -m ui_discovery.probe   https://portal.example.com/settings --auth-state session.json
```

`session.json` grants access to your logged-in session until it expires — treat
it like a password, keep it out of version control (it is git-ignored), and
re-run `login` when it stops working. Only crawl portals you are authorized to
test.

## Run — V5 (semantic labels; deterministic by default)

Label every element by its semantic role (primary/secondary action, navigation,
filter, data display, destructive, form input, informational) from an analysis:

```bash
# Deterministic — zero tokens, no provider, no key, no network
python -m ui_discovery.semantic output/<slug>/
```

```
output/<slug>/
  semantics.json / .md / .html    # labels keyed by element fingerprint
```

An optional LLM can *refine* the labels on top of the deterministic pass — but
it's **quarantined** (architecture principle #13): off by default, its SDK lives
only under the `[semantic]` extra, and it never mutates the analysis.

```bash
pip install -e ".[semantic]"      # installs the optional AI provider
# then, with the provider's API key in your environment:
python -m ui_discovery.semantic output/<slug>/ --provider anthropic
# offline demo of the refine plumbing (no tokens):
python -m ui_discovery.semantic output/<slug>/ --provider mock
```

The engine — including this command — runs fully **without** the `[semantic]`
extra; `pytest` is green with no AI package installed.

## Run — V5.2 (documentation; deterministic by default)

Assemble a UI reference document (overview, global nav, shared components, and a
per-page reference with controls grouped by semantic role) from a crawl — using
`analysis.json` / `semantics.json` too if present:

```bash
# Deterministic — zero tokens
python -m ui_discovery.docgen output/<slug>/
```

```
output/<slug>/
  documentation.json / .md / .html
```

Optional LLM prose (quarantined; writes the overview + per-page purpose on top,
marked as AI-drafted, source models untouched):

```bash
python -m ui_discovery.docgen output/<slug>/ --provider mock       # offline demo
python -m ui_discovery.docgen output/<slug>/ --provider anthropic  # needs [semantic] extra + key
```

## Run — V5.3 (QA scenarios + Playwright skeletons; deterministic by default)

Generate candidate test scenarios and **runnable Playwright test stubs** from a
crawl (+ analysis + semantics if present):

```bash
python -m ui_discovery.qagen output/<slug>/            # Python skeletons (default)
python -m ui_discovery.qagen output/<slug>/ --lang ts  # TypeScript (@playwright/test)
```

```
output/<slug>/
  qa.json / qa.md / qa.html      # scenarios (smoke, navigation, form, destructive-guard)
  generated_tests.py             # runnable Playwright skeletons (or generated_tests.spec.ts)
```

Skeletons use the stable role + accessible-name selectors the engine captured.
**Destructive controls are never automated** — they appear as explicit
`SKIP (guard)` lines; forms are fill-only (never submitted). An optional
`--provider` adds an LLM test-strategy narrative on top (quarantined), leaving
the scenarios unchanged.

## What gets captured

Per page: `schema_version`, title, requested vs. final URL, viewport, and a
**readiness report** (which signals fired — DOM-content-loaded, networkidle,
body-present — and their timings, so you can judge whether the snapshot was
taken against a settled page).

Per element (buttons, links, inputs, selects, textareas, forms, images, tables,
dialogs, nav) — a generous **identity signal set**, captured now so stable
fingerprints can be *computed later* (V2) and change-analysis run (V5) without
re-crawling:

- computed `role` and `accessible_name` (+ `accessible_name_source`, so you can
  see how the name was derived)
- visible `text`
- `visible` and `enabled` state
- `bounding_box` geometry
- stable `attributes` (id, name, type, href, role, data-testid, aria-\*, …)
- `dom_path` and `sibling_ordinal` (distinguishes same-named siblings)
- `landmark` (which nav / header / main / dialog it lives in)

Plus the browser's own **ARIA snapshot** (`accessibility_tree`), kept alongside
the deterministic pass rather than instead of it.

### Shadow DOM & iframes (H3)

The extractor sees past two boundaries a plain `document.querySelectorAll`
misses — with deliberately different policies:

| Boundary | Traversed? | Why |
| --- | --- | --- |
| Open shadow root | yes | Component libraries put real controls there — it's part of your page |
| Nested open shadow roots | yes | Same reason, recursively |
| Closed shadow root | no | `element.shadowRoot` is `null` by web standards — genuinely unobservable, not skipped |
| Same-origin iframe | yes | Part of the product under test |
| Cross-origin iframe | **no** | Third-party content is outside the product under test |

Cross-origin frames are *recorded, not entered* — Playwright could read them;
choosing not to is a scoping decision. Every iframe seen is listed in
`page.frames[]` with `traversed` and a `reason`, so a snapshot always says
what it declined to look at:

```json
{ "key": "cross-origin", "url": "https://widget.vendor.example/…",
  "same_origin": false, "traversed": false,
  "reason": "cross-origin frame — recorded but not traversed …" }
```

Provenance is carried per element: `shadow_depth` (0 = light DOM) and
`frame`/`frame_path`. Shadow boundaries appear in `dom_path` as ` >>> `,
Playwright's shadow-piercing combinator, so the path stays resolvable:

```
main:nth-of-type(1) > open-widget#open-host >>> button#shadow-btn
```

> **Elements inside an iframe are never clicked by the probe.** Selectors do
> not cross frame boundaries, so a frame-relative `dom_path` resolved against
> the page could match a *different* element. They are observed only, with
> `skipped_reason: "inside an iframe (observed only)"`.

## Scope configs (H5 · R2 · S1)

Per-target behavior lives in a config file, not in flags or code. One file
says what to crawl, as whom, how much, what to capture and what never to
touch — and doubles as the audit record of what was in scope and why.

```bash
python -m ui_discovery.intake                  # interactive; writes scope.yaml
python -m ui_discovery.intake --template       # a filled-in example instead
python -m ui_discovery.intake --check scope.yaml   # validate + flag concerns

python -m ui_discovery.crawl --config scope.yaml   # url comes from the config
```

```yaml
target: acme-portal
start_url: https://portal.acme.example/
environment: staging
authorized: true
authorized_by: jordan
scope:
  include: ["/app/**"]
  exclude: ["/admin/**", "/billing/**", "/logout"]
auth:
  required: true
  state_file: session.json
budget: { max_pages: 25, max_depth: 3, max_interactions: 40 }
identity: { dedupe_queries: true, hash_routes: false, drop_params: ["tab"] }
capabilities:
  screenshots: true
  accessibility_tree: true
  probe: false
safety:
  block_words_extra: ["deactivate account"]
  never_touch: ["#danger-zone"]
privacy:
  redact_network_keys: ["account", "ssn"]
outputs:
  dir: ./output
  keep_history: true        # one dated folder per run, so `diff` has two snapshots
```

Three rules govern all of this:

- **Zero-config still works.** Every default reproduces today's behavior, so
  a bare `crawl <url>` is unchanged.
- **Precedence is flags > config > defaults.** Config-backed flags default to
  `None` internally so "the user typed `--max-pages 25`" is distinguishable
  from "argparse filled in 25" — otherwise a default nobody typed would
  silently beat the config.
- **If it is in the schema, it is wired.** A toggle that quietly did nothing
  would be worse than no toggle. An unknown key is an error, not a no-op, so
  a typo'd `budgt:` fails loudly instead of being ignored.

**Config can only tighten safety, never loosen it.** `block_words_extra` and
`never_touch` add restrictions; there is no way to remove a block word, and
`submit_forms: true` is rejected outright. Auth-expiry signals are likewise
*extended*, never replaced, so a config cannot accidentally blind the check.

Excluded paths are dropped before they are ever queued — an excluded area is
never fetched, not fetched-then-discarded. The scope that produced a snapshot
is recorded in `crawl.json` (`config.include` / `config.exclude` /
`config.capabilities` / `config.config_file`), so a capture always says what
it was and wasn't allowed to look at.

## Use as a library

Every capability above is importable and composable — the CLIs
(`python -m ui_discovery.crawl`, etc.) are thin wrappers over the same
functions exported from the top-level `ui_discovery` package:

```python
import ui_discovery

# V0 -> V1 -> V2, no CLI involved.
page = ui_discovery.extract_page("https://example.com")
crawl = ui_discovery.crawl_site("https://example.com", max_pages=10, max_depth=2)
analysis = ui_discovery.analyze_crawl(crawl)
paths = ui_discovery.write_analysis(analysis, "output/example.com")

# Authenticated sites: capture a session once, reuse it everywhere.
ui_discovery.capture_session("https://portal.example.com/login", "session.json")
auth_state = ui_discovery.load_storage_state("session.json")
crawl = ui_discovery.crawl_site("https://portal.example.com", auth_state=auth_state)

# V5 (deterministic by default; pass provider_name="anthropic" etc. to refine).
semantics = ui_discovery.classify_analysis(analysis)
doc = ui_discovery.generate_documentation(crawl, analysis, semantics)
qa_plan = ui_discovery.generate_qa_plan(crawl, analysis, semantics, probe=None)
```

Exports are resolved lazily (`ui_discovery/__init__.py`'s `__getattr__`), so
`import ui_discovery` stays cheap and AI-free even though `classify_analysis`
/ `generate_documentation` / `generate_qa_plan` live in V5 modules — nothing
in the import path pulls in an LLM SDK; see `tests/test_no_ai_runtime.py`.

## Design invariants (carried from the brief)

- **Structured data is the source of truth.** `page.json` (Pydantic-validated)
  is canonical; reports are rendered from it, never the reverse.
- **Deterministic core.** No LLM anywhere in the observation path.
- **Append-only, versioned snapshots.** `schema_version` on every write so
  future snapshots stay comparable — this makes V5 change-analysis nearly free.
- **Framework-agnostic.** Only browser/web-standards signals.

## Tests

```bash
pytest -q
```

Local HTML fixtures in `fixtures/` are the **primary regression surface**
(static page, client-rendered SPA, modal with visibility discrimination, data
table, TodoMVC-style app). Live sites are only ever smoke tests, never part of
the suite — they change under you.

## Project layout

```
src/ui_discovery/
  extract.py       # V0 CLI: url -> page.json + screenshot
  extraction.py    # sync extractor + shared assemble_page() (used by V0 & V1)
  browser.py       # navigation + readiness signals + ARIA snapshot
  extract.js       # the deterministic in-page pass (one evaluate round-trip)
  models.py        # Pydantic models (Page + Crawl; schema_version + identity)
  crawl.py         # V1 CLI: url -> crawl.json + report.md + report.html
  crawler.py       # V1 Crawlee PlaywrightCrawler driving the extractor
  reports.py       # renders crawl + analysis reports (Markdown/HTML)
  util.py          # slug, URL normalization, same-site filter, BFS depth
  analyze.py       # V2 CLI: crawl.json -> analysis.json + .md + .html
  analysis/        # V2 analysis layer:
    fingerprint.py #   stable element identity + component signatures
    regions.py     #   landmark-based region inference
    components.py   #   shared + repeated component detection
    navigation.py  #   nav-menu / breadcrumb extraction
    engine.py      #   orchestrator: Crawl -> Analysis
  probe.py         # V3 CLI: url -> probe.json + probe.md + probe.html
  interactions.py  # V3 single-page interaction probe (safe execution loop)
  safety.py        # V3 deterministic two-gate safety model
  network.py       # V3 request classification + secret redaction
fixtures/          # single-page regression surface (static, spa, modal, table)
fixtures/interactive/  # tabs/accordion/menu/dialog + fetch calls (probe tests)
fixtures/site/     # multi-page linked site for crawl tests
tests/             # unit + integration tests over fixtures
output/            # generated snapshots (git-ignored)
```

## Known limitations (V0–V2)

- `accessible_name` is a deterministic *approximation* of the ARIA
  accessible-name algorithm (aria-label → aria-labelledby → label → text →
  title → placeholder → alt). The browser's full ARIA tree is stored separately
  as `accessibility_tree` for cross-checking.
- **Page identity is URL-based** (fragment stripped, trailing slash normalized).
  SPA hash/route dedup — same URL / different view — is not handled yet.
- Regions are inferred from catalogued elements; a `<main>` that holds only
  headings/text (no interactive elements) currently produces no `main` region.
- The page graph is built from `<a href>`/role=button anchors; JS-driven
  navigation without an href is not followed (V3 territory).
- No shadow-DOM / iframe traversal yet.
- The V3 probe is **single-page and standalone** — it is not yet woven into the
  crawl (clicking on every crawled page is deferred until the probe is trusted).
- The probe's before/after signature is coarse (URL, visible-interactive count,
  dialog/expanded counts, a content hash) — enough to classify *what changed*,
  not a full DOM diff.
- Fingerprints are computed but **not yet diffed across crawls** — the diff /
  change-analysis consumer is V5. `--max-pages` is an approximate cap.
- **Query-string variants** (`p?x=1` vs `p?x=2`) are crawled as distinct pages;
  query normalization is a planned enhancement.
- In **network-restricted environments**, Crawlee's `tldextract` logs a one-time
  public-suffix-list fetch error, then falls back to its bundled snapshot and
  crawls correctly (environment noise, not a fault).

See `QA_REPORT.md` for the full rainy-day / edge-case testing pass (77 tests).

## Next step

Review a `probe.html`/`probe.json` and confirm the safe controls were exercised
(and reverted) while destructive ones were refused. Then **V4** (optional)
correlates the runtime UI with frontend **source code** — matching observed
elements/routes/API calls to components in a repo, each correlation carrying an
explicit confidence level and its evidence.
