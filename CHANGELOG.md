# Changelog

All notable changes to the UI Discovery Engine are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/). This is a `0.x` project, so minor bumps may add
capabilities freely; patch bumps are fixes/hotfixes.

**Two version numbers are tracked separately:**
- **Product version** (`pyproject.toml` `version`, `__version__`) — bumped every
  release below.
- **Schema version** (`SCHEMA_VERSION`) — the JSON model shape. Still `0.1.0`
  because all growth so far is *additive* (new optional fields / new models),
  not breaking. Bump it only when a change would break readers of old snapshots.

The "V0…V5" phase names used in planning map to product versions as noted.

---

## [Unreleased]

Nothing pending. Remaining roadmap items are deliberately deferred:
`X3` (CI) needs a git remote to exist first; `X4` (incremental crawl) is a
speculative optimization until crawl times actually hurt; `X6` (storage
backend) is explicitly deferred in `ROADMAP.md` until data volume demands it.

---

## [0.15.0] — Relationships, controls and visual capture

The engine could say *what* it found; operators fed back that it could not say
what the product **is**. Endpoints and URLs were present and nothing
human-readable was: no way to see how screens connected, what a dropdown
offered, or what a modal contained, and no picture of anything that is not on
a settled page. This release is that gap.

### ⚠️ Behaviour change

**The interaction probe is now on by default** (`capabilities.probe: true`).
A capture that never clicks anything cannot see a modal, a menu, a tab panel or
an API call, which is most of what a portal is. Crawls therefore take longer and
do interact with the target — under the same two unchanged safety gates.

Scope it down rather than off, per module and per tab:

```yaml
probe:
  tabs: listed
  tab_labels: [Overview, Activity]
  tab_exclude: [Audit Log]
modules:
  - name: Reports
    start_url: /reports
    probe: {enabled: false}     # read, never clicked
```

Or turn it off entirely with `--no-probe`.

### Added

- **Relationship layer** (`relations.py`, `relations.json`, written every run).
  Screen-to-screen edges now carry the *label of the control that reaches them*,
  so the page graph answers "how do I get there?". Element-to-element links are
  computed per screen from standard markup: containment (`parent_path`),
  `aria-controls` (tab → panel, button → dialog) and form ownership.
- **What controls offer.** `<select>` options and the selected one, ARIA
  listbox / radiogroup / menu / tablist items, control state (checked,
  required, expanded, sorted, readonly) read from DOM *properties*, table
  columns and row counts, help text, fieldset grouping. A radio set is reported
  as **one** choice, not N controls.
- **Component screenshots** — every form, dialog, tab panel, data table and
  labelled region cropped to itself (`screenshots/components/`).
- **Revealed-state capture** — the modal, drawer, menu, tab panel or disclosure
  each probed click opens is photographed (`screenshots/states/`) and recorded
  with its contents and **what opens it**. Introduces no new interaction: it
  rides on clicks the probe already makes.
- **Rewritten crawl report.** `report.md` / `report.html` are now a walkthrough
  of the product: a Mermaid site map with labelled edges, a "how the screens
  connect" table, and per screen its picture, actions (with the engine's safety
  verdict), forms as field tables, data tables with columns and row actions,
  and every modal/panel with a picture. HTML gains a table of contents,
  dark-mode support and per-screen collapsing.
- **Per-module / per-tab probe configuration** — `ProbeSettings` in the scope
  config, resolved **flags > module > top-level `probe:` > capabilities**, with
  `--no-probe`, `--no-state-capture` and `--no-component-screenshots`. Pages are
  matched to modules by the same longest-prefix rule that decides their output
  folder (`util.module_for_path`), so the two can never disagree.
- `controls.csv` — every clickable with its label, type, region, options and
  destination. `elements.csv` gains options, state, relationships and crops.
- `docgen` consumes the relationship layer: page purposes now name the actual
  forms, tables and columns instead of describing a shape.
- Public API: `build_relations`, `screen_edges`, `element_links`, `forms_of`,
  `tables_of`, `write_relations`.

### Fixed

