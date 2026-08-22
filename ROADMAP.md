# ROADMAP — features to build next

Hand this to Claude Code. Read `CLAUDE.md` first for the principles and
commands. Work **one item at a time, top to bottom**, keeping `pytest -q` green.
Each item lists: **Goal · Build · Acceptance · Files · Effort · Depends-on.**
Effort is rough: S = a sitting, M = a day, L = multi-day.

Current state: V0–V3 + session auth, 84 tests passing. Everything below is
additive and must preserve the non-negotiable principles in `CLAUDE.md`.

---

## Recommended order (why)

Do the **deterministic, high-leverage** items first — they need no LLM, sharpen
coverage, and unlock the two things this engine is ultimately for (docs + tests):

`X0 → R1 → H1 → H2 → C1 → C2 → H3 → H4 → (H5 + R2 + S1) → V4 → V5`

Since shipped, the order continues: **`X3` (CI) → `O1…O5` (observability, complete
at 0.18.0) → `G1…G4` (governance) → `G5…G7` (redaction) → `M1…M4` + `H6…H8`
(discovery) → `L1…L3` + `C3` (liveness) → `I1…I3` (reachability) → `W1…W4` +
`H10` + `X9` (watch) → `PV1…PV3` + `H9` + `H11` (variants) → `T1…T3`
(vocabulary)**. `X3` was blocked on a git remote existing; once it does, CI is
what makes every later item cheap to verify.

Everything after `G4` arrived from two capability reviews and is sequenced by
file contention rather than by value — `models.py`, `config.py`, `crawler.py`
and `reports.py` are contended across all of it, so these run as consecutive
sprints, not parallel ones (`BRANCHING.md` § *Running sprints in parallel*).

One exception to "sequenced by contention", and it is deliberate: **`G5`–`G7`
jump the queue.** They are the only P0s in the set, and the reason is that
`QA.2` — a real run against a real authenticated portal — is already in flight.
Every such run between now and `G5` writes a capture folder containing real
customer names, addresses and account references in the element model, the ARIA
snapshot, `elements.csv` and every screenshot. `G4` (retention) governs how long
that survives; `G5`/`G6` govern whether it is ever written. Retention is the
weaker of the two guarantees and it does not go first.

After that the ordering is by leverage: discovery, because `M2`'s dry run makes
scoping every later run cheap; liveness, because `L1` is what `QA.4` is waiting
on; reachability, because it is the largest and uses `M1`'s seeds; watch,
because it needs `C1`, `C3` and `X9` in place before a nightly run is worth
scheduling; then variants and vocabulary, which are the two most additive and
the two least urgent.

X0 first — put the current green state under version control. R1 (library/SDK
surface) is foundational and cheap — do it early so everything after is
composable. Then hardening (H) makes real portals crawl cleanly, and C1/C2
(diff + test export) turn captures into deliverables. The **config bundle**
(H5 config file + R2 capability toggles + S1 operator intake) is best built
together once the capabilities it configures exist. V4 (source correlation) and
V5 (LLM layer) are the ambitious, optional payoffs and come last.

---

## X0 — Establish a version-control baseline (do this before anything else)  ·  Effort: S

- **Goal.** Capture the current, passing state (V0–V3 + auth, 84 tests) as the
  git baseline so every roadmap item lands as its own clean, reviewable commit
  on its own branch.
- **Build.**
  1. Confirm `pytest -q` is green **before** committing (don't baseline a red
     tree).
  2. `git init` if the repo isn't already initialized.
  3. Verify `.gitignore` excludes the things that must never be committed —
     `.venv/`, `output/`, `__pycache__/`, `*.egg-info/`, `.pytest_cache/`, and
     **`session.json` / `*.session.json`** (saved logins — treat as secrets).
     The repo's `.gitignore` already lists these; add anything missing.
  4. `git add -A && git commit -m "baseline: V0–V3 + session auth, 84 tests green"`.
  5. Adopt a branch-per-feature workflow: one branch per ROADMAP item
     (e.g. `feat/h1-url-normalization`), PR/commit per item, keep `main` green.
- **Acceptance.** `git status` is clean; `git log` shows the baseline commit;
  no `.venv/`, `output/`, or `session.json` is tracked (`git ls-files | grep -E
  'venv|output/|session.json'` returns nothing).
- **Files.** `.gitignore` (verify/extend only), git metadata. No source changes.
- **Depends-on.** none. **Do this first.**

---

## A. Hardening — make real portals crawl cleanly (do first)

### H1 — Query-string & SPA route normalization  ·  Effort: M
- **Goal.** Stop crawling `p?x=1` and `p?x=2` as separate pages, and give SPA
  hash/history routes a stable page identity.
- **Build.** Extend `util.normalize_url` with configurable query handling: drop
  known-noise params (utm_*, session ids, `sort`, `page` optionally), sort the
  rest, and add an option to treat `#/route` hash fragments as part of identity
  (currently stripped). Add a `--dedupe-queries` / config knob. Apply
  consistently in `crawler.crawl_site` (both the enqueue graph and the page
  graph) so counts match.
- **Acceptance.** New tests: two query variants of one path collapse to one page
  when enabled; a hash-routed SPA fixture yields distinct pages per `#/route`.
  Existing crawl tests still pass.
- **Files.** `util.py`, `crawler.py`, new `fixtures/spa_routes/`, `tests/`.
- **Depends-on.** none.

### H2 — Weave the safe probe into the (authenticated) crawl  ·  Effort: M
- **Goal.** Run V3's safe interaction + network probe on *every crawled page*,
  as the logged-in user, so behavior is captured site-wide — not just one page.
- **Build.** Add `--probe` to `crawl`. In the crawler's page handler, after
  extraction, optionally call the probe logic against the *same* async page
  (refactor `interactions.probe_page` so the core loop accepts an already-open
  page, mirroring how `extraction.assemble_page` is shared). Attach the probe
  result to each `PageNode` (new optional field) and fold network/interactions
  into the crawl report.
- **Acceptance.** `crawl --probe` over `fixtures/site` (add a couple of safe
  toggles there) records interactions per page; destructive controls still
  refused; nothing navigates the crawler off-course (reuse the route-recovery).
- **Files.** `interactions.py` (extract an async-friendly core), `crawler.py`,
  `models.py` (PageNode.probe), `reports.py`, `tests/`.
- **Depends-on.** none (auth already wired).

### H3 — Shadow DOM & iframe traversal  ·  Effort: M
- **Goal.** See elements inside open shadow roots and same-origin iframes (common
  in component libraries and embedded widgets).
- **Build.** In `extract.js`, recurse into `element.shadowRoot` (open roots) when
  walking, tagging elements with a `shadow_path` / `frame` provenance. For
  iframes, extract each same-origin frame via Playwright `page.frames()` and
  merge, prefixing dom_paths with a frame id. Cross-origin frames: record their
  presence, don't traverse (note it).
- **Acceptance.** A shadow-DOM fixture and an iframe fixture yield the inner
  controls with correct visibility and a frame/shadow marker; cross-origin frame
  is recorded but not entered.
- **Files.** `extract.js`, `extraction.py`, `models.py` (Element.frame/shadow),
  `fixtures/edge/`, `tests/`.
- **Depends-on.** none.

### H4 — Auth robustness: session-expiry detection + config  ·  Effort: S
- **Goal.** Fail loudly and helpfully when a saved session has expired, instead
  of silently crawling login pages.
- **Build.** Add a lightweight "are we logged in?" heuristic: after navigation,
  if the final URL matches a configurable `login_url_pattern` or the page title/
  H1 matches a `logged_out_signal`, mark the run `auth_expired=true` and warn
  (and optionally abort). Surface it in stats and the report. Verify localStorage
  origins from storage_state are applied in the crawler hook (cookies already
  are).
- **Acceptance.** Against the cookie-gated auth fixture, an expired/missing
  session sets `auth_expired` and warns; a valid one does not.
- **Files.** `auth.py`, `crawler.py`, `extraction.py`, `models.py`, `tests/`.
- **Depends-on.** none.

### H5 — Config file + capability adapters  ·  Effort: M
- **Goal.** Move per-site behavior (budgets, allow/deny URL patterns, query
  rules, auth signals, safety overrides) out of flags into an optional YAML
  config, per the "config over hacks" principle.
- **Build.** `--config site.yaml` on all CLIs; a `config.py` Pydantic settings
  model with sane defaults; flags override file. Document the schema.
- **Acceptance.** A config file reproduces a flag-driven run; precedence tested.
- **Files.** new `config.py`, all CLIs, `tests/`, README.
- **Depends-on.** H1 (so query rules live in config).

### H6 — Subdomain policy  ·  Effort: S
- **Goal.** `util.same_site` compares `netloc` exactly, so a product split across
  `app.example.com` and `admin.example.com` captures as two unrelated targets, or
  more often as one target with half its modules silently out of scope.
