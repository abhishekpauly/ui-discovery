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
at 0.18.0) → `G1…G4` (governance) → `M1…M3` + `H6…H8` (discovery) → `L1…L3`
(liveness) → `I1…I3` (reachability)**. `X3` was blocked on a git remote existing;
once it does, CI is what makes every later item cheap to verify.

The last three groups arrived together from a capability review and are sequenced
by file contention rather than by value — `models.py`, `config.py`, `crawler.py`
and `reports.py` are contended across all of them, so they run as consecutive
sprints, not parallel ones (`BRANCHING.md` § *Running sprints in parallel*).
Discovery goes first because `M2`'s dry run makes scoping every later run cheap;
liveness next because `L1` is what `QA.4` is waiting on; reachability last
because it is the largest and benefits from the seeds `M1` provides.

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

### G1 — Authorization is enforced, not just recorded  ·  Effort: S
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

### G2 — The safety envelope on the record  ·  Effort: S
- **Goal.** A capture should state the rules it operated under, not leave them
  to be inferred from the engine version.
- **Build.** Manifest section: allow-list, block/caution word counts,
  `never_touch` rules, and the resolved probe profiles
  (`CrawlConfig.probe_profiles` already carries the last).
- **Acceptance.** Two runs with different safety configs produce visibly
  different manifests.
- **Files.** `run.py`, `safety.py`, `models.py`.
- **Depends-on.** O3.

### G3 — Data-handling posture  ·  Effort: S
- **Goal.** The engine redacts typed values, password fields and sensitive
  query keys. That guarantee is currently folklore; make it auditable.
- **Build.** A manifest section recording what was deliberately not persisted,
  and the redaction rules in force.
- **Acceptance.** The manifest names each redaction; a grep for known secret
  shapes over a whole capture returns nothing.
- **Files.** `run.py`, `browser.py`, `network.py`.
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

---

## Guardrails for whoever builds this

- Keep it **framework-agnostic** and **deterministic-first**. If an item tempts
  you toward a React parser or an always-on LLM, re-read `CLAUDE.md`.
- Every item ends **green**: `pytest -q` passes, README updated, sample output
  shown, limitations noted, `SCHEMA_VERSION` bumped if the schema moved.
- Prefer the smallest version that satisfies the acceptance criteria. Earn
  complexity; don't front-load it.