- **Two long-standing privacy leaks.** A password field's value was written into
  `attributes.value` on every capture, and Playwright's ARIA snapshot rendered
  typed field values inline (`- textbox "API token": hunter2`) into
  `accessibility_tree`. Both are redacted now: the field and its structure are
  kept, what someone typed is not. `attributes.value` survives only for controls
  where it names the thing (`input[type=submit]`) or is a choice.
- Form fields are reported in **reading order** rather than extractor-category
  order, which had put a form's fourth field last.
- A checkbox's "default" no longer reads `on` — it reads checked / unchecked.

### Tests

+121 (412 → 533): `test_relations.py`, `test_uistate.py`,
`test_report_readability.py`, `test_probe_config.py`, plus the
`fixtures/forms/` site. `SCHEMA_VERSION` stays `0.1.0` — every model change is
additive.

---

## [0.14.0] — 2026-08-20  ·  Two things the operator no longer has to know  (minor)

Both of these were previously documented workarounds. A workaround only helps
someone who already knows the problem exists, which is the wrong bar.

### Added
- **Held-open connections are detected, and change how long we wait.** An app
  keeping a websocket or SSE stream open never reaches `networkidle`, so the
  DOM plateau is the only evidence available — and a pause between render
  bursts looks exactly like being finished. The engine now wraps the
  `WebSocket` and `EventSource` constructors, so it sees the connection
  whether or not it succeeds, and demands six consecutive quiet polls instead
  of two. Reported as `readiness.held_open_connection`.

  This removes the need for a hand-written `extra_wait` adapter on such apps.
  On the portal that motivated it, two consecutive crawls went from differing
  by **565 elements to differing by one** (on the page with a live-updating
  list), and captured *more* than the manual adapter did — 106 elements per
  page against 101.

  The check runs on every poll rather than once up front: the socket opens a
  beat into page load, so sampling it early read zero on a page about to hold
  one open for its lifetime.
- **Session pre-flight.** A saved session's own expiry is read before the
  crawl starts, so a lapsed one costs a second rather than a full crawl of
  login screens. Exits 2 when expired, and prints the re-capture command.

### Fixed
- The first version of that pre-flight took the earliest expiry across every
  credential in the storage state and declared a **working** session dead: a
  session captured through Google SSO also holds that provider's cookies, and
  one had lapsed while the portal's own token had 16 hours left. It now
  consults only the target origin's bearer token, and reports "unknown"
  rather than guessing for cookie-only sessions.

---

## [0.13.0] — 2026-08-20  ·  Coverage, deliverables, and a UI taxonomy  (minor)

Driven by running the engine against a live portal rather than fixtures.
Every item below started as something the capture got wrong or left out.

### Added
- **Deep navigation discovery.** Some apps build a sidebar from plain `<div>`s
  with click handlers — no anchor, no button, no ARIA role — so link-following
  cannot see where they lead, and neither can a screen reader. The crawler now
  clicks elements that only `cursor: pointer` identifies as clickable and
  records both outcomes that matter: navigating, and revealing links by
  expanding a submenu. **On by default**; `--no-deep-nav` opts out. Took a
  real portal from 0 of 7 requested screens to 7 of 7 with no seeds.
- **Navigation reveal** — collapsed menus are expanded before links are read.
- **Seed URLs** — `--seed`, and `modules:` in a config, which had been declared
  in the schema since the config bundle while being consumed by nothing.
- **UI type taxonomy** (`taxonomy.py`) — every element carries a `ui_type`
  alongside `category`: slider, tab, breadcrumb, file upload, rich-text
  editor, drawer. 64 types, resolved deterministically from
  `aria-roledescription` → explicit `role` → input `type` → implicit element
  role → state signals. `summary.md` reports coverage in three buckets:
  found, absent from this app, and **not deterministically detectable**.
- **Run artifacts on every crawl** — `summary.md`, `urls.txt`, `elements.csv`
  (with `ui_type`), `endpoints.md`, `inventory.json`, written unconditionally.
- **Captures go to Downloads**, in a product folder split module by module,
  each module folder self-contained. `crawl.json` is never split.
- **X1 `pipeline`** — crawl → analyze → semantic → docgen → qagen in one
  command. **X5 politeness** — rate cap, concurrency cap, robots.txt.