- **Build.** `scope.subdomains: same-host | registrable-domain | list` (default
  `same-host`, today's behaviour). `same_site` grows a policy argument; the
  registrable-domain case uses the `tldextract` instance `crawler.py` already
  pins offline — do not add a second suffix source or reach the network for one.
  `list` takes explicit hostnames, for the common case of two known hosts.
- **Acceptance.** A two-host fixture crawls as one site under
  `registrable-domain` and as one host under the default; an unrelated host is
  never enqueued under any policy; `test_util.py` covers each policy directly.
- **Files.** `util.py`, `config.py`, `crawler.py`, `fixtures/site/`, `tests/`.
- **Depends-on.** none.

### H7 — External links recorded, never followed  ·  Effort: S
- **Goal.** An outbound link is currently dropped without trace, so a report
  cannot distinguish "this product has no integrations" from "we were not
  authorized past this point". The authorization boundary should be visible in
  the capture, not inferred from its absence.
- **Build.** When a discovered link fails the same-site test, record it as a
  navigation edge carrying `external: true` and its label/region (the labelled
  edge `F6.1` already builds), and **never enqueue it**. Surface a short
  "leaves the product" table in the crawl report and in `relations.json`.
- **Acceptance.** A fixture page linking to an off-site host yields exactly one
  external edge with its label, zero requests to that host (assert on the
  request log, not on the page count), and a report row naming it.
- **Files.** `crawler.py`, `models.py`, `relations.py`, `reports.py`, `tests/`.
- **Depends-on.** H6 (both turn on the same-site decision, so land them together).

### H8 — Crawl failure ledger  ·  Effort: S
- **Goal.** A capture reports what it found and says nothing about what it
  missed. `Crawl.stats.discovered_not_captured` is a single integer; the URLs
  behind it are gone. "Is this product 40 screens, or 60 screens with 20
  failures?" is currently unanswerable from the artifacts.
- **Build.** A `failures` artifact rolling up every URL that errored, timed out,
  was refused by robots, or was dropped by budget, each with its reason and
  depth. **Extend** `discovered_not_captured` and the `page.skipped` event `O2`
  already emits — this is a rollup of existing facts, not a third tally. A
  `Not captured` section in `summary.md`.
- **Acceptance.** A crawl capped below the fixture site's page count lists every
  dropped URL with reason `budget`; a fixture with a deliberately broken link
  lists it with its status; the ledger's length equals
  `discovered_not_captured`, asserted in the test rather than by eye.
- **Files.** `crawler.py`, `models.py`, `inventory.py`, `reports.py`, `tests/`.
- **Depends-on.** none. Pairs with `O2`.

### H9 — Exclude the furniture  ·  Effort: S
- **Goal.** Cookie banners, chat widgets, session-timeout warnings and support
  bubbles are on every screen of a real portal, and the engine dutifully models
  all of them. They inflate element counts, invent components that span every
  page (`F2.3` finds them and is technically right), and put a third-party
  vendor's UI in the middle of a document about *your* product.
- **Build.** `capture.exclude_selectors` in the scope config: DOM subtrees
  excluded from extraction entirely. Distinct from `safety.never_touch`, which
  forbids *interacting* — this forbids *modelling*. Record the count excluded
  per page so the exclusion is visible rather than silent, and never let it
  exclude a landmark.
- **Acceptance.** A fixture with a cookie banner models zero banner elements
  when excluded and models them when not; the per-page count of exclusions
  appears in the report; excluding a `main` landmark is refused with a reason.
- **Files.** `extract.js`, `extraction.py`, `config.py`, `reports.py`, `tests/`.
- **Depends-on.** none.

### H10 — Capture an explicit list of URLs  ·  Effort: S
- **Goal.** Every entry point today is a *start URL to crawl from*. There is no
  way to say "capture exactly these forty screens" — which is what you want when
  re-checking a previous run's `urls.txt`, when `M2`'s map has been filtered by
  hand, or when a scheduled run (`W1`) should watch a fixed set rather than
  rediscover the site every night.
- **Build.** `--from urls.txt` on `crawl` and `pipeline`, plus a `urls:` list in
  the scope config. Each URL is still filtered by `ScopeRules` — a list is
  convenience, never an authorization bypass. Invalid or out-of-scope entries
  are reported and skipped rather than aborting the run, and land in `H8`'s
  ledger with a reason.
- **Acceptance.** A list of three fixture URLs captures exactly those three and
  follows no links; an out-of-scope entry is skipped with a reason and appears
  in the failure ledger; the file `M2` writes is directly consumable, asserted
  by round-tripping map → capture.
- **Files.** `crawl.py`, `pipeline.py`, `crawler.py`, `config.py`, `tests/`.
- **Depends-on.** M2 (produces the list), H8 (records the skips).

### H11 — TLS verification as a recorded decision  ·  Effort: S
- **Goal.** Internal staging environments routinely serve self-signed or
  internally-rooted certificates, and the engine currently cannot reach them at
  all. The fix is one flag; the point of this item is that it must not be a
  quiet one.
- **Build.** `politeness.verify_tls` (default `true`). When disabled, the run
  proceeds **and** the manifest records that it did, in the safety envelope
  `G2` builds — because a capture that silently skipped certificate validation
  is a capture whose provenance is weaker than it looks. A warning on every run,
  not just the first.
- **Acceptance.** A fixture served over a self-signed certificate fails by
  default with a message naming the flag, and succeeds with it set; the manifest
  of the second run says so; the default is never changed by any other config
  key.
- **Files.** `browser.py`, `config.py`, `run.py`, `tests/`.
- **Depends-on.** G2 (the envelope this is recorded in).

---

## B. Turn captures into deliverables (deterministic, high value)

### C1 — Change diff between two crawls/analyses  ·  Effort: M
- **Goal.** The payoff of fingerprinting: given two snapshots of the same site,
  report what changed — pages added/removed, elements added/removed, controls
  renamed (same structural fingerprint, changed accessible name), components
  gained/lost. **Deterministic, no LLM.**
- **Build.** New `diff.py` + `python -m ui_discovery.diff old/ new/`. Match pages
  by normalized URL, elements by `fingerprint` (rename = same structural key,
  different name), components by signature. Emit `diff.json` + a Markdown/HTML
  diff report.
- **Acceptance.** Two hand-built crawl fixtures (v1, v2) produce a diff that
  catches an added page, a removed button, and a "Create → Add" rename.
- **Files.** new `diff.py`, `models.py` (Diff models), `reports.py`, `tests/`.
- **Depends-on.** none (fingerprints already exist).

### C2 — Playwright test skeleton export  ·  ✅ SHIPPED (0.8.0, via V5.3 `qagen`)
- **Goal.** Emit runnable Playwright test stubs from a crawl: per page, a test
  that navigates and asserts key controls (by role + accessible name, the stable
  selectors we already capture). Seeds a regression suite for the target app.
- **Build.** New `export_tests.py` + `python -m ui_discovery.export-tests
  output/<slug>/`. Generate `.spec.ts` (or `.py`) using `getByRole(name=...)`
  from each element's role/accessible_name; prefer `data-testid` when present.
  Mark destructive controls as skipped-by-default.
- **Acceptance.** Export over the fixture crawl produces syntactically valid spec
  files; a smoke test parses/compiles them; destructive controls are `.skip`.
- **Files.** new `export_tests.py`, templates, `tests/`, README.
- **Depends-on.** none (selectors already captured); pairs well with H2.

### C3 — Teach the diff what noise looks like  ·  Effort: S
- **Goal.** `C1` compares two captures faithfully, which on a real portal means
  it reports the clock in the header, the "12 unread" badge, the row count, and
  the session-scoped id in an accessible name — every time, forever. A diff that
  cries wolf on every run is a diff nobody reads, and `W1`'s scheduled captures
  will produce one nightly.
- **Build.** `diff.ignore` rules in the scope config: selectors, fingerprints,
  and accessible-name patterns whose changes are not findings. Plus one
  deterministic default the engine can apply without being told — a control
  whose accessible name differs only in digits is a **counter**, not a rename,
  and should be reported as such rather than as `Orders (12) → Orders (14)`.
  Suppressed changes are counted and summarised, never silently dropped: "38
  changes, 31 suppressed by 3 rules" keeps the rules honest.
- **Acceptance.** Two fixture captures differing only in a timestamp and a
  badge count produce an empty findings list and a non-zero suppressed count;
  a genuine rename alongside them is still reported; removing a rule brings its
  changes back, so suppression is provably reversible.
- **Files.** `diff.py`, `config.py`, `reports.py`, `tests/`.
- **Depends-on.** C1. Wanted by `W2`.

---

## F. Reusability, configurability & scoping (first-class design goals)

### R1 — Formalize the library/SDK surface  ·  Effort: S
- **Goal.** Make the engine **reusable as a library**, not just CLIs — every
  capability importable and composable.
- **Build.** Curate a stable top-level API (`from ui_discovery import
  extract_page, crawl_site, analyze_crawl, probe_page, capture_session, ...`),
  document it, and keep CLIs as *thin* wrappers over it (move any logic that
  lives in `argparse` handlers down into functions). Add a short "use as a
  library" section to the README with a code example. Semantic-version the API.
- **Acceptance.** A test imports the public API and runs extract→analyze end to
  end without touching any CLI; the CLIs call the same functions.
- **Files.** `__init__.py` (exports), the `*.py` CLIs (thin them), README, tests.
- **Depends-on.** none. Do early.

### R2 — Capability toggles (configurability)  ·  Effort: M
- **Goal.** Every capability is **switchable and tunable** from config: screenshots,
  accessibility tree, network, probe, analysis, test export, safety words,
  budgets, redaction. Same engine, different config → different target.
- **Build.** A `capabilities` block in the scope config (Pydantic model in
  `config.py`), threaded through extract/crawl/probe/analyze so each feature
  checks its toggle. Sensible defaults = today's behavior (zero-config still
  works). Flags override config; config overrides defaults.
