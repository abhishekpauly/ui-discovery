# Product Tracker — UI Discovery Engine

The single source of truth for **what exists, what's in flight, and what's next**
— features, scope, priority, status, version, and test coverage. Pair it with
`CHANGELOG.md` (version history) and `ROADMAP.md` (detailed specs + acceptance
criteria). Keep this file updated as work lands (see *Maintenance* at the end).

**Current product version: `0.19.0`** · Schema `0.1.0` · **703 tests** — 700 passing, 3 skipped

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

### V6 — Relationships, controls & visual capture · v0.15.0
| ID | Capability | Scope | Pri | Status | Tests |
|----|-----------|-------|-----|--------|-------|
| F6.1 | Labelled screen graph | every navigation edge carries the control label, region and kind that reaches it | P0 | ✅ | ✔ |
| F6.2 | Element relationships | containment (`parent_path`), `aria-controls`, form ownership — computed, per screen | P0 | ✅ | ✔ |
| F6.3 | Control options & state | select/listbox/radiogroup/menu/tablist options + selected; checked/required/expanded/sorted from DOM properties | P0 | ✅ | ✔ |
| F6.4 | Forms & tables as data | fields with type/required/options/default/help; radios merged into one choice; table columns + row actions | P0 | ✅ | ✔ |
| F6.5 | Component screenshots | forms, dialogs, tab panels, tables, labelled regions cropped to themselves | P1 | ✅ | ✔ |
| F6.6 | Revealed-state capture | modal/drawer/menu/tab-panel/disclosure opened by the probe, photographed, with what opens it | P0 | ✅ | ✔ |
| F6.7 | Readable crawl report | site map, screen-connection table, per-screen walkthrough; HTML with TOC + dark mode | P0 | ✅ | ✔ |
| F6.8 | Per-module / per-tab probe config | `ProbeSettings`, longest-prefix module matching, `--no-probe` family | P0 | ✅ | ✔ |
| F6.9 | `relations.json` + `controls.csv` | relationships and clickables as plain data, every run | P1 | ✅ | ✔ |
| F6.10 | Typed-value redaction | password/free-text values kept out of `attributes` **and** the ARIA snapshot | P0 | ✅ | ✔ |

---

### Needs real-world validation
| ID | Capability | Scope | Pri | Status | Tests |
|----|-----------|-------|-----|--------|-------|
| QA.1 | Run on a real **public** site | crawl/analyze/probe a live external app from the user's machine | P0 | 🧪 | — |
| QA.2 | Run on a real **authenticated** portal | `login` → `--auth-state` against an actual SSO/login portal | P0 | 🧪 | — |
| QA.3 | Probe-on-by-default runtime | confirm a real portal still captures in acceptable time now that every crawl interacts — `O4` now reports the probe's share of the crawl, so this is a number to read rather than a judgement to make | P0 | 🧪 | ✔ |
| QA.4 | Report reviewed as product documentation | someone unfamiliar with the portal reads `report.html` and can describe it | P0 | 🧪 | — |

---

## Planned — see `ROADMAP.md` for full specs & acceptance criteria

### Observability — make a run accountable · EPIC-OBS
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| O1 | Run identity | one `run_id` per pipeline run; `crawl_id` becomes its child | P0 | ✅ |
| O2 | Run event stream | `events.jsonl` — stages, pages, probes, refusals, budget, auth | P0 | ✅ |
| O3 | Run manifest | `run.json` — who/what/when/config hash/versions/outcome | P0 | ✅ |
| O4 | Stage metrics | per-stage durations + counts; answers `QA.3` from data | P1 | ✅ |
| O5 | Run index | `runs.jsonl` at the output root, one line per run | P2 | ✅ |