- **Headed by default** from the CLIs; `--headless` for CI.
- `RUNBOOK.md` and `PRODUCT_GUIDE.md`.
- Guards against three classes of recurring defect: dead config fields
  (`test_no_dead_config.py`), version/changelog drift
  (`test_release_hygiene.py`), and features whose tests pass without them
  (paired negative controls).

### Changed
- **Destructive-label matching is on word boundaries.** `BLOCK_WORDS` used
  substring matching, so a real portal refused "Crunchbase" (contains "run"),
  "Omnisend" and "Resend Email" ("send"), "Payments" and "Payroll" ("pay") —
  thirteen refusals, six of them nonsense. Erring toward refusal is right;
  erring toward refusing arbitrary things costs coverage on every run and
  teaches a reader to discount the refusals that are real. camelCase is split
  first, so `DeleteAll` still blocks, and words substring matching had been
  catching by luck (`resend`, `rerun`, `terminate`, …) are now explicit.
- `crawl_site` takes a `CrawlOptions` object instead of 23 keyword arguments.
  Existing keyword calls are unaffected.
- A truncated crawl says so: `summary.md` leads with "This capture is
  incomplete" and lists the screens it found but never visited.

### Fixed
- **X5 silently raised browser concurrency from 1 to 10**, starving page
  rendering. One page dropped from 528 elements to 28, and two crawls of an
  *unchanged* site diffed to 594 phantom removals.
- **A page that had not begun rendering was mistaken for a settled one** — an
  app shell has an unchanging DOM, so stability fired after ~500ms and every
  later stage recorded zero elements. Stability now requires rendered content.
- Stability was accepted after 500ms of quiet even when the network had never
  gone idle, which under load is just a gap between render bursts.
- Deep-nav re-clicked the same global sidebar on every page, turning a
  three-minute crawl into a timeout.
- Enqueueing our own resolved links bypassed the scope gate, so an excluded
  area would have been crawled anyway.
- `extract.js` pre-computed a role that flattened every exotic input to
  "textbox", hiding file uploads and date pickers.

---

## [0.12.0] — 2026-08-19  ·  Roadmap complete: hardening, config, adapters, source correlation  (minor)

Everything from `ROADMAP.md` except the three deferred items above. This
covers eleven roadmap items shipped since 0.8.0; the version had drifted badly
behind the code.

### Added
- **X0** — git baseline. The project is now version-controlled, one branch and
  commit per roadmap item.
- **R1** — a formal library/SDK surface. Every capability is importable from
  `ui_discovery` and composable without touching a CLI; the CLIs are thin
  wrappers over the same functions. Exports resolve lazily, so
  `import ui_discovery` stays cheap and AI-free.
- **H1** — query-string and SPA route normalization. `--dedupe-queries`
  (plus `--drop-param`) collapses tracking/session variants; `--hash-routes`
  makes `#/route` fragments distinct pages. Both applied to the page graph
  *and* Crawlee's request queue so counts agree; both off by default.
- **H2** — the safe interaction/network probe runs on every crawled page
  (`crawl --probe`), as the logged-in user, in one pass. `interactions.py`
  gained an async core operating on a page the crawler already has open.
- **H3** — shadow DOM and iframe traversal. Open shadow roots are queried
  (boundaries marked with ` >>> ` in `dom_path`, plus `shadow_depth`);
  same-origin iframes are merged with `frame`/`frame_path` provenance;
  cross-origin frames are recorded but not entered.
- **H4** — session-expiry detection. Four signals (visible password field,
  login URL segment, logged-out title/heading, and a settled page that
  rendered *nothing*) set `stats.auth_expired`, banner the report and print
  the re-capture command. `--fail-on-auth-expiry` exits 2 for CI.
- **H5 + R2 + S1** — scope configs (`--config scope.yaml`), capability
  toggles, and `python -m ui_discovery.intake` to generate and `--check` one.
  Includes URL include/exclude scoping, dropped before enqueue so an excluded
  area is never fetched.
- **R3** — the adapter seam. Site-specific behavior registers as named
  adapters (`extra_wait`, `extra_headers`, `skip_paths`, `logged_in_marker`)
  instead of accumulating as special cases in the core.