- **Acceptance.** Disabling `screenshots`/`network`/`probe` in config verifiably
  skips that work; enabling test export runs it; defaults reproduce current runs.
- **Files.** new `config.py`, all commands, `tests/`, README.
- **Depends-on.** H5 (config file plumbing); pairs with it.

### R3 — Capability / adapter plugin seam  ·  Effort: M
- **Goal.** Let **site-specific behavior** (custom login flows, special waits,
  odd routing) register as adapters **without editing the core** — the "config/
  adapters over hacks" principle made real.
- **Build.** A small registry: named adapters implementing hooks
  (`pre_navigate`, `is_logged_in`, `should_visit`, `on_page`). Select adapters by
  name in the scope config. Ship a couple of examples; core stays generic.
- **Acceptance.** A sample adapter (e.g. a custom logged-in check) activates via
  config and changes behavior; with no adapter, behavior is unchanged.
- **Files.** new `adapters/`, `config.py`, wiring in `crawler.py`/`interactions.py`.
- **Depends-on.** R2, H5.

### S1 — Operator intake → scope config  ·  Effort: M
- **Goal.** Turn the **operator intake questionnaire** (`INTAKE_QUESTIONNAIRE.md`)
  into a validated, machine-readable **scope config** that drives a run — the
  scoping front door and the audit record of what was in scope and why.
