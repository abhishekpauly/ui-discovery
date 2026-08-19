# Product Tracker — UI Discovery Engine

The single source of truth for **what exists, what's in flight, and what's next**
— features, scope, priority, status, version, and test coverage. Pair it with
`CHANGELOG.md` (version history) and `ROADMAP.md` (detailed specs + acceptance
criteria). Keep this file updated as work lands (see *Maintenance* at the end).

**Current product version: `0.8.0`** · Schema `0.1.0` · **110 tests passing**

## Legend

**Status:** ✅ Shipped (merged, automated tests green) · 🧪 Ready for QA (code
complete, awaiting real-world/manual validation) · 🚧 In Progress · 📋 Planned
(spec'd in ROADMAP) · 🗄️ Backlog (nice-to-have)

**Priority:** P0 critical · P1 high · P2 medium · P3 low

**Tests:** ✔ automated coverage exists · — none yet · n/a not applicable

---

## Shipped capabilities

### V0 — Single-page extraction · v0.1.0
| ID | Capability | Scope | Pri | Status | Tests |
|----|-----------|-------|-----|--------|-------|
| F0.1 | Render + extract one page | URL → deterministic `page.json` + screenshot | P0 | ✅ | ✔ |
| F0.2 | Framework-agnostic element model | role, accessible name (+source), text, visible, enabled, geometry, attributes, dom_path, sibling ordinal, landmark | P0 | ✅ | ✔ |
| F0.3 | ARIA snapshot alongside DOM | browser accessibility tree captured with the deterministic pass | P1 | ✅ | ✔ |
| F0.4 | Robust readiness signals | DOM-loaded / networkidle / body-attached, timed; no fixed sleeps | P1 | ✅ | ✔ |
| F0.5 | Versioned Pydantic model | `schema_version` on every write | P0 | ✅ | ✔ |

### V1 — Crawl + UI Crawl Report · v0.2.0
| ID | Capability | Scope | Pri | Status | Tests |
|----|-----------|-------|-----|--------|-------|
| F1.1 | Same-domain crawl (Crawlee) | request queue, dedup, retries, concurrency | P0 | ✅ | ✔ |
| F1.2 | Crawl budgets | `--max-pages`, `--max-depth` (approximate under concurrency) | P0 | ✅ | ✔ |
| F1.3 | Page graph | depth per page + navigation edges | P1 | ✅ | ✔ |
| F1.4 | UI Crawl Report | `crawl.json` + Markdown + HTML + per-page screenshots | P0 | ✅ | ✔ |

### V2 — Analysis · v0.3.0
| ID | Capability | Scope | Pri | Status | Tests |
|----|-----------|-------|-----|--------|-------|
| F2.1 | Element fingerprinting | stable identity (testid→id→structural), generated-id resilient | P0 | ✅ | ✔ |
| F2.2 | UI region inference | grouped by accessibility landmark | P1 | ✅ | ✔ |
| F2.3 | Component detection | shared (cross-page) + repeated (within-page) | P1 | ✅ | ✔ |
| F2.4 | Navigation-menu extraction | nav landmarks + breadcrumb flag | P2 | ✅ | ✔ |
| F2.5 | Analysis reports | `analysis.json` / `.md` / `.html`, append-only | P1 | ✅ | ✔ |

### V3 — Safe interaction + network · v0.4.0
| ID | Capability | Scope | Pri | Status | Tests |
|----|-----------|-------|-----|--------|-------|
| F3.1 | Two-gate safety model | allow-list types + SAFE/CAUTION/BLOCK labels; deterministic | P0 | ✅ | ✔ |
| F3.2 | Safe interaction execution | clicks only reversible controls, records before/after, reverts | P0 | ✅ | ✔ |
| F3.3 | Navigation recovery | route change detected → `go_back` restores page | P1 | ✅ | ✔ |
| F3.4 | Network observation | method/url/status, secrets redacted, endpoint `:id`, GraphQL/API detect | P1 | ✅ | ✔ |
| F3.5 | Probe reports | `probe.json` / `.md` / `.html` | P2 | ✅ | ✔ |

### Auth + quality · v0.4.1 / v0.5.0
| ID | Capability | Scope | Pri | Status | Tests |
|----|-----------|-------|-----|--------|-------|
| FA.1 | Session capture (`login`) | log in by hand → save `storage_state` | P0 | ✅ | n/a* |
| FA.2 | `--auth-state` on extract/crawl/probe | reuse logged-in session everywhere | P0 | ✅ | ✔ |
| FA.3 | Edge-case hardening | empty/malformed/hidden/unicode/deep/broken-link inputs | P1 | ✅ | ✔ |
| FQ.1 | Fixture-first test suite | 88 tests, localhost server helper, no external deps | P0 | ✅ | ✔ |
| FQ.2 | AI-free runtime guarantee | no LLM/API-key/tokens in core; enforced by guard test; `[semantic]` quarantine for V5 | P0 | ✅ | ✔ |
| FQ.3 | Self-contained crawl | tldextract pinned offline; no network beyond the target site | P1 | ✅ | ✔ |

\* `login` opens a visible browser for manual sign-in — validated by use, not by a headless test.

### Needs real-world validation
| ID | Capability | Scope | Pri | Status | Tests |
|----|-----------|-------|-----|--------|-------|
| QA.1 | Run on a real **public** site | crawl/analyze/probe a live external app from the user's machine | P0 | 🧪 | — |
| QA.2 | Run on a real **authenticated** portal | `login` → `--auth-state` against an actual SSO/login portal | P0 | 🧪 | — |

---

## Planned — see `ROADMAP.md` for full specs & acceptance criteria

### Infrastructure baseline
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| X0 | Git baseline + branch-per-feature | commit current green state; one branch per item | P0 | 📋 |

### Hardening (make real portals crawl cleanly)
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| H1 | Query-string & SPA route normalization | dedupe `?x=1`/`?x=2`; hash/history route identity | P0 | 📋 |
| H2 | Probe-in-crawl (authenticated) | run safe probe on every crawled page as logged-in user | P0 | 📋 |
| H3 | Shadow DOM & iframe traversal | extract inside open shadow roots + same-origin frames | P1 | 📋 |
| H4 | Session-expiry detection | warn/abort when a saved session is stale, not silently crawl login | P1 | 📋 |
| H5 | Config file + capability adapters | per-site YAML: budgets, URL patterns, auth signals | P2 | 📋 |

### Deliverables (deterministic, high value)
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| C1 | Change diff between two crawls | pages/elements/components added/removed/renamed (by fingerprint) | P1 | 📋 |
| C2 | Playwright test-skeleton export | runnable `.py`/`.spec.ts` stubs from captured selectors; destructive skipped | P1 | ✅ (via V5.3) |

### Reusability / configurability / scoping (first-class goals)
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| R1 | Library/SDK surface | every capability importable & composable; CLIs are thin wrappers | P1 | 📋 |
| R2 | Capability toggles | each feature switchable/tunable via config; defaults = today's behavior | P1 | 📋 |
| R3 | Capability/adapter plugin seam | site-specific behavior registers without editing core | P2 | 📋 |
| S1 | Operator intake → scope config | questionnaire → validated `scope.yaml`; scoping front door + audit record | P1 | 📋 |
| DOC | Operator intake questionnaire | `INTAKE_QUESTIONNAIRE.md` template (use by hand until S1 ships) | P1 | ✅ |

### V4 — Source-code correlation (optional)
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| V4.1 | Repo ingest + component/route/API index | static parse of a frontend repo (no execution) | P2 | 📋 |
| V4.2 | Runtime→source correlation | link elements/routes/APIs to components, confidence + evidence | P2 | 📋 |

### V5 — Semantic / LLM layer (optional, opt-in, additive)
| ID | Feature | Scope | Pri | Status | Tests |
|----|---------|-------|-----|--------|-------|
| V5.1 | Semantic element classification | labels by fingerprint; **deterministic default (0 tokens)** + optional LLM refine (quarantined `[semantic]`) | P2 | ✅ | ✔ |
| V5.2 | Documentation generation | UI reference doc (overview, per-page, controls-by-role); **deterministic default** + optional LLM prose | P2 | ✅ | ✔ |
| V5.3 | QA / test-scenario generation | scenarios (smoke/nav/form/guard) + Playwright skeletons; **deterministic default** + optional LLM strategy | P3 | ✅ | ✔ |
| V5.4 | LLM change narrative | human summary over C1's deterministic diff | P3 | 📋 | — |

### Cross-cutting / infra
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| X1 | `pipeline` command | one-shot login→crawl→analyze(+probe)→reports | P2 | 📋 |
| X2 | CHANGELOG + version discipline | bump product/schema versions per release | P1 | 🚧 |
| X3 | CI (GitHub Actions) | run pytest + playwright install on push | P2 | 📋 |
| X4 | Incremental / resumable crawl | skip pages unchanged since last crawl | P2 | 🗄️ |
| X5 | Politeness | robots.txt + rate limit + concurrency cap | P2 | 🗄️ |
| X6 | Storage backend seam | interface so SQLite/Postgres can slot in later (no DB now) | P3 | 🗄️ |

---

## Maintenance (for whoever edits the code — including Claude Code)

When an item lands, in the **same change**:
1. Flip its **Status** here (📋 → 🚧 → ✅), and note the version it shipped in.
2. Add a **CHANGELOG.md** entry under the new version (Added/Changed/Fixed +
   test delta), and bump `pyproject.toml` `version` + `__version__`.
3. Bump `SCHEMA_VERSION` **only** if the JSON model shape changed in a
   breaking way; otherwise leave it.
4. Keep `pytest -q` green and update the **Tests** cell (✔) with new coverage.

Status meanings for QA flow: mark **🧪 Ready for QA** when code + automated tests
are done but real-world validation is pending; move to **✅ Shipped** once it's
validated (a real run, or a human sign-off). Keep P0/P1 rows at the top of each
table.