- **C1** — deterministic change diff (`python -m ui_discovery.diff old/ new/`):
  pages, elements and components added/removed, plus **renamed controls**
  matched by fingerprint or, for structurally-identified elements, by a
  name-independent key. Ambiguous pairings stay add+remove.
- **V4** — source correlation (`python -m ui_discovery.sourcescan <repo>`).
  Reads a frontend repo as text (never executes it) into a `SourceIndex`, and
  links runtime observations to it with a confidence level and evidence for
  every claim.
- **V5.4** — a readable change narrative over the C1 diff. Deterministic by
  default; `--provider` rewrites the prose only.
- **X1** — `python -m ui_discovery.pipeline`: crawl → analyze → semantic →
  docgen → qagen in one command. A failing report stage never discards the
  crawl.
- **X5** — politeness: `--max-requests-per-minute`, `--max-concurrency`,
  `--respect-robots-txt`. Defaults are unchanged behavior.

### Fixed
- Sessions with tokens in `localStorage` were never actually restored — the
  injected script was a function expression that was defined and never
  invoked, so authenticated crawls silently captured login pages. Now pinned
  by a regression test verified to fail against the old code.
- Extraction and screenshots fired before SPAs finished rendering.
  `networkidle` only tracks network traffic, so a DOM-stability poll now runs
  after it; new `dom_stable` / `dom_stable_wait_ms` readiness signals.
- **A page that had not begun rendering was mistaken for a settled one.** An
  app shell produces an identical DOM on every poll, so the stability check
  above declared it stable after ~500ms and every later stage faithfully
  recorded zero elements — and H4 then reported a healthy session as
  rejected. Stability now requires *rendered* content (measured by rendered
  text and interactive elements, not markup size — a shell's inline script
  can be kilobytes while it displays nothing). On the live portal this took
  two pages from 0 elements to 47 and 91, and one page that had been captured
  half-rendered now waits 2.2s for its content.
- IDREF lookups (`aria-labelledby`, `label[for]`) resolved against `document`,
  which is wrong across a shadow boundary; the landmark walk stopped at a
  shadow root instead of continuing through the host.
- Elements inside an iframe could be clicked by the probe using a
  frame-relative selector resolved against the page — which can match a
  *different* element. They are observe-only now.
- `load_storage_state` rejected session files carrying a UTF-8 BOM.
- **X5 silently raised browser concurrency from 1 to 10.** Crawlee defaults
  browser crawlers to `desired_concurrency=1` because parallel browser pages
  starve each other's rendering; passing our own `ConcurrencySettings`
  unconditionally overrode that. Pages then settled half-rendered — one went
  from 528 elements to 28 — and two crawls of an *unchanged* site diffed to
  594 phantom removals. Politeness settings are now only sent to Crawlee when
  actually requested, and keep `desired_concurrency=1`.
- Stability was declared after 500ms of quiet even when the network had never
  gone idle, which under load is just a gap between render bursts. A page
  still fetching now has to stay quiet four times as long.
- A concurrency cap below 10 was rejected outright by Crawlee's default
  `desired_concurrency` — the exact value someone throttling a shared host
  would reach for.
- The Anthropic provider default was pinned to a stale model.
- `tests/conftest.py`'s server never released its port (`shutdown()` without
  `server_close()`), which hung any test that rebound one.

### Changed
- `crawl_site` takes a `CrawlOptions` object instead of 23 keyword arguments.
  Existing keyword calls still work unchanged (`crawl_site(url, max_depth=2)`),
  and a mistyped option is still a `TypeError`.
- `pyyaml` added to core dependencies (pinned `6.0.3` — the first release with
  a Python 3.14 wheel). JSON configs work without it.
- `SCHEMA_VERSION` stays `0.1.0`: every model change above is additive (new
  optional fields), so old snapshots remain readable.

---

## [0.8.0] — 2026-08-12  ·  QA / test generation + Playwright export (V5.3 + C2)  (minor)

### Added
- `qagen` CLI (`ui_discovery.qagen`) — generates candidate **test scenarios**
  (smoke, navigation, form, destructive-guard, interaction) from crawl (+
  analysis + semantics + probe if present) → `qa.json` / `qa.md` / `qa.html`.