### Governance — state the rules a capture ran under · EPIC-GOV
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| G1 | Authorization enforced | `authorized` / `environment` stop being inert metadata — a `prod` config without `authorized: true` + `authorized_by` exits 3 before anything opens | P0 | ✅ |
| G2 | Safety envelope recorded | allow-list in full, word counts *in force* + this config's additions, `never_touch`, `submit_forms`, resolved probe profiles | P1 | ✅ |
| G3 | Data-handling posture | `never_persisted` vs `redactions`, each rule named by the module that enforces it; value types read from `extract.js`, not mirrored | P1 | ✅ |
| G4 | Retention | `outputs.retention_days` (default off) + `prune`; lists by default, `--delete` to act; only folders with `run.json`, aged from the manifest | P2 | ✅ |
| G5 | Redact the people out of the model | EMAIL/PHONE/CARD(Luhn)/IBAN(mod-97)/NATIONAL_ID + operator name list; at capture time, page model **and** probe record; off by default, posture recorded either way | P0 | ✅ |
| G6 | Redact the people out of the screenshots | mask element boxes using geometry already recorded; crops and revealed states too | P0 | ✅ |
| G7 | Egress ledger | every host contacted during a run, in the manifest; off-scope hosts flagged | P1 | ✅ |

### Discovery — know the URL surface before crawling it · EPIC-MAP
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| M1 | Sitemap ingestion | `robots.txt` + `sitemap.xml` (+ index, `.gz`) seed the crawl; scope rules still decide | P1 | 📋 |
| M2 | `map` command | `map.json` + `urls.txt`: every URL with its source, verdict, and the rule that decided it | P1 | 📋 |
| M3 | Scope dry-run | `--dry-run` on `crawl`/`pipeline` — the map and the budget verdict, navigating nothing | P2 | 📋 |
| M4 | Orphan & dead-end screens | reachable by URL but unlinked; captured but leading nowhere | P1 | 📋 |

### Liveness & freshness — a capture that says how current it is · EPIC-FRESH
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| L1 | Per-page capture verdict | `captured`/`redirected`/`auth_wall`/`error`/`empty`/`unknown` + evidence | P0 | 📋 |
| L2 | Capture age surfaced | timestamp + age lead the reports; `diff` warns on stale or unhealthy sides | P1 | 📋 |
| L3 | `verify` command | re-check a prior capture cheaply: live / redirected / gone / auth-walled | P2 | 📋 |
| L4 | Revisit `X4` with `O4` metrics | **spike** — does capture reuse pay? Deliverable is a recorded answer | P3 | 📋 |

### Reachability — reach the screens a link cannot · EPIC-INTERACT
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| I1 | Declarative action steps | typed `click`/`select`/`check`/`press`/`scroll`/`wait_for`/`open_tab`; choice inputs only; `safety.py` unchanged | P1 | 📋 |
| I2 | Recipes in the scope config | named step sequences per module; resulting screen captured as first-class | P1 | 📋 |
| I3 | Recipes on the record | `recipe.*` events + a `recipes` section in `run.json`, refusals included | P1 | 📋 |

### Watch — a capture that runs itself · EPIC-WATCH
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| W1 | Scheduled capture | `watch` command: capture, diff against the last run, exit 0/1/2. No daemon, no notifications | P1 | 📋 |
| W2 | Change significance rules | deterministic rules deciding what is worth waking up for; every suppression named | P1 | 📋 |
| W3 | Trend | `runs.jsonl` has been written since 0.18.0 and never read back — render it | P2 | 📋 |
| W4 | Run estimate | wall-clock and pages per scheduled run, from `M3`'s dry run and `O4`'s measurements | P3 | 📋 |

### Presentation variants — the same screen, more than one way · EPIC-VARIANT
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| PV1 | Viewport & device variants | capture per breakpoint under one screen identity, not as separate screens | P1 | 📋 |
| PV2 | Locale, colour scheme & motion | `prefers-color-scheme`, `prefers-reduced-motion`, language + timezone, RTL direction | P2 | 📋 |
| PV3 | What changes between presentations | the payoff: which controls exist in one variant only, and what reveals them | P1 | 📋 |

