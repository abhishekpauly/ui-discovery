# Architecture — UI Discovery Engine

The canonical, concise reference for **what we're building, why, and the shape of
the system**. The full narrative lives in `ui-discovery-engine-brief.md`; the
enforceable rules for contributors live in `CLAUDE.md`; this file is the
one-page north star that ties them together.

---

## Goal

Build a **reusable, framework-agnostic UI intelligence engine** that crawls a
permitted web application and produces a **structured, deterministic
representation of its UI** — pages, navigation, elements, components,
interactions, screenshots, accessibility, and network/API activity — that later
layers enrich with semantics and source-code correlation.

It is **not a scraper**. Scraping extracts text; this engine builds *knowledge
about a UI*. It must work across modern browser-rendered apps (React, Angular,
Vue, Svelte, Next, vanilla, Web Components, …) **without any framework-specific
code**, by understanding the browser and web standards rather than the framework.

## Vision

A single pipeline that turns a live application into a knowledge model and, from
it, useful artifacts:

```
Target app
  → Pages / routes            (crawl)
  → DOM + Accessibility tree   (observe)
  → UI elements + geometry + state
  → Interactions (safe)        (probe)
  → Navigation graph
  → Network / API activity
  → [later] Source-code correlation
  → UI KNOWLEDGE MODEL  (structured, versioned, append-only)
      → Docs · QA/tests · Change analysis · Training material
```

The engine collects **facts deterministically first**; interpretation and
generation (including any LLM use) sit *on top* of that evidence, never inside it.

## Why this architecture

**Foundation: Crawlee + Playwright + our own UI-intelligence layer.**

- **Crawlee = infrastructure.** Request queue, dedup, retries, concurrency,
  crawl budgets, session management, storage, URL discovery. Solved plumbing —
  we don't rebuild it.
- **Playwright = browser/runtime observation.** Real rendering, JS execution,
  DOM, accessibility snapshot, geometry, visibility, screenshots, network,
  interaction.
- **Our code = the product (the IP).** The page/component/interaction models,
  normalization, element identity/fingerprinting, the graphs, the safety model,
  the reports, and later the semantic + source-correlation layers.

Crawlee earns its keep for *discovery* (which pages exist); the *behavior* of a
page (V3 interaction) is our code driving the Playwright page directly.

## The three separated stages

`OBSERVATION → INTERPRETATION → GENERATION`, kept strictly apart so the core
stays reliable, reproducible, and debuggable:

- **Observation** (deterministic, no LLM): what the browser actually exposes.
- **Interpretation** (later, optional): semantics computed *from* observations.
- **Generation** (later, optional): docs / tests / narratives built on top.

## The three models

Not one flattened hierarchy — three conceptual graphs that may share entities:

1. **Page graph** — pages/routes and how they link (built in V1).
2. **Component graph** — UI structure within pages: regions, shared/repeated
   components (built in V2 via fingerprints).
3. **Interaction graph** — behavior: what safe interactions do and how state
   changes (started in V3).

## Architecture principles (canonical — enforced in `CLAUDE.md`)

1. **Framework-agnostic** — only browser/web-standards signals; never branch on
   the frontend framework.
2. **Deterministic core** — no LLM in the observation path; raw facts are
   reproducible.
3. **Separate observation / interpretation / generation.**
4. **Structured data is the source of truth** — Pydantic + versioned JSON;
   reports are rendered from it.
5. **Identity is first-class** — capture enough per-element signal to compute
   stable fingerprints later without re-crawling.
6. **Browser-centric; adaptive/HTTP is a late optimization** — for UI
   intelligence the browser is the default.
7. **Safe interaction by allow-list** — nothing destructive is ever clicked; two
   deterministic gates, no LLM decides safety.
8. **Append-only, versioned snapshots** — never mutate a past capture; bump the
   schema only on breaking shape changes.
9. **Config/adapters over hacks** — site-specific behavior stays out of the core.
10. **Small increments, tested every phase** — fixtures are the primary test
    surface; never ship red.
11. **Reusable by construction** — every capability is a **library function
    first, CLI second**: importable, composable, embeddable in other tools. No
    capability is CLI-only or wired to one target.