- **Build.** `python -m ui_discovery.intake` — either interactive prompts or
  reads a filled questionnaire — and emits a validated `scope.yaml` (the schema
  from `INTAKE_QUESTIONNAIRE.md`'s sketch). All commands accept `--config
  scope.yaml`. Validate authorization/scope fields; refuse obviously out-of-scope
  runs (e.g. no start URL, or a URL matching an `exclude` pattern).
- **Acceptance.** Filling the questionnaire produces a `scope.yaml` that a crawl
  consumes to reproduce a flag-driven run; exclude patterns are honored; missing
  required fields error clearly.
- **Files.** new `intake.py`, `config.py`, all commands, `tests/`, docs.
- **Depends-on.** R2 (capabilities), H5 (config), H1 (scope/URL rules).

---

## C. V4 — Source-code correlation (optional; after the runtime engine is solid)

### V4.1 — Repo ingest + component/route/API index  ·  Effort: L
- **Goal.** Given a frontend repo (local path; GitHub later), build an index of
  components, routes, and API call sites to correlate against runtime UI.
- **Build.** New `sourcemap/` package. Parse the repo statically (framework-
  agnostic heuristics: component file names, exported symbols, route configs,
  `fetch`/axios/API-client call sites with URL literals). No execution of the
  repo. Produce a `SourceIndex` model.
- **Acceptance.** Against a small sample repo fixture, the index lists components,
  routes, and API endpoints with file+line evidence.
- **Files.** new `sourcemap/`, `models.py`, `tests/` + a tiny repo fixture.
- **Depends-on.** none.

### V4.2 — Correlate runtime → source with confidence + evidence  ·  Effort: L
- **Goal.** Link observed elements/routes/API calls to source components, each
  link carrying a confidence level (`confirmed/high/medium/low/unknown`) and its
  evidence. **Never present inference as certainty.**
- **Build.** Matchers: accessible-name ↔ component name/label literals; runtime
  route ↔ route config; observed API endpoint (from V3 network) ↔ source call
  site. Emit `correlation.json` + report, every row with evidence and confidence.
- **Acceptance.** On the sample repo + a matching crawl, a known button links to
  its component (high) and its API endpoint (high); an ambiguous case is `low`
  with evidence shown, never fabricated.
- **Files.** `sourcemap/`, `reports.py`, `tests/`.
- **Depends-on.** V4.1; benefits from V3 network (H2).

---

## D. V5 — Semantic intelligence + generation (optional; LLM; last)

**Rule for all of D (enforced — architecture principle #13).** V5 is the *only*
place AI is permitted, because it is genuine intelligence work (content, semantic
labels, QA scenarios). It is **quarantined**:
- LLM deps live **only** under the optional `[semantic]` extra
  (`pip install ui-discovery[semantic]`) — **never** in core `dependencies`.
- Off unless explicitly enabled (`--semantic` + a configured provider).
- Additive only: it never edits raw observations, never sits in the observation/
  analysis/safety path; outputs go to *separate* files.
- **The full test suite must pass with the extra NOT installed.**
  `tests/test_no_ai_runtime.py` fails the build if any of this is violated.
- Prefer the deterministic equivalents first — `C1` (change diff) and `C2` (test
  skeletons) deliver most of V5's value with **zero tokens**.

### V5.1 — Semantic element classification  ·  ✅ SHIPPED (0.6.0)
- Delivered **deterministic-first**: labels every element with zero tokens; an
  optional LLM (`--provider`) refines on top, quarantined under `[semantic]`.
  `semantic` CLI → `semantics.json/.md/.html`. This is the template for V5.2–V5.4.
- **Goal.** Label controls (primary/secondary action, navigation, filter, data
  display, destructive, informational) on top of the deterministic model.
- **Build.** An optional `semantic.py` that batches elements to an LLM and writes
  labels into a *separate* `semantics.json` keyed by fingerprint — the analysis
  model is not mutated. Deterministic fallbacks where obvious.
- **Acceptance.** Runs only with `--semantic` + provider set; without it, nothing
  changes and all tests pass. With a stub/mock provider, labels attach by
  fingerprint.
- **Depends-on.** V2 (fingerprints).

### V5.2 — Documentation generation  ·  ✅ SHIPPED (0.7.0)
- Delivered deterministic-first: `docgen` assembles a UI reference doc from
  crawl(+analysis+semantics) with zero tokens; optional `--provider` writes
  overview/purpose prose on top (shared `llm.py` seam, quarantined). User-journey
  narratives remain a future enhancement.
- **Goal.** Generate UI reference docs / page descriptions / user-journey write-
  ups from crawl + analysis (+ semantics).
- **Build.** Deterministic assembly of context → LLM draft → Markdown/HTML. Cite
  the underlying pages/elements. Off by default.
- **Depends-on.** V5.1 optional, V2.

### V5.3 — QA / test-scenario generation  ·  ✅ SHIPPED (0.8.0)
- Delivered deterministic-first: `qagen` emits scenarios + runnable Playwright
  skeletons (C2) with zero tokens; optional `--provider` adds a strategy
  narrative. Destructive controls are never automated.
- **Goal.** Propose candidate test scenarios and regression cases (natural-
  language + optionally fleshing out the C2 skeletons).
- **Depends-on.** C2 (skeletons), V5.1.

### V5.4 — LLM-assisted change narrative  ·  Effort: S
- **Goal.** Turn C1's deterministic diff into a human-readable "what changed and
  why it might matter" summary. The diff stays the source of truth.
- **Depends-on.** C1.

---

## E. Cross-cutting / infrastructure (fit in as needed)

- **X1 — `pipeline` command  ·  S.** One command runs login→crawl→analyze
  (+probe) and writes all reports, so a full capture is a single invocation.
- **X2 — CHANGELOG.md + version bumps  ·  S.** Track each phase; bump project
  version and `SCHEMA_VERSION` on schema changes.
- **X3 — CI (GitHub Actions)  ·  ✅ SHIPPED (0.16.0).** Three workflows:
  `fast` (ruff + the browser-free tests, ~60s, Python 3.11 and 3.14), `full`
  (the browser suite sharded across 6 runners), and `capture` (runs the
  pipeline against a fixture and attaches the report to the PR, so reviewing
  *whether the output improved* is a download rather than a local run).
  `full-ok` is the single required check.
- **X4 — Incremental / resumable crawl  ·  M.** Skip pages unchanged since last
  crawl (compare by fingerprint set), for large portals.
- **X5 — Politeness  ·  S.** Optional robots.txt respect + request rate limit +
  concurrency cap for real sites.
- **X6 — Storage backend seam  ·  M.** Keep JSON default, but add a thin
  interface so SQLite/Postgres can slot in later without touching the models
  (deferred until data volume demands it — do NOT add a DB server now).
- **X8 — "Which command do I run?"  ·  S.** There are now nine commands and no
  page that says which one answers which question, so the honest default is
  `pipeline` and everything cheaper goes unused. A decision table in
  `PRODUCT_GUIDE.md`: for each of `extract` `map` `crawl` `probe` `pipeline`
  `analyze` `diff` `verify` `docgen`/`qagen` — the question it answers, what it
  needs (a URL, a config, a prior capture), what it costs in wall-clock and
  pages, and what it will *not* tell you. Costed in time and pages, because
  those are what this engine actually spends. Ends with the two-line version:
  *exploring → `map` then `crawl`; documenting → `pipeline`; checking a change
  → `crawl` then `diff`.*
- **X9 — Capture profiles  ·  S.** `Capabilities` now has nine toggles and
  `ProbeSettings` several more, which is the right amount of control and the
  wrong amount of decision. An operator wants to express an *intent* —
  "reconnaissance", "the full documentation pass", "the nightly check" — not
  nine booleans. `outputs.profile: fast | standard | deep`, each a named,
  documented preset over the toggles that already exist; `standard` is exactly
  today's defaults, so nothing moves for anyone who ignores it. Explicit keys
  always beat the profile, and the manifest records the resolved set rather
  than the profile name, so a capture still says precisely what it did. Wanted
  by `W1`: a nightly run should be cheap by declaration, not by remembering
  which nine flags to turn off.

---

## G. Observability & governance — make a run accountable

A capture currently answers "what is in this product?" but not "what happened
when we looked?". `Crawl.crawl_id` exists, and every model carries versions and
timestamps — but a *pipeline run* spanning crawl→analyze→semantic→docgen→qagen
has no identity of its own, no event stream, and no record of who authorized
it. The package uses no stdlib `logging` at all: output is `print()` plus
Crawlee's logger.

These items are what turn a capture into something you can audit, trend, and
hand to someone else. All deterministic, all files-only, no service and no new
runtime dependency — principle #11 stands.

### O1 — Run identity  ·  ✅ SHIPPED (0.17.0)
- **Goal.** One id for a whole pipeline run, with `crawl_id` as its child, so
  every artifact from one invocation is provably from the same invocation.
- **Build.** Allocate `run_id` in `pipeline.py`; thread it through each stage;
  stamp it into every written model. New `run.py` owns the allocation.
- **Acceptance.** Every artifact in an output folder carries the same `run_id`;
  two runs into the same folder are distinguishable.
- **Files.** new `run.py`, `models.py`, `pipeline.py`, `crawl.py`.
- **Depends-on.** none.

### O2 — Run event stream  ·  ✅ SHIPPED (0.17.0)
- **Goal.** A greppable record of what happened during a run, in order.
- **Build.** `events.jsonl` beside the capture, one JSON object per line:
  `run.started`, `stage.started`/`stage.finished`, `page.captured`,
  `page.skipped` (budget), `probe.executed`, `probe.refused` (with the safety
  verdict), `state.captured`, `auth.rejected`, `budget.exhausted`,
  `run.finished`/`run.failed`. `pipeline._run_stage` is the natural emission
  point and already wraps every stage.
- **Acceptance.** `seq` strictly increasing; `run.started` first and a terminal
  event last; a failed stage still produces a well-formed trailing record.
- **Files.** `run.py`, `models.py` (`RunEvent`), `pipeline.py`, `crawler.py`.
- **Depends-on.** O1.

### O3 — Run manifest  ·  ✅ SHIPPED (0.17.0)
- **Goal.** One file that answers who ran this, against what, under whose
  authorization, with which settings, and how it ended.
- **Build.** `run.json` (`RunManifest`): ids, versions, target, `config_file`,
  `config_sha256` over the *resolved* scope, authorization fields, operator,
  stage records, stats rollup, artifact list, safety envelope. **Never** the
  session contents — only `auth_used` and the expiry `auth.session_status`
  already computes.
- **Acceptance.** `config_sha256` is stable across two runs of one config and
  changes when a single setting changes; no secret appears anywhere in the file.
- **Files.** `run.py`, `models.py`, `pipeline.py`, `auth.py`.
- **Depends-on.** O1.

### O4 — Stage metrics  ·  ✅ SHIPPED (0.18.0)
- **Goal.** Make "is the probe-on default too slow?" (`QA.3`) answerable from
  data rather than memory.
- **Build.** Per-stage durations and counts in the manifest, and a `metrics`
  block in `summary.md`.
- **Acceptance.** Stage durations sum to roughly wall-clock runtime.
- **Files.** `run.py`, `inventory.py`, `reports.py`.
- **Depends-on.** O3.

### O5 — Run index  ·  ✅ SHIPPED (0.18.0)
- **Goal.** "Every run against this target, and how they trend", without a
  database.
- **Build.** `runs.jsonl` appended at the output root, one line per run.
- **Acceptance.** Exactly one line per run; readable after N runs without
  loading any single large file.
- **Files.** `run.py`, `pipeline.py`.
- **Depends-on.** O3. **Deliberately not** SQLite — see `X6`.

### G1 — Authorization is enforced, not just recorded  ·  ✅ SHIPPED
- **Goal.** The scope config already carries `authorized`, `authorized_by` and
  `environment`, and `test_no_dead_config.py` classifies them as
  `DOCUMENTED_AS_METADATA` — nothing reads them. Make them mean something.
- **Build.** Refuse to run against `environment: prod` without
  `authorized: true` and a non-empty `authorized_by`; stamp all three into
  every manifest.
- **Acceptance.** A prod config without authorization exits non-zero with a
  clear message and performs no navigation; a staging config is unaffected.
- **Files.** `config.py`, `cliconfig.py`, `pipeline.py`, `crawl.py`.
- **Depends-on.** O3.

### G2 — The safety envelope on the record  ·  ✅ SHIPPED
- **Goal.** A capture should state the rules it operated under, not leave them
  to be inferred from the engine version.
- **Build.** `RunManifest.safety` (`SafetyEnvelope`): the allow-list in full,
  block/caution word counts **as they were in force** plus the additions this
  config made, `never_touch` rules, `submit_forms`, and the resolved probe
  profiles. `safety.describe_envelope()` owns the numbers, so they come from
  the gates rather than from a second reading of the config. Built in two
  steps — the rules up front, the probe profiles once the crawl has resolved
  them — so a run that dies mid-crawl still says what it would have refused.
- **Acceptance.** Two runs with different safety configs produce visibly
  different manifests. `tests/test_g2_safety_envelope.py` (18).
- **Files.** `run.py`, `safety.py`, `models.py`, `pipeline.py`.
- **Depends-on.** O3.

### G3 — Data-handling posture  ·  ✅ SHIPPED
- **Goal.** The engine redacts typed values, password fields and sensitive
  query keys. That guarantee is currently folklore; make it auditable.
- **Build.** `RunManifest.data_handling` (`DataHandling`): `never_persisted`
  (headers, bodies, the session — data that never enters the model) kept apart
  from `redactions` (data the engine sees and drops), each named with a stable
  `rule` id, where it applies and what it drops. Every rule is reported by the
  module that enforces it — `network`, `browser`, `extraction` — and the input
  types are **read out of `extract.js`** rather than mirrored, so the
  description cannot drift from the behaviour.
- **Acceptance.** The manifest names each redaction; a grep for known secret
  shapes over a whole capture returns nothing.
  `tests/test_g3_data_handling.py` (14) greps a real capture of
  `fixtures/forms/` — which plants a password and an email for the purpose —
  and separately asserts that choice-shaped values *survive*, because a capture
  that dropped everything would pass the grep and be worthless.
- **Known limitation.** Text artifacts only. A screenshot renders a typed value
  as pixels, so no grep can speak for it; `G5`/`G6` are what close that.
- **Files.** `run.py`, `browser.py`, `network.py`, `extraction.py`,
  `models.py`, `pipeline.py`.
- **Depends-on.** O3.

### G4 — Retention  ·  Effort: S
- **Goal.** Captures contain screenshots of authenticated internal screens and
  currently accumulate in Downloads forever.
- **Build.** `outputs.retention_days` in the scope config plus a `prune`
  command that deletes captures past it, reporting what it removed.
- **Acceptance.** Prune removes only expired run folders and says which;
  a dry-run mode lists without deleting.
- **Files.** `config.py`, new `prune.py`, `inventory.py`.
- **Depends-on.** O5.

### G5 — Redact the people out of the model  ·  Effort: M  ·  **P0**
- **Goal.** The engine's data-handling story has a hole in the middle of it.
  Typed values, password fields and sensitive query keys are all redacted —
  and *rendered page content is not*. On a logged-in CRM, `Element.text`,
  `accessible_name`, the ARIA snapshot and `elements.csv` carry real customer
  names, email addresses, phone numbers and account references. `G3` promises
  to state what was deliberately not persisted; right now the honest answer
  would be "less than you think".
- **Build.** Deterministic pattern detection over captured text — email,
  phone, payment-card (Luhn-checked, so a 16-digit order number is not a false
  positive), IBAN, and national-identifier shapes — plus an opt-in
  `person_names` pass driven by the operator's own supplied list, never a model.
  `privacy.redact_content` with `entities` and `replace_style`
  (`<EMAIL>` / masked / removed). Applies at capture time, before anything is
  written, so no unredacted copy ever reaches disk. **Detection is a
  deterministic classifier, not an LLM** — the same rule as `safety.py`.
- **Acceptance.** A fixture page seeded with each entity shape yields a model,
  an ARIA snapshot and a CSV containing none of them; a 16-digit order number
  that fails Luhn survives untouched; `replace_style` round-trips; disabling
  redaction is a config change that the manifest records, so a capture always
  says which posture it ran under. A grep for each seeded value across the
  whole output folder returns nothing.
- **Files.** new `redact.py`, `extraction.py`, `config.py`, `models.py`,
  `inventory.py`, `fixtures/pii/`, `tests/`.
- **Depends-on.** none. Pairs with `G3`; do it *before* `QA.2` runs again.

### G6 — Redact the people out of the screenshots  ·  Effort: M  ·  **P0**
- **Goal.** `G5` cleans the model and leaves the harder half untouched: a
  capture of an authenticated portal is mostly *pictures* of that portal, and
  a picture of a customer list is a customer list. `F6.5` component crops and
  `F6.6` revealed states multiply the copies.
- **Build.** The engine already records `geometry` for every element, so it can
  mask deterministically without reading a pixel: for each element whose text
  `G5` redacted, paint its box before the screenshot is written. Same for the
  cropped component and revealed-state captures. `privacy.redact_screenshots`,
  defaulting to whatever `redact_content` is set to — the two should not drift
  apart by accident.
- **Acceptance.** On the `G5` fixture, the boxes covering each seeded value are
  opaque in the full-page shot, in a component crop that contains them, and in
  a revealed-state capture; an element the redactor left alone is untouched;
  masking survives the crop's coordinate translation, asserted by sampling the
  pixel rather than by eye.
- **Files.** `browser.py`, `uistate.py`, `redact.py`, `tests/`.
- **Depends-on.** G5.

### G7 — Egress ledger  ·  Effort: S
- **Goal.** Principle #11 says the engine talks to nothing but the target. That
  is a design claim, and every other claim in a capture is evidenced. This one
  should be too — particularly once `M1` starts fetching sitemaps and `H7`
  starts recording links that leave the product.
- **Build.** A manifest section listing every host contacted during the run,
  with request counts and the first path that reached it. Sourced from the
  network observation `F3.4` already performs, so this is a rollup rather than
  new instrumentation. Any host outside the target's scope is flagged, loudly.
- **Acceptance.** A fixture run's ledger names exactly the fixture host; a
  fixture embedding a third-party asset lists that host and flags it; the
  ledger is present in every manifest, not only when something unusual happened.
- **Files.** `run.py`, `network.py`, `models.py`, `tests/`.
- **Depends-on.** O3.

---

## H. Discovery — know the URL surface before crawling it  ·  `EPIC-MAP`

The engine finds URLs one way: by walking what it has already rendered, plus
`D4`'s deep-nav clicking. That is thorough and slow, and it cannot see a module
the landing page never links to. Most products publish their own answer at
`/sitemap.xml` and the engine has never read it.

Two things follow. The obvious one is faster, wider coverage. The less obvious
one matters more: **you cannot currently find out what a crawl would do without
running it.** Scoping a real portal — `S1`'s whole purpose — is guesswork until
the run finishes and the budget is spent.

### M1 — Sitemap ingestion  ·  Effort: M
- **Goal.** Seed the crawl from the target's own declaration of its URLs, so
  modules that nothing links to are still captured.
- **Build.** New `discovery.py`: read `Sitemap:` directives from `/robots.txt`
  and fall back to `/sitemap.xml`; follow `<sitemapindex>` one level; handle
  `.gz`; ignore `lastmod`/`priority` for now (they are `L2`'s business). Filter
  through `ScopeRules.include/exclude` with the existing `util.path_matches` —
  a sitemap is a suggestion, not an authorization. Feed the survivors into the
  crawler's **existing `seeds` option**; do not add a second seeding path.
  Config: `discovery.sitemap: include | skip | only` (default `include`;
  `only` crawls the sitemap and follows nothing, which is the fast survey).
  Malformed XML, a 404, or a sitemap naming another host is a warning and an
  empty list, never an exception — plenty of products ship a broken one.
- **Acceptance.** A fixture serving `robots.txt` + a gzipped sitemap index +
  two child sitemaps yields every listed URL; an orphan page listed only in the
  sitemap is captured; an out-of-scope entry and a foreign-host entry are both
  dropped with a reason; `skip` reproduces today's crawl exactly; a 404 sitemap
  warns and crawls normally.
- **Files.** new `discovery.py`, `config.py`, `crawler.py`,
  `fixtures/sitemap/`, `tests/`.
- **Depends-on.** none. Note `needs-real-run`: a fixture proves the parsing, not
  what production sitemaps are actually like.

### M2 — `map` command  ·  Effort: S
- **Goal.** Answer "what would you crawl, and why?" in seconds and without
  navigating — the dry run that makes scoping a decision rather than a bet.
- **Build.** `python -m ui_discovery.map <url> [--config scope.yaml]
  [--search <glob>]` → `map.json` (a `UrlMap` model) + `urls.txt`. Every entry
  carries `url`, `source` (`seed` | `sitemap` | `link` | `deep-nav`),
  `in_scope`, and **which rule decided it** — an include pattern, an exclude
  pattern, the subdomain policy, or the budget. `--search` is a deterministic
  glob over the path; there is no relevance ranking and no scoring, because a
  ranked list nobody can reproduce is worse than an unranked one.
- **Acceptance.** Over `fixtures/site/` the map lists every URL the equivalent
  crawl captures, and names the excluding rule for each URL it does not; two
  runs produce byte-identical `urls.txt`; `--search '/admin/*'` narrows without
  changing any verdict.
- **Files.** new `map.py`, `models.py` (`UrlMap`, `MappedUrl`), `reports.py`,
  `tests/`.
- **Depends-on.** M1.

### M3 — Scope dry-run  ·  Effort: S
- **Goal.** The same answer from the command you were going to run anyway.
- **Build.** `--dry-run` on `crawl` and `pipeline`: resolve the config, build the
  map, write it, print the page count against the budget and which modules would
  be truncated — then exit zero having navigated nothing. Reuses `map.py`; no
  second implementation of scope resolution.
- **Acceptance.** `crawl --dry-run` opens no browser (assert on the absence of a
  launch, not on runtime), writes `map.json`, and reports the budget verdict; a
  config whose budget cannot reach a declared module says so by name.
- **Files.** `crawl.py`, `pipeline.py`, `map.py`, `tests/`.
- **Depends-on.** M2.

### M4 — Orphan and dead-end screens  ·  Effort: M
- **Goal.** Once `M1` supplies a URL surface and the crawl supplies a
  navigation graph, the difference between them is a finding nobody has been
  able to state: **a screen that works if you type its URL and that nothing in
  the product links to.** Those are dead routes, features shipped without an
  entry point, admin pages that outlived their menu item — exactly the things a
  product owner cannot enumerate from memory and exactly what this engine
  exists to surface. The mirror case is as useful: a screen with no outbound
  navigation at all, which is either a leaf or a trap.
- **Build.** Compute two properties over graphs that already exist: `orphan`
  (present in the sitemap or reached by `D4` deep-nav, but no navigation edge
  points at it) and `dead_end` (captured, but contributing no outbound edge).
  Both are derived, not observed — put them in the analysis output, not in
  `Page`. A section in the crawl report and a column in `urls.txt`.
- **Acceptance.** A fixture with a sitemap-only page reports exactly that page
  as an orphan and no others; a fixture page whose only links are external
  (`H7`) is a dead end and says so; a fully linked fixture reports neither, so
  the check can be trusted when it is silent. Orphan status is stable across
  two runs of an unchanged fixture.
- **Files.** `analysis/`, `models.py`, `reports.py`, `inventory.py`, `tests/`.
- **Depends-on.** M1 (the sitemap half of the surface), H7 (external edges).

---

## I. Liveness & freshness — a capture that says how current it is  ·  `EPIC-FRESH`

Every artifact carries `extracted_at`, and nothing does anything with it. Worse,
nothing distinguishes a screen that was captured from a screen that *redirected
to the login page and was captured anyway*. `Page.requested_url` and
`Page.final_url` have both existed since V0; no report has ever compared them.

The failure mode is specific and has already happened: a session expires
mid-crawl, and the capture is forty screenshots of a login form with a page
count that looks healthy. `H4` catches the run-level case. This is the
per-screen case, and it is what stands between `report.html` and `QA.4`.

### L1 — Per-page capture verdict  ·  Effort: M
- **Goal.** Every screen states whether it is the screen it claims to be, with
  the evidence for that claim.
- **Build.** A `verdict` on `Page`: `captured` | `redirected` | `auth_wall` |
  `error` | `empty` | `unknown`, alongside the evidence it was drawn from —
  requested vs final URL, HTTP status, title, element count, and whether
  `auth.py`'s logged-out heuristic fired. **Extend the existing
  `Page.auth: Optional[AuthCheck]` pattern rather than paralleling it**, and
  reuse `auth.session_status` — the logged-out reasoning is written and tested.
  `unknown` is a real verdict and must be used when the evidence is thin; a
  confident wrong verdict is worse than an honest `unknown`. Roll the counts up
  into `summary.md`, `report.html` and `run.json`.
- **Acceptance.** Against the cookie-gated auth fixture with no session, every
  page is `auth_wall` with the login URL as evidence, and the report leads with
  that rather than burying it; a fixture 302 is `redirected` with both URLs; a
  page with no elements is `empty`; the normal fixture site is entirely
  `captured`. A capture where fewer than half the screens are `captured` says so
  at the top of `summary.md`.
- **Files.** `extraction.py`, `models.py`, `auth.py` (reuse), `reports.py`,
  `inventory.py`, `tests/`.
- **Depends-on.** none. Serves `QA.4` directly.

### L2 — Capture age surfaced  ·  Effort: S
- **Goal.** A reader should never have to open JSON to find out whether they are
  looking at last Tuesday.
- **Build.** Capture timestamp and elapsed age at the head of `summary.md` and
  `report.html`. `diff` gains two warnings: the captures are more than
  `n` days apart, and either side contains non-`captured` verdicts — because
  diffing a healthy capture against a capture of the login page produces a
  spectacular and entirely fictional list of removals.
- **Acceptance.** Both reports state age in the first screenful; a diff of two
  fixture captures with divergent timestamps warns; a diff where one side is all
  `auth_wall` refuses to present its removals as findings.
- **Files.** `reports.py`, `diff.py`, `inventory.py`, `tests/`.
- **Depends-on.** L1.

### L3 — `verify` command  ·  Effort: M
- **Goal.** Ask an old capture whether it is still true, without paying for a
  new one.
- **Build.** `python -m ui_discovery.verify output/<slug>/` re-requests the
  captured URLs — no probe, no analysis, no screenshots by default — and writes
  `verify.json` + a report classifying each: still live, now redirects (with the
  destination), gone, or auth-walled. Reuses `L1`'s verdict vocabulary rather
  than inventing a second one. Honours `politeness`; a verify is still a crawl.
- **Acceptance.** Verifying a fixture capture against an unchanged fixture
  reports every URL live; against a fixture with one page removed and one moved,
  exactly those two are flagged with the right verdicts; runtime is a small
  fraction of the original crawl's, asserted as a page count rather than a
  wall-clock threshold.
- **Files.** new `verify.py`, `models.py`, `reports.py`, `tests/`.
- **Depends-on.** L1. Note `needs-real-run`.

### L4 — Revisit `X4` now that stage timings exist  ·  Effort: S  ·  **spike**
- **Goal.** `X4` (incremental crawl) was deferred because "crawl times actually
  hurt" was a judgement nobody could check. `O4` now reports per-stage
  durations, so it is a number.
- **Build.** Time-boxed investigation, not code: read `O4` metrics from real
  runs, work out what fraction of a capture is unchanged between runs, and say
  whether reuse would pay for its own complexity. Record the answer — including
  "no" — in this ROADMAP.
- **Acceptance.** A decision, with the numbers behind it, written down. If the
  answer is yes, `X4` gets a real spec; if no, its deferral gets a reason with a
  date on it rather than a shrug.
- **Files.** `ROADMAP.md`. No source changes.
- **Depends-on.** O4. Belongs to `sprint/4-deferred`.

---

## J. Reachability — reach the screens a link cannot  ·  `EPIC-INTERACT`

The probe explores: it clicks whatever the allow-list permits and records what
happens. That is the right default and it has a hard ceiling. It cannot be
*told* anything. There is no way to express "the Orders detail screen exists,
and to see it you choose *Last 90 days* in the range filter and open the first
row" — so on a real portal the most valuable screens are exactly the ones the
capture misses, because they sit behind a selection nobody made.

**This epic carries `principle-risk` and the risk is worth naming precisely.**
Principle #6 says nothing is clicked unless its type is on the allow-list *and*
its label classifies `SAFE`. A recipe is a *narrowing* of that — it says which
of the already-permitted controls to touch and in what order. It is never a
widening, and there is no config key that makes it one. A recipe step that needs
a control the safety gate refuses fails the recipe, loudly, and captures nothing.
If a design discussion ever reaches "the recipe should be able to override the
classifier", that is the point to stop and re-read `CLAUDE.md`.

### I1 — Declarative action steps  ·  Effort: M
- **Goal.** A typed, inspectable vocabulary for "do this, then this" — data, not
  a script.
- **Build.** A `Step` model in `models.py` and an executor in new `steps.py`:
  `click` | `select` | `check` | `press` | `scroll` | `wait_for` | `open_tab`.
  Targets are addressed the way the engine already describes controls — role
  plus accessible name, falling back to `data-testid` — so a recipe is written
  in the same vocabulary the reports print. **No free-text `fill`**: choice
  inputs only (select, radio, checkbox, tab), because `CONTRIBUTING.md` forbids
  persisting what someone typed and a step that types is a step that gets
  committed with a value in it. Every step goes through `safety.py` unchanged.
  A step that matches nothing, matches ambiguously, or is refused ends the
  recipe with a reason — never a silent skip and never a "best guess".
- **Acceptance.** Against `fixtures/forms/`, a four-step recipe reaches a state
  the exploratory probe does not; a step naming a destructive control is refused
  with the safety verdict recorded and the recipe abandoned; an ambiguous target
  errors naming both candidates; `test_safety.py` still passes untouched, and a
  test asserts that no recipe path can reach an interaction the allow-list
  rejects.
- **Files.** `models.py`, new `steps.py`, `interactions.py`, `tests/`.
- **Depends-on.** V6 (`F6.3` control options — a `select` step needs the options).

### I2 — Recipes in the scope config  ·  Effort: M
- **Goal.** Reachability is site-specific knowledge, so it belongs in config,
  per principle #7 — never in the core and never in a branch on a hostname.
- **Build.** A `recipes:` block, per module: a name, a start URL, and an ordered
  list of `I1` steps. After the crawler reaches the start URL it runs each
  recipe and captures the resulting screen as first-class, reusing `uistate.py`'s
  revealed-state capture and its "what opens it" provenance so the report can
  say how the screen was reached. Recipes are budgeted like everything else and
  count against `max_interactions`.
- **Acceptance.** A recipe in `examples/` opens a screen no crawl reaches
  otherwise, and it appears in `report.html` with its recipe named as the path
  to it; with the recipe removed the screen is absent, which is what makes the
  test meaningful; a recipe whose start URL is out of scope is refused at config
  load, not at run time.
- **Files.** `config.py`, `crawler.py`, `uistate.py`, `examples/`, `tests/`.
- **Depends-on.** I1.

### I3 — Recipes on the record  ·  Effort: S
- **Goal.** A directed interaction is the most consequential thing this engine
  does. It should be the best-documented, not the least.
- **Build.** `recipe.started`, `recipe.step.executed`, `recipe.step.refused`
  (carrying the safety verdict) and `recipe.finished`/`recipe.failed` into
  `events.jsonl`; a `recipes` section in `run.json` listing each recipe, its
  steps, and its outcome. Follows `O2`'s event shape exactly — no new mechanism.
- **Acceptance.** A successful recipe and a refused one are both fully
  reconstructible from `events.jsonl` alone; the manifest names every recipe
  that ran and every one that failed, with the step it failed on.
- **Files.** `run.py`, `models.py`, `pipeline.py`, `tests/`.
- **Depends-on.** I2, O2, O3.

### Explicitly not in this epic
Natural-language instructions, arbitrary script execution against the page, form
submission, and live-view streaming. The first two are refused on principle (see
§ K); the third is `safety.submit_forms`, which stays off and stays a separate
decision; the fourth needs a service and this engine does not have one.

---

## L. Watch — a capture that runs itself  ·  `EPIC-WATCH`

The engine produces a point-in-time capture, and `C1` compares two of them.
Nothing has ever run it twice. That is the difference between a tool you
remember to use and a product that tells you your UI changed — and it is a
small difference in code, because every piece is already built: `pipeline`
runs a full capture, `diff` compares two, `runs.jsonl` records the series, and
`O4` says what it cost.

**No daemon, no service, no notification integration.** Principle #11 is not
negotiable and it is not the obstacle it looks like: every environment that
would run this already has a scheduler — cron, Task Scheduler, a CI cron
trigger — and every one of them already knows how to tell someone that a job
failed. The engine's job is to be *schedulable*: run unattended, exit with a
code that means something, and leave a report worth opening. Delivering the
news is somebody else's job and they are better at it.

### W1 — Scheduled capture  ·  Effort: M
- **Goal.** One command, safe to run unattended on a timer, that captures,
  compares against the previous run, and says in its exit code whether anything
  changed.
- **Build.** `python -m ui_discovery.watch --config scope.yaml`: resolve the
  most recent prior capture from `runs.jsonl`, run the pipeline, run `C1`
  against that prior, write `watch.json` + a report, and exit `0` (no
  significant change), `1` (change found), or `2` (the run itself failed) —
  three states a scheduler can act on without parsing anything. Runs headless
  and non-interactive by construction: never prompts, never opens a browser
  window, and refuses to start rather than hanging if auth is missing (`D6`
  already knows how to tell). Document the cron / Task Scheduler / GitHub
  Actions wiring in the RUNBOOK; **ship no scheduler**.
- **Acceptance.** Two consecutive watch runs against an unchanged fixture exit
  `0` and the second names the first as its baseline; a fixture changed between
  them exits `1` and the report leads with what changed; an expired session
  exits `2` without capturing a single login screen. A watch run with no prior
  capture establishes a baseline and exits `0`, saying so.
- **Files.** new `watch.py`, `pipeline.py`, `diff.py`, `run.py`, RUNBOOK, `tests/`.
- **Depends-on.** C1, O5, D6. Wants `X9` (a cheap profile) and `H10` (a fixed
  URL set).

### W2 — What counts as a change worth waking up for  ·  Effort: M
- **Goal.** `C3` teaches the diff to ignore noise. This decides, from what
  survives, whether the run should say "something happened". Without it `W1`
  reports every night and is muted within a week.
- **Build.** `watch.significant` rules over `C1`'s output: which change *kinds*
  count (a removed control usually does; a reordered list usually does not), an
  optional threshold, and paths or modules that are exempt. Deterministic and
  inspectable — the report states which rule fired and which changes it
  dismissed, so a suppressed finding is always one command from being seen.
  **Explicitly not a model judging "meaningful change"**: a rule you can read
  is worth more than a judgement you cannot audit, and this is the same
  argument as `safety.py`.
- **Acceptance.** A fixture change matching an exempt rule exits `0` while the
  report still lists it as dismissed and names the rule; a removed control exits
  `1`; a threshold set to two suppresses a single change and reports two.
- **Files.** `watch.py`, `config.py`, `diff.py`, `reports.py`, `tests/`.
- **Depends-on.** W1, C3.

### W3 — Trend  ·  Effort: S
- **Goal.** `O5` has been appending one line per run to `runs.jsonl` since
  0.18.0 and nothing has ever read it back. Once `W1` produces a run a night,
  that file is the most interesting artifact in the output folder — screen
  count, control count, coverage and failures over time — and it is unreadable.
- **Build.** `python -m ui_discovery.trend output/` renders `runs.jsonl` as a
  table and a small inline chart per series: screens, controls, taxonomy
  coverage (`D3`), non-`captured` verdicts (`L1`), failures (`H8`), duration
  (`O4`). No new data — this is a renderer, and it must stay one.
- **Acceptance.** A synthetic `runs.jsonl` of ten runs renders every series
  with correct values; a file with one run renders without special-casing; a
  malformed line is skipped with a warning rather than failing the render,
  because a trend that cannot be read at all is worse than one with a gap.
- **Files.** new `trend.py`, `reports.py`, `tests/`.
- **Depends-on.** O5.

### W4 — What it will cost before you schedule it  ·  Effort: S
- **Goal.** "Every night" against a 4,000-page portal is a different proposition
  from "every night" against forty screens, and there is currently no way to
  find out which you have except by running it.
- **Build.** `watch --estimate`: combine `M3`'s dry-run page count with `O4`'s
  recorded per-stage durations from prior runs to project wall-clock and page
  count per scheduled run, and per week at the proposed cadence. States its
  basis — "measured over the last 3 runs" or "no prior runs; using defaults" —
  because an estimate whose provenance is hidden is a guess with a decimal point.
- **Acceptance.** With prior runs recorded the estimate is within a stated
  tolerance of the next actual run; with none it says so rather than inventing
  a number; the estimate scales with the configured budget.
- **Files.** `watch.py`, `run.py`, `map.py`, `tests/`.
- **Depends-on.** W1, M3, O4.

---

## M. Presentation variants — the same screen, more than one way  ·  `EPIC-VARIANT`

The engine captures each screen exactly once, at one viewport, in one locale,
in whatever colour scheme the browser defaults to — and then calls the result a
model of the UI. For any product built this decade that is between a third and
a half of the truth. The mobile layout is not the desktop layout with narrower
columns: it has a different navigation, different controls, and often different
features. A right-to-left locale is a different layout entirely. Dark mode is a
different set of colours that can and does fail contrast independently.

None of this needs a framework hint, a heuristic, or an LLM. Viewport, locale,
`prefers-color-scheme` and `prefers-reduced-motion` are browser-level inputs,
which makes this exactly the kind of capability principle #1 was written for.

### PV1 — Viewport and device variants  ·  Effort: M
- **Goal.** Capture each screen at the breakpoints the product actually has,
  rather than at whichever one the engine happened to open.
- **Build.** `capture.viewports`: a named list (`mobile: 390×844`,
  `tablet: 820×1180`, `desktop: 1440×900` as the shipped default set, all
  overridable), plus device-emulation flags — touch, device pixel ratio, and a
  mobile user agent — because a layout that responds to width and one that
  responds to pointer type are different things and the engine should not
  conflate them. Each variant produces its own element set and screenshot,
  keyed by variant name under one screen — **not** a separate screen, or the
  page graph doubles and every count in every report becomes a lie.
- **Acceptance.** A fixture with a `min-width` breakpoint yields different
  element sets per viewport under a single screen identity; screen counts and
  navigation edges are unchanged from a single-viewport run; a single configured
  viewport reproduces today's output byte for byte.
- **Files.** `browser.py`, `extraction.py`, `models.py`, `config.py`,
  `crawler.py`, `fixtures/responsive/`, `tests/`.
- **Depends-on.** none. Contends `models.py` heavily — land it first in its sprint.

### PV2 — Locale, colour scheme and motion  ·  Effort: M
- **Goal.** The other three browser-level inputs that change what a UI is.
- **Build.** `capture.locales` (language + timezone, so date, number and
  currency rendering are real rather than assumed — and so a right-to-left
  locale exercises the layout that actually ships to those users),
  `capture.color_schemes` (`light` / `dark` via `prefers-color-scheme`), and
  `capture.reduced_motion`. Same variant keying as `PV1`. Record the resolved
  writing direction per variant: a screen that flips to RTL is the single
  largest layout change most products contain and nothing currently notices it.
- **Acceptance.** A fixture with `prefers-color-scheme` rules yields different
  computed colours per scheme; an RTL locale is recorded as `dir=rtl` with the
  geometry to match; a fixture rendering a date renders it differently under two
  locales; unset config reproduces today's single-variant output exactly.
- **Files.** `browser.py`, `extraction.py`, `config.py`, `models.py`, `tests/`.
- **Depends-on.** PV1 (the variant keying).

### PV3 — What changes between presentations  ·  Effort: M
- **Goal.** The payoff, and the reason `PV1` and `PV2` are worth the storage:
  *state the difference*. "The mobile variant hides six navigation links behind
  a menu control." "Three actions in the desktop toolbar have no mobile
  equivalent." That is a product finding, and no amount of screenshots
  substitutes for it.
- **Build.** Reuse `C1`'s comparison machinery — the same fingerprints, run
  across variants of one screen instead of across two captures — and report
  per screen: controls present in one variant only, controls that moved region,
  and controls that changed accessible name. A variant-comparison section in the
  report, and the same data in `relations.json`.
- **Acceptance.** A fixture whose mobile layout collapses its nav reports
  exactly those links as desktop-only and names the control that reveals them;
  a fixture identical at both viewports reports no differences, so silence is
  trustworthy; the comparison reuses `diff.py` rather than reimplementing
  matching.
- **Files.** `analysis/`, `diff.py`, `reports.py`, `relations.py`, `tests/`.
- **Depends-on.** PV1, PV2, C1.

---

## N. Design vocabulary — what the product is built from  ·  `EPIC-VOCAB`

The engine models controls and misses the material they are made of. Every
element already carries geometry and attributes; what it does not carry is the
*computed* presentation — the colours, the type scale, the spacing rhythm, the
corner radii — which is the vocabulary a design system is written in and the
evidence for whether one is being followed.

This is deterministic and framework-agnostic by construction:
`getComputedStyle` is a web standard, and a colour is a colour whether it came
from Tailwind, a CSS-in-JS runtime, or a stylesheet somebody wrote in 2014.
It is also the only part of this engine that could answer a question design and
engineering ask constantly and settle by argument: *do our screens agree with
each other?*

### T1 — Design tokens from computed style  ·  Effort: M
- **Goal.** Derive the product's actual palette, type scale, spacing rhythm and
  radii from what the browser computed — not from a stylesheet, which lies
  about what is used, and not from a screenshot, which cannot be counted.
- **Build.** During extraction, collect computed values for a bounded set of
  properties (colour, background, font family/size/weight/line-height, margin,
  padding, gap, border radius, box shadow) per element. Cluster by frequency
  into a `DesignTokens` model: the palette with usage counts and where each
  colour appears, the type scale, the spacing values in use. Report the long
  tail explicitly — **eleven greys used once each is the finding**, not noise to
  be smoothed away. Contrast ratios are computed for text against its resolved
  background while the values are in hand; it is arithmetic, and skipping it
  would be a waste of the one moment the data exists.
- **Acceptance.** A fixture using three colours and two font sizes reports
  exactly those with correct counts; a fixture with a near-duplicate grey
  reports both rather than merging them; contrast ratios match a reference
  implementation on a fixture with known pairs; the collection adds no
  measurable per-page cost at the default element count.
- **Files.** `extract.js`, `extraction.py`, new `tokens.py`, `models.py`,
  `reports.py`, `tests/`.
- **Depends-on.** none. Benefits enormously from `PV2` (a dark-mode palette is a
  second palette).

### T2 — What the page says it is  ·  Effort: S
- **Goal.** The engine infers a screen's purpose from its controls and ignores
  the places the page states it outright. `<html lang>`, `<meta name=robots>`,
  Open Graph tags, and JSON-LD / microdata / RDFa are declared, structured,
  free to read, and frequently more reliable than inference.
- **Build.** Harvest and attach to `Page`: `lang`, `dir`, the meta block
  (description, robots, viewport, theme-color), Open Graph properties, and any
  JSON-LD / microdata / RDFa graph found — recorded as observed, unresolved and
  uninterpreted. A screen declaring `BreadcrumbList` hands the navigation
  analysis (`F2.4`) a fact it currently guesses at; one declaring `noindex`
  explains an orphan (`M4`) without speculation.
- **Acceptance.** A fixture carrying each markup style yields all of them
  intact; a page with none yields empty structures rather than nulls that every
  reader has to guard; malformed JSON-LD is recorded as unparsed with the raw
  text kept, never dropped and never guessed at.
- **Files.** `extract.js`, `extraction.py`, `models.py`, `reports.py`,
  `fixtures/semantics/`, `tests/`.
- **Depends-on.** none.

### T3 — Does the product agree with itself?  ·  Effort: M
- **Goal.** `F2.3` already finds that the same component appears on nine
  screens. `T1` knows what each instance is made of. The interesting question
  falls straight out and nobody can currently answer it: **do those nine
  instances agree?** A primary button that is four different greens across a
  product is a finding no design review catches, because no reviewer opens nine
  screens side by side.
- **Build.** For each component signature `F2.3` detects, compare `T1` tokens
  across instances and report drift: the property, the values, and the screens
  each was found on. Rank by how many instances disagree, because one outlier
  in nine is a bug and four-way disagreement is a missing decision.
- **Acceptance.** A fixture with one deliberately off-palette button reports
  exactly that instance with both values and both locations; a consistent
  fixture reports no drift; drift ranking is stable across runs.
- **Files.** `analysis/`, `tokens.py`, `reports.py`, `tests/`.
- **Depends-on.** T1, F2.3.

---

## K. Considered and declined

Capabilities that were reviewed against this engine and deliberately not
adopted, with the principle that decided each. This section exists so the same
proposals are not re-litigated every few months — and so that a *changed*
circumstance, rather than a fresh enthusiasm, is what reopens one.

| Capability | Declined because |
| --- | --- |
| **Hosted LLM extraction endpoints** — agentic discovery, schema-driven JSON extraction, natural-language "find me X across the web" | Principles #2 and #11. Observation must be reproducible and the runtime must need no API key. V5 is the only place AI is permitted, it is quarantined under `[semantic]`, and it never writes to the observation path. `tests/test_no_ai_runtime.py` enforces this. |
| **Natural-language browser agents** — "describe what you want and it clicks" | Principles #2 and #6. Safety here is a deterministic two-gate allow-list; an LLM deciding what to click is precisely the thing principle #6 forbids. `EPIC-INTERACT` is the deterministic answer to the same need. |
| **Arbitrary script execution against the page** (inline Playwright/JS/bash) | Principles #6 and #7. An escape hatch that can do anything is a safety envelope that guarantees nothing, and site-specific behaviour belongs in config or an `R3` adapter with a reviewable seam. |
| **Live-view streaming, outbound webhooks, hosted job API, server-side result expiry** | Principle #11. The engine is self-contained and talks to nothing but the target. `events.jsonl` (`O2`) is the local equivalent of a webhook stream and is greppable, which a webhook is not. |
| **URL discovery via search engines or a vendor-side cache** | Principle #11. `M1` takes the same idea from the only source that is legitimately ours to read: the target's own sitemap. |
| **Following links off the target domain** | The authorization boundary. `G1` exists to make authorization mean something; crawling a host nobody authorized would undo it. `H7` records the edge instead, which answers the actual question. |
| **Credit and cost accounting** | Not applicable — artifacts are local files. The cost that matters here is wall-clock and pages, which `O4` reports. Retention is `G4`. |
| **Page content as markdown / cleaned HTML for LLM consumption** | The first line of `CLAUDE.md`: this is not a scraper. Its output is a structured model of a UI — screens, controls, relationships, states — and page prose is the one thing about a product that is already easy to get. `docgen` writes documentation *from the model*, which is the opposite direction and the whole point. |
| **Document parsing — PDF, DOCX, XLSX, PPTX, EPUB, OCR** | A document is not a user interface. The engine's job is to record that a screen *offers* a download and what control does it — `D3`'s taxonomy already has `download-link` — not to open it. Parsing the file would answer a question nobody asked this engine. |
| **Audio and video extraction from pages** | Same boundary. A `<video>` is an element with controls, and the engine models it as one. |
| **Natural-language question answering over a page** | Principles #2, #11. |
| **LLM-judged "meaningful change" against a plain-language goal** | Principles #2, #11. `W2` is the deterministic answer: a rule you can read, that names itself in the report when it fires, is worth more than a judgement you cannot audit. The same argument that produced `safety.py`. |
| **Natural-language schedules** ("every weekday at 9am") | Principle #2, and cron is unambiguous, testable, and already understood by every scheduler that would run `W1`. Parsing English into a timer is a dependency and a class of bug in exchange for nothing. |
| **Proxy tiers and automatic escalation to defeat bot detection** | Out of scope by design. The engine is pointed at products you own and are authorized to test — `G1` is about to enforce that — and an authorized target does not need to be evaded. Building evasion would also make principle #11's "nothing but the target" false. |
| **Recurring web search as a monitoring target** | Principle #11 — a third-party index is an external service, and the engine's subject is one product, not the web. |
| **Webhook, email and Slack notification** | Principle #11. `W1` exits with a code that means something and writes a report; cron, Task Scheduler and CI already know how to deliver bad news, and they are better at it than anything shipped here would be. |
| **Custom HTTP request headers** | The obvious use is an auth token, and the moment that is a config key it is a config key someone commits. `login` → `--auth-state` is the supported path and it keeps the secret in a file that `.gitignore` already knows about. |
| **Appending to an existing capture** | Principle #4 — snapshots are append-only and versioned, never mutated in place. Two half-runs stitched together would have one `run_id` and two different truths in it. |
| **Ad blocking during capture** | Needs a maintained third-party blocklist, which is an external dependency for a benefit `H9`'s `exclude_selectors` already delivers under the operator's own control. |
| **Model-based PII detection** | Principle #2 for the detector itself; `G5` uses deterministic patterns with a Luhn check rather than a classifier nobody can audit. The recall is lower and the failure mode is one you can read, reproduce and fix — which is the trade this engine makes everywhere else. |

---

## Guardrails for whoever builds this

- Keep it **framework-agnostic** and **deterministic-first**. If an item tempts
  you toward a React parser or an always-on LLM, re-read `CLAUDE.md`.
- Every item ends **green**: `pytest -q` passes, README updated, sample output
  shown, limitations noted, `SCHEMA_VERSION` bumped if the schema moved.
- Prefer the smallest version that satisfies the acceptance criteria. Earn
  complexity; don't front-load it.