### Design vocabulary — what the product is built from · EPIC-VOCAB
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| T1 | Design tokens from computed style | palette, type scale, spacing, radii with usage counts; contrast ratios while the data is there | P2 | 📋 |
| T2 | What the page says it is | `lang`/`dir`, meta, Open Graph, JSON-LD / microdata / RDFa — recorded as observed | P2 | 📋 |
| T3 | Does the product agree with itself? | token drift across instances of one component — the finding no design review catches | P3 | 📋 |


### Infrastructure baseline
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| X0 | Git baseline + branch-per-feature | commit current green state; one branch per item | P0 | ✅ 0.12.0 |

### Hardening (make real portals crawl cleanly)
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| H1 | Query-string & SPA route normalization | dedupe `?x=1`/`?x=2`; hash/history route identity | P0 | ✅ 0.12.0 |
| H2 | Probe-in-crawl (authenticated) | run safe probe on every crawled page as logged-in user | P0 | ✅ 0.12.0 |
| H3 | Shadow DOM & iframe traversal | extract inside open shadow roots + same-origin frames | P1 | ✅ 0.12.0 |
| H4 | Session-expiry detection | warn/abort when a saved session is stale, not silently crawl login | P1 | ✅ 0.12.0 |
| H5 | Config file + capability adapters | per-site YAML: budgets, URL patterns, auth signals | P2 | ✅ 0.12.0 |
| H6 | Subdomain policy | `same-host` (today) / `registrable-domain` / explicit list | P2 | 📋 |
| H7 | External links recorded, never followed | the authorization boundary as a visible edge, not a silence | P2 | 📋 |
| H8 | Crawl failure ledger | what was *not* captured, with reasons — a rollup of `discovered_not_captured` + `page.skipped` | P2 | 📋 |
| H9 | Exclude the furniture | `capture.exclude_selectors` — cookie banners and chat widgets out of the model, counted not silent | P2 | 📋 |
| H10 | Capture an explicit URL list | `--from urls.txt` / `urls:` — scope rules still apply; a list is not an authorization | P2 | 📋 |
| H11 | TLS verification as a recorded decision | reach internal staging, and say in the manifest that you did | P3 | 📋 |

### Deliverables (deterministic, high value)
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| D1 | Run artifacts on every crawl | `summary.md`, `urls.txt`, `elements.csv`, `endpoints.md` | P1 | ✅ 0.13.0 |
| D2 | Downloads + module-wise layout | product folder; one self-contained folder per module | P1 | ✅ 0.13.0 |
| D3 | UI type taxonomy + coverage | 64 types; found / absent / not-detectable | P1 | ✅ 0.13.0 |
| D4 | Deep navigation discovery | reach routes the app never marked up as links | P0 | ✅ 0.13.0 |
| D5 | Held-open connection detection | websocket/SSE apps settle correctly without a hand-written adapter | P0 | ✅ 0.14.0 |
| D6 | Session pre-flight | read a saved session's expiry before crawling, not after | P1 | ✅ 0.14.0 |
| C1 | Change diff between two crawls | pages/elements/components added/removed/renamed (by fingerprint) | P1 | ✅ 0.12.0 |
| C2 | Playwright test-skeleton export | runnable `.py`/`.spec.ts` stubs from captured selectors; destructive skipped | P1 | ✅ (via V5.3) |
| C3 | Diff noise suppression | clocks, badge counts and session ids stop being findings; suppressions counted and reversible | P1 | 📋 |

### Reusability / configurability / scoping (first-class goals)
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| R1 | Library/SDK surface | every capability importable & composable; CLIs are thin wrappers | P1 | ✅ 0.12.0 |
| R2 | Capability toggles | each feature switchable/tunable via config; defaults = today's behavior | P1 | ✅ 0.12.0 |
| R3 | Capability/adapter plugin seam | site-specific behavior registers without editing core | P2 | ✅ 0.12.0 |
| S1 | Operator intake → scope config | questionnaire → validated `scope.yaml`; scoping front door + audit record | P1 | ✅ 0.12.0 |
| DOC | Operator intake questionnaire | `INTAKE_QUESTIONNAIRE.md` template (use by hand until S1 ships) | P1 | ✅ |