- **Playwright test-skeleton export (delivers roadmap C2):** runnable
  `generated_tests.py` (or `.spec.ts` with `--lang ts`) built from the stable
  role + accessible-name selectors. **Destructive controls are never
  automated** — they become explicit "SKIP (guard)" lines; forms are fill-only.
- **Deterministic by default (zero tokens);** optional `--provider` writes a
  test-strategy narrative on top (shared quarantined `llm.py`), never changing
  the scenarios.
- `TestStep` / `TestScenario` / `QAPlan` models; QA reports in `reports.py`.
### Changed
- AI-free guard extended to `ui_discovery.qagen`.
### Tests
- +6 (scenarios, generated-Playwright-compiles, destructive-guard, mock strategy).
  **Total: 110.**

---

## [0.7.0] — 2026-08-12  ·  Documentation generation (V5.2)  (minor)

### Added
- `docgen` CLI (`ui_discovery.docgen`) — assembles a **UI reference document**
  from crawl (+ analysis + semantics if present) → `documentation.json` / `.md`
  / `.html`: executive overview, global navigation, shared components, and a
  per-page reference (purpose, regions, controls grouped by semantic role,
  links, screenshot).
- **Deterministic by default (zero tokens);** degrades gracefully without
  analysis/semantics (falls back to category grouping).
- **Optional LLM prose, quarantined:** `--provider mock|anthropic|openai` has the
  model write the overview + per-page purpose *on top of* the deterministic
  scaffold; AI-drafted prose is marked as such and never mutates source models.
- Shared `ui_discovery.llm` text-provider seam (Mock + lazy Anthropic/OpenAI) —
  the reusable quarantined LLM layer for V5 generation features.
- `Documentation` / `DocPage` models; documentation reports in `reports.py`.
### Changed
- AI-free guard extended to `ui_discovery.llm` and `ui_discovery.docgen`
  (imports load no AI library).
### Tests
- +6 (deterministic doc, no-analysis fallback, mock prose, provider seam).
  **Total: 104.**

---

## [0.6.0] — 2026-08-12  ·  Semantic classification (V5.1)  (minor)

### Added
- `semantic` CLI (`ui_discovery.semantic`) — labels every fingerprinted element
  by semantic role (primary/secondary action, navigation, filter, data display,
  destructive, form input, informational) → `semantics.json` / `.md` / `.html`.
- **Deterministic by default (zero tokens):** classifies from role / accessible
  name / landmark / safety class; needs no provider, key, or network.
- **Optional LLM refinement, quarantined:** `--provider mock|anthropic|openai`
  refines labels *on top of* the deterministic pass; providers import their SDK
  **lazily** (module import stays AI-free), live only under the `[semantic]`
  extra, and outputs never mutate the analysis. `mock` is an offline stand-in
  for testing/demo.
- `SemanticLabel` / `Semantics` models; semantics reports in `reports.py`.
### Changed
- AI-free guard extended to cover `ui_discovery.semantic` (its import loads no
  AI library); the suite passes with the `[semantic]` extra NOT installed.
### Tests
- +10 (deterministic classification, mock refine, plumbing). **Total: 98.**

---

## [0.5.1] — 2026-08-12  ·  AI-free runtime guarantee  (hotfix)

### Added
- Architecture principle #13 — **runtime is AI-free and self-contained** (no
  LLM, no API key, no tokens, no external service beyond the target). AI is a
  detachable opt-in enrichment for V5 only. Documented in `ARCHITECTURE.md` /
  `CLAUDE.md`.
- Enforceable guard `tests/test_no_ai_runtime.py`: fails the build if the core
  imports any AI/LLM library, lists one as a core dependency, or reads a provider
  API key. The optional `[semantic]` extra is the sole quarantined home for V5's
  future AI deps.
### Changed
- Crawler pins Crawlee's `tldextract` to its bundled public-suffix snapshot, so
  same-domain checks make **no network fetch** — crawls depend on nothing beyond
  the target site.
### Tests
- +4 (AI-free runtime guards). **Total: 88.**

---

## [0.5.0] — 2026-08-12  ·  Session-based authentication  (minor)

### Added
- `login` CLI (`ui_discovery.login`) — opens a visible browser, you log in by
  hand, and the session (`storage_state`: cookies + localStorage) is saved.
