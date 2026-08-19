# CLAUDE.md — working context for the UI Discovery Engine

Read this first. It is the standing contract for how to extend this codebase.
Goal, vision & architecture: `ARCHITECTURE.md` (canonical) and
`ui-discovery-engine-brief.md` (full narrative). Feature backlog: `ROADMAP.md`.
Live status: `PRODUCT_TRACKER.md`. History: `CHANGELOG.md`. Testing: `QA_REPORT.md`.

## What this is

A **framework-agnostic UI intelligence engine**. It renders a permitted web app
in a real browser (Playwright), crawls it (Crawlee), and produces a structured,
deterministic model of its UI — pages, elements, components, interactions,
network — that later layers enrich. It is **not a scraper**; it builds knowledge
about a UI.

Built so far: **V0** single-page extractor · **V1** Crawlee crawler + UI Crawl
Report · **V2** analysis (fingerprints, regions, components, navigation) · **V3**
safe interaction + network probe · **session-based auth** for logged-in portals.
**84 tests pass.**

## Non-negotiable principles (do not violate these when adding features)

1. **Framework-agnostic.** Never branch on `if react / angular / vue`. Only use
   browser/web-standards signals: DOM, ARIA roles, accessible names, landmarks,
   geometry, visibility, URL/DOM changes, network. If you're tempted to detect a
   framework, stop.
2. **Deterministic core; no LLM in the observation path.** Observation →
   interpretation → generation are separate stages. Raw facts are reproducible.
   Any LLM feature is optional, additive, and never overwrites raw observations.
3. **Structured data is the source of truth.** Pydantic models in `models.py`,
   serialized to JSON with `schema_version`. Markdown/HTML reports are *rendered
   from* the model, never the reverse.
4. **Append-only, versioned snapshots.** Bump `SCHEMA_VERSION` in `__init__.py`
   when the schema changes; never mutate an existing crawl/analysis in place.
5. **Identity is first-class.** Elements carry a generous signal set so stable
   fingerprints can be *computed* later without re-crawling. Preserve those
   signals; don't drop them.
6. **Safe interaction by allow-list.** Nothing is clicked unless its interaction
   *type* is on `safety.ALLOW_LIST` AND its label classifies `SAFE`. The word
   classifier is a second gate, never the only one. No LLM decides safety.
7. **Config/adapters over hacks.** Site-specific behavior belongs in config or
   capability modules, never sprinkled into the core.
8. **Small increments, tested every phase.** Each feature ships with tests and a
   README update. Don't advance while tests are red.
9. **Reusable by construction.** Every capability is a library function first,
   CLI second — importable and composable. CLIs are thin wrappers; never put
   logic only reachable through `argparse`.
10. **Configurable, not hardcoded.** Every capability is a toggle via the scope
    config; nothing about a specific target is baked into code. New capability →
    a config flag + a sensible default. Scoping starts from
    `INTAKE_QUESTIONNAIRE.md`.
11. **Runtime is AI-free and self-contained.** The engine runs with no LLM, no
    API key, no tokens, no external service beyond the target. Build-time AI
    assistance is fine; runtime dependence is forbidden. AI is allowed **only**
    in V5, and **only** quarantined: LLM deps live under the optional
    `[semantic]` extra, off by default, outputs to separate files, never in the
    observation/analysis/safety path, and the suite passes with the extra NOT
    installed. `tests/test_no_ai_runtime.py` enforces this — keep it green.
    Never add an AI/LLM library to the core `dependencies` in `pyproject.toml`.

## Repo map

```
src/ui_discovery/
  extract.py extraction.py browser.py extract.js  # V0 single-page extractor
  crawl.py crawler.py                              # V1 Crawlee crawler
  models.py                                        # ALL Pydantic models (+ schema_version)
  util.py                                          # slug, URL normalize, same-site, BFS
  reports.py                                       # ALL Markdown/HTML renderers
  analyze.py analysis/                             # V2 fingerprint/regions/components/nav
  probe.py interactions.py safety.py network.py    # V3 interaction + network
  login.py auth.py                                 # session-based auth
fixtures/            # local HTML — the PRIMARY test surface
fixtures/site/       # multi-page linked site (crawl tests)
fixtures/interactive/# tabs/menu/dialog + fetch (probe tests)
fixtures/edge/       # adversarial inputs (edge tests)
tests/               # pytest; conftest.py has a localhost server helper
```

## Commands

```bash
# setup (once)
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m playwright install chromium

# ALWAYS run before and after a change — keep it green
pytest -q

# the CLIs
python -m ui_discovery.extract  <url> [--auth-state session.json]
python -m ui_discovery.crawl    <url> [--max-pages N --max-depth N --auth-state session.json]
python -m ui_discovery.analyze  output/<slug>/
python -m ui_discovery.probe    <url> [--auth-state session.json]
python -m ui_discovery.login    <login-url> --output session.json   # run locally, headed
```

## Conventions

- **Add a model, don't overload one.** New output → a new Pydantic model in
  `models.py` with `schema_version`. New report → a renderer in `reports.py`.
- **Tests use fixtures over live sites.** Serve `fixtures/**` over the localhost
  helper in `tests/conftest.py`. Never make the suite depend on an external site.
- **Sync vs async.** V0/V3 use sync Playwright; V1 crawler is async (Crawlee).
  The pure model-builder `extraction.assemble_page()` is shared by both — reuse
  it, don't duplicate logic.
- **Chromium under root/CI needs `--no-sandbox`** (already applied at every
  launch). Keep it.
- **Never persist secrets.** No request/response headers or bodies in models;
  redact sensitive query values (`network.redact_url`).
- **Pin new deps** in `pyproject.toml` and record the version. Avoid heavy deps
  (LLM SDKs, databases, LangChain, vector stores) unless a ROADMAP item calls
  for it and it's optional.

## Definition of done for any feature

Working code · tests (fixture-based) · `pytest -q` green · README section ·
sample output · known limitations noted. "It compiles" is not done; "I can run
it and see useful output" is.

**Also update the tracking docs in the same change:**
- `PRODUCT_TRACKER.md` — flip the item's status (📋 → 🚧 → ✅) and its Tests cell.
- `CHANGELOG.md` — add an entry under a new version (Added/Changed/Fixed + test
  delta); bump `pyproject.toml` `version` and `__version__` (product version).
- Bump `SCHEMA_VERSION` in `__init__.py` **only** if the JSON model shape changed
  in a way that breaks readers of old snapshots (additive fields don't count).

## How to work

Pick one ROADMAP item at a time, in the recommended order. State a short plan,
implement the smallest viable version, add tests, run `pytest -q`, update the
README, then stop and summarize. Do not batch several phases silently.