12. **Configurable, not hardcoded** — every capability is a **toggle** (on/off/
    tuned) via a per-target scope config; nothing about a specific app is baked
    into code. Sensible defaults, overridable everywhere.
13. **Runtime is AI-free and self-contained** — the engine ships and runs with
    **no LLM, no API key, no tokens, and no external service** beyond the target
    site. AI is a *detachable, opt-in enrichment* (V5 only), never a dependency
    and never in the observation / analysis / safety path. This is enforced by a
    test, not just intended.

## The AI boundary (build-time vs runtime)

Separate two things that are easy to conflate:

- **Build-time assistance** — this codebase was authored and iterated with AI
  help. That's fine and leaves no trace in the artifact.
- **Runtime dependence** — the shipped software must run entirely on its own.
  The deterministic core (V0–V4) uses **zero AI and zero tokens**; verified by a
  guard test (`tests/test_no_ai_runtime.py`) that fails if any AI library is
  imported by the core or listed as a core dependency.

**V5 is the only place AI is permitted**, because it is genuine intelligence work
(content, semantic labels, QA scenarios). It is **quarantined**: its LLM
dependencies live *only* under the optional `[semantic]` extra
(`pip install ui-discovery[semantic]`), it is off unless explicitly enabled with
a provider, its outputs go to *separate* files and never gate the deterministic
pipeline, and **the full test suite must pass with the extra not installed.**
Prefer the deterministic equivalents first — `C1` (change diff) and `C2` (test
skeletons) deliver most of V5's value with no tokens.

## Reusability, configurability & scoping

These three are first-class design goals, not afterthoughts:

- **Reusability.** The engine is a **library with CLIs on top**. Core functions
  (`extract_page`, `crawl_site`, `analyze_crawl`, `probe_page`, …) are the API;
  the `python -m ui_discovery.*` commands are thin wrappers. Anything the CLI can
  do, another program can do by import. Site-specific behavior enters through
  **capability adapters**, never edits to the core.
- **Configurability.** Every capability (screenshots, a11y tree, network, probe,
  analysis, test export, safety words, budgets, redaction) is switchable and
  tunable through a **scope config**. Same engine, different config → different
  target. Defaults make the zero-config path work; config makes it precise.
- **Scoping via operator intake.** A run begins with the **operator intake
  questionnaire** (`INTAKE_QUESTIONNAIRE.md`) — target, auth, budget, what to
  capture, the safety envelope, sensitive-data rules. Its answers *become* the
  scope config. The intake is both the scoping front door and the audit record
  of what was in scope and why.

## Tech stack

- **Language:** Python 3.10+
- **Crawling:** Crawlee (PlaywrightCrawler) · **Browser:** Playwright (Chromium)
- **Data model:** Pydantic + JSON (`schema_version`, append-only)
- **Storage (now):** local JSON files · **Reports:** JSON + Markdown + HTML
- **Auth:** Playwright `storage_state` (session reuse; no credential handling)
- **Later, earned only when needed:** SQLite/Postgres seam, LLM semantic layer,
  source-code correlation. **Deliberately NOT built yet:** LLM agents, RAG,
  vector DBs, LangChain, a DB server, framework-specific parsers, cloud/K8s.

## Where the principles live in code

| Principle | Realized in |
|---|---|
| Framework-agnostic observation | `extract.js`, `extraction.py` |
| Structured source of truth | `models.py` (`schema_version`) |
| Element identity / fingerprints | `analysis/fingerprint.py` |
| Three graphs | `crawler.py` (page), `analysis/` (component), `interactions.py` (interaction) |
| Safe interaction (two gates) | `safety.py`, `interactions.py` |
| Secrets never stored | `network.py` (redaction) |
| Reports rendered from model | `reports.py` |
| Crawlee = infra, our code = IP | `crawler.py` wraps `extraction.assemble_page()` |

---

*This is the stable reference. Detailed feature specs are in `ROADMAP.md`;
status in `PRODUCT_TRACKER.md`; history in `CHANGELOG.md`.*