- `--auth-state session.json` on `extract`, `crawl`, and `probe`; the session is
  applied to every page so authenticated portals can be captured as the logged-in
  user. Crawler applies it via a Crawlee pre-navigation hook (cookies +
  localStorage).
- `auth.py` (load/validate storage state, capture helper); `session.json` added
  to `.gitignore` (treated as a secret).
### Tests
- +7 (cookie-gated fixture server proving session reuse; storage-state
  validation). **Total: 84.**

---

## [0.4.1] — 2026-08-12  ·  QA hardening  (hotfix)

### Fixed
- `body_present` readiness falsely reported `false` on empty/minimal pages — the
  wait used `state="visible"`; an empty `<body>` has zero height. Now waits for
  `state="attached"` (extractor + crawler).
- Duplicate `id`s collapsed distinct elements onto one `dom_path` — the `#id`
  shortcut is now taken only when the id is unique on the page.
### Added
- `requestfailed` network handler in the probe (blocked/aborted requests are now
  captured).
- Adversarial fixtures (`fixtures/edge/`) and a rainy-day test suite; `QA_REPORT.md`.
### Tests
- +16 edge/negative-path tests. **Total: 77.**

---

## [0.4.0] — 2026-08-12  ·  Safe interaction + network probe (V3)  (minor)

### Added
- `probe` CLI (`ui_discovery.probe`) — discovers interactive elements and
  executes only structurally-safe, reversible ones (tabs, accordions, menus,
  disclosures), recording before/after state and reverting after.
- Deterministic two-gate safety model (`safety.py`): allow-list of interaction
  types + SAFE/CAUTION/BLOCK label classifier. Nothing destructive is clicked.
- Network observation (`network.py`): method/url/status only, secrets redacted,
  endpoints normalized to `:id`, GraphQL/API detection. No headers or bodies.
- Probe reports (`probe.json` / `.md` / `.html`).
### Tests
- +22 (safety classification, destructive-override, redaction, live probe).
  **Total: 61.**

---

## [0.3.0] — 2026-08-12  ·  Analysis layer (V2)  (minor)

### Added
- `analyze` CLI (`ui_discovery.analyze`) — reads the immutable crawl and writes
  `analysis.json` / `.md` / `.html`. No re-crawling.
- Element **fingerprinting** (`analysis/fingerprint.py`): stable per-element
  identity (data-testid → id → structural), generated-id resilient.
- UI **region** inference from landmarks; **component** detection (shared across
  pages + repeated within page); **navigation**-menu extraction.
### Tests
- +15 (fingerprint determinism/stability, component/region/nav detection).
  **Total: 39.**

---

## [0.2.0] — 2026-08-12  ·  Crawler + UI Crawl Report (V1)  (minor)

### Added
- `crawl` CLI (`ui_discovery.crawl`) — Crawlee `PlaywrightCrawler` drives the V0
  extractor across a same-domain site; request queue, dedup, retries, `--max-
  pages` / `--max-depth` budgets.
- Page graph (depth + navigation edges) and UI Crawl Report (`crawl.json` /
  `report.md` / `report.html` + per-page screenshots).
- Shared `assemble_page()` so sync (V0) and async (crawler) reuse model-building.
### Fixed
- Chromium `--no-sandbox` under root; per-crawl in-memory storage isolation so
  repeated crawls in one process stay clean.
### Tests
- +12 (crawl completeness, depth/page budgets, same-domain filtering, reports).
  **Total: 24.**

---

## [0.1.0] — 2026-08-12  ·  Single-page extractor (V0)  (initial)

### Added
- `extract` CLI (`ui_discovery.extract`) — renders one URL and emits a
  deterministic UI model (`page.json`) + screenshot.
- Framework-agnostic extraction (`extract.js`): per-element role, accessible
  name (+ source), text, visibility, enabled state, geometry, attributes,
  `dom_path`, sibling ordinal, landmark; plus the browser ARIA snapshot.
- Pydantic models with `schema_version`; robust readiness waits (no fixed
  sleeps); local HTML fixtures as the primary test surface.
### Tests
- 12 (extraction, schema round-trip, visibility, identity signals). **Total: 12.**
