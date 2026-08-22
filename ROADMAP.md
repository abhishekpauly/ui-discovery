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

Since shipped, the order continues: **`X3` (CI) → `O1…O5` (observability, complete at 0.18.0) → `G1…G4` (governance)**. `X3` was blocked on a git remote existing; once it does, CI is what makes every later item cheap to verify.

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

## Guardrails for whoever builds this

- Keep it **framework-agnostic** and **deterministic-first**. If an item tempts
  you toward a React parser or an always-on LLM, re-read `CLAUDE.md`.
- Every item ends **green**: `pytest -q` passes, README updated, sample output
  shown, limitations noted, `SCHEMA_VERSION` bumped if the schema moved.
- Prefer the smallest version that satisfies the acceptance criteria. Earn
  complexity; don't front-load it.