### V4 — Source-code correlation (optional)
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| V4.1 | Repo ingest + component/route/API index | static parse of a frontend repo (no execution) | P2 | ✅ 0.12.0 |
| V4.2 | Runtime→source correlation | link elements/routes/APIs to components, confidence + evidence | P2 | ✅ 0.12.0 |

### V5 — Semantic / LLM layer (optional, opt-in, additive)
| ID | Feature | Scope | Pri | Status | Tests |
|----|---------|-------|-----|--------|-------|
| V5.1 | Semantic element classification | labels by fingerprint; **deterministic default (0 tokens)** + optional LLM refine (quarantined `[semantic]`) | P2 | ✅ | ✔ |
| V5.2 | Documentation generation | UI reference doc (overview, per-page, controls-by-role); **deterministic default** + optional LLM prose | P2 | ✅ | ✔ |
| V5.3 | QA / test-scenario generation | scenarios (smoke/nav/form/guard) + Playwright skeletons; **deterministic default** + optional LLM strategy | P3 | ✅ | ✔ |
| V5.4 | LLM change narrative | human summary over C1's deterministic diff | P3 | ✅ 0.12.0 | — |

### Cross-cutting / infra
| ID | Feature | Scope | Pri | Status |
|----|---------|-------|-----|--------|
| X1 | `pipeline` command | one-shot login→crawl→analyze(+probe)→reports | P2 | ✅ 0.12.0 |
| X2 | CHANGELOG + version discipline | bump product/schema versions per release | P1 | ✅ 0.13.0 |
| X3 | CI (GitHub Actions) | `fast` lane (~60s), `full` suite sharded 6-way, `capture` attaches the report to every PR | P2 | ✅ |
| X4 | Incremental / resumable crawl | skip pages unchanged since last crawl — speculative until crawl times actually hurt | P2 | 🗄️ deferred |
| X5 | Politeness | robots.txt + rate limit + concurrency cap | P2 | ✅ 0.12.0 |
| X6 | Storage backend seam | interface so SQLite/Postgres can slot in later (no DB now) — deferred by ROADMAP until data volume demands it | P3 | 🗄️ deferred |
| X7 | Repo governance | branching model (`BRANCHING.md`), release process + `release` workflow (`RELEASING.md`), labels as data (`.github/labels.yml`), retroactive tags `v0.12.0`–`v0.18.0`, project board | P2 | 🚧 |
| X8 | "Which command do I run?" | decision table over the nine commands, costed in wall-clock and pages | P2 | 📋 |
| X9 | Capture profiles | `fast` / `standard` / `deep` presets over the toggles; `standard` is today's defaults | P2 | 📋 |

---

## Maintenance (for whoever edits the code — including Claude Code)

When an item lands, in the **same change**:
1. Flip its **Status** here (📋 → 🚧 → ✅), and note the version it shipped in.
2. Add a **CHANGELOG.md** entry under the new version (Added/Changed/Fixed +
   test delta), and bump `pyproject.toml` `version` + `__version__`.
3. Bump `SCHEMA_VERSION` **only** if the JSON model shape changed in a
   breaking way; otherwise leave it.
4. Keep `pytest -q` green and update the **Tests** cell (✔) with new coverage.
5. Quote the **collected** total above, with the pass/skip split beside it.
   Passing-count alone drifts for reasons that have nothing to do with the
   tests: `test_release_hygiene.py` skips its drift check when no `src/`
   commit has landed since the last version bump, so the same suite reports
   594/7 immediately after a release and 595/6 a few commits later.

Status meanings for QA flow: mark **🧪 Ready for QA** when code + automated tests
are done but real-world validation is pending; move to **✅ Shipped** once it's
validated (a real run, or a human sign-off). Keep P0/P1 rows at the top of each
table.
