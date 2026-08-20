# Operator Intake Questionnaire — scoping a run

Fill this out **before** pointing the engine at a target. It scopes the run,
sets the safety envelope, and turns into the machine-readable config the engine
consumes (see the `scope.yaml` sketch at the end). Copy this file per target
(e.g. `intake/acme-portal.md`).

> Rule zero — **authorization.** Only run against applications you are
> explicitly permitted to crawl and test. If in doubt, stop and get sign-off.

Each question notes a **default** you can keep if it doesn't matter yet.

---

## A. Target & scope
1. **Product / app name:** ______
   *(Used as the run label and the artifact folder name — see H40.)*
2. **Product / start URL:** ______
   *(The single entry point the crawl begins from.)*
3. **Module URLs** — the distinct areas of the product worth capturing, one per
   line, each with a short name. A module is a separately-crawlable section
   (its own start URL), not just a page:

   | Module name | Start URL | In scope? | Notes (heavy? gated? skip?) |
   | --- | --- | --- | --- |
   | e.g. `dashboard` | `/platform/dashboard` | yes | |
   | e.g. `app-builder` | `/platform/app-builder` | yes | many per-app detail pages |

   *(Default: crawl from the single start URL and let same-domain discovery
   find everything. List modules when you want them crawled/reported
   separately, or when only some are in scope.)*
4. **Known API endpoints** (informational — the engine **observes** network
   traffic, it does not call these directly): ______
   *(Useful for recognising which observed requests matter. Format:
   `METHOD /path` per line, e.g. `GET /api/v1/apps`. Default: leave blank —
   V3's network probe discovers endpoints on its own.)*
5. **Environment:** prod / staging / sandbox  → *(default & strong preference: non-prod)*
6. **In-scope URL patterns** (paths to include): ______  *(default: everything same-domain)*
7. **Out-of-scope URL patterns** (paths to exclude — e.g. `/admin`, `/billing`, `/logout`): ______
8. **Domain scope:** same-domain only / include subdomains / specific hosts  *(default: same-domain)*
9. **Authorized to test?** yes / no — **owner who approved:** ______

## B. Authentication & identity
10. **Requires login?** yes / no  *(default: no)*
11. **Auth type:** form / SSO / OTP / MFA / API key / other: ______
12. **Test account available?** yes / no — **role/permissions:** ______
13. **Capture multiple roles?** (e.g. admin vs standard) yes / no — which: ______
14. **Session gotchas:** timeout length, "remember me", anything that force-logs-out: ______
    *(Session is captured with `ui_discovery.login` → `--auth-state session.json`.)*

## C. Crawl budget & politeness
15. **Max pages:** ______  *(default: 25)*
16. **Max depth:** ______  *(default: 3)*
17. **Max runtime:** ______  *(default: none)*
18. **Rate limit / concurrency cap** (be gentle on shared/prod): ______  *(default: engine default)*
19. **Known heavy areas to cap or skip** (huge tables, reports): ______

## D. What to capture (capability toggles)
20. **Screenshots?** yes / no · full-page? yes / no  *(default: yes / yes)*
21. **Accessibility tree?** yes / no  *(default: yes)*
22. **Network / API observation?** yes / no  *(default: yes)*
23. **Interaction probe (safe-only)?** yes / no  *(default: yes)*
    - 23a. **Any module that must be read but never clicked?** (name + start URL)
    - 23b. **Any tabs that must never be opened?** (e.g. Audit Log — heavy, or
      noisy on every screen)
    - 23c. **Photograph modals/menus/panels as they open?** yes / no  *(default: yes)*
    - 23d. **Crop screenshots of forms, dialogs and tables?** yes / no  *(default: yes)*
    - 23e. **CSS selectors for cards/tiles/widgets** you want photographed
      *(these have no standard markup, so the engine cannot find them for you)*
24. **Component/fingerprint analysis?** yes / no  *(default: yes)*
25. **Playwright test-skeleton export?** yes / no  *(default: no)*

## E. Interaction safety envelope
26. **Extra BLOCK words** beyond the defaults (delete/pay/send/…): ______
27. **Custom SAFE controls to allow** (non-standard but reversible): ______
28. **Forms:** skip / fill-with-safe-dummy-data  *(default: skip — never submit)*
29. **Regions/elements to never touch** (selectors or descriptions): ______
30. **Anything genuinely destructive that must never be clicked** (call it out explicitly): ______

## F. Application characteristics (helps handling; engine stays framework-agnostic)
31. **SPA?** yes / no — **routing:** path-based / hash-based
    *(Hash-based routing needs `--hash-routes` so each `#/route` is its own
    page; otherwise the whole SPA collapses into one. See H1 in the README.)*
32. **Query params that are just noise** (tracking/session ids that shouldn't
    split one page into many): ______
    *(Handled by `--dedupe-queries`, plus `--drop-param NAME` for app-specific
    ones. Defaults already cover `utm_*`, `gclid`, `sessionid`, ...)*
33. **Infinite scroll / virtualized lists?** yes / no
34. **iframes / shadow DOM / canvas UI?** yes / no — which
35. **WebSockets / realtime?** yes / no
    *(Realtime apps often finish rendering after the network goes idle — the
    engine already waits for the DOM to stop mutating before capturing.)*
36. **File uploads / downloads in flows?** yes / no

## G. Sensitive data & compliance
37. **PII / PHI / financial data visible on screen?** yes / no — what
38. **Redaction needs** (screenshots and/or network — mask which fields): ______
39. **Can outputs leave this machine?** yes / no  *(default: local-only; nothing is uploaded)*
40. **Anything that must NOT be screenshotted:** ______

## H. Outputs, artifact location & naming
41. **Wanted outputs:** crawl report / analysis / probe / test skeletons / docs  *(default: crawl + analysis)*
42. **Formats:** JSON / Markdown / HTML  *(default: all three)*
43. **Artifact root** — where run artifacts are saved: ______  *(default: `./output`, via `--output`)*
44. **Keep history across runs?** yes (one folder per run) / no (overwrite in place)
    *(**Default today: no** — a re-run of the same URL overwrites the previous
    artifacts in the same folder. If you need before/after snapshots to compare
    — e.g. for a change diff — point `--output` at a dated root per run, e.g.
    `--output output/2026-08-19`, until per-run folders land natively.)*
45. **Run label** (used in folder names when keeping history): ______
    *(default: the product name from A1, slugified)*

### Folder structure & naming conventions (what the engine writes today)

Artifacts always land under `<artifact-root>/<slug>/`, where **`<slug>` is
derived from the URL**, not chosen by hand: host + path, with every character
outside `[A-Za-z0-9._-]` collapsed to `_`, truncated to 120 chars.
So `https://portal.example.com/platform/dashboard`
→ `portal.example.com_platform_dashboard`.

```
<artifact-root>/                 # --output, default ./output
  <slug>/                        # one folder per crawl start URL (see above)
    crawl.json                   # V1 canonical crawl model — source of truth
    report.md / report.html      # V1 crawl report
    analysis.json/.md/.html      # V2 fingerprints, regions, components
    probe.json/.md/.html         # V3 safe interactions + observed network
    semantics.json/.md/.html     # V5.1 semantic element labels
    documentation.json/.md/.html # V5.2 generated UI reference doc
    qa.json/.md/.html            # V5.3 candidate test scenarios
    generated_tests.py           # C2 runnable Playwright skeletons
    generated_tests.spec.ts      #    (.spec.ts with --lang ts)
    screenshots/
      <page-slug>.png            # one full-page screenshot per crawled page,
                                 # same slug rule applied to each page URL
```

Single-page `extract` runs use the same `<artifact-root>/<slug>/` layout but
write `page.json` + `screenshot.png` instead of the crawl set.

Conventions worth knowing:
- **Stem = stage, extension = format.** Every stage writes the same stem in
  three formats (`analysis.json` / `.md` / `.html`), so tooling can glob by
  stage or by format.
- **JSON is canonical**; `.md`/`.html` are rendered *from* it, never the reverse.
- **Slugs are deterministic** — the same URL always maps to the same folder,
  which is what makes re-runs overwrite in place (see H44).
- **Session files are secrets.** `session.json` / `*.session.json` are
  gitignored; keep them out of the artifact root if that root is ever shared.

46. **Deviations from the above** (if this run needs a different layout or
    naming, write it here): ______

## I. Source-code correlation (V4 — optional)
47. **Frontend repo available?** yes / no — **path or URL:** ______
48. **Framework / build tool** (informational): ______

## J. Semantic / LLM layer (V5 — optional)
49. **LLM features allowed?** yes / no — **provider:** ______
50. **Data-sharing constraints** (what may/may not be sent to an LLM): ______

---

## From answers → config

The engine is **configurable, not hardcoded**: these answers become a scope
config (target-specific YAML), and every capability above is a toggle. Sketch of
where the automated intake (ROADMAP `S1`) will land:

```yaml
# scope.yaml  (generated from this questionnaire; consumed by all commands)
target: acme-portal              # A1 — also the default run label
start_url: https://portal.acme.example/                # A2
modules:                         # A3 — crawl/report these areas separately
  - name: dashboard
    start_url: https://portal.acme.example/app/dashboard
  - name: reports
    start_url: https://portal.acme.example/app/reports
    max_pages: 10                # per-module budget override (optional)
known_endpoints:                 # A4 — informational; helps label observed traffic
  - "GET /api/v1/accounts"
  - "POST /api/v1/reports"
scope:
  domain: same-domain            # A8
  include: ["/app/**"]           # A6
  exclude: ["/admin/**", "/billing/**", "/logout"]   # A7
auth:
  required: true                 # B10
  state_file: session.json       # B14
  login_url_pattern: "/login"    # H4 expiry detection
budget:
  max_pages: 25                  # C15
  max_depth: 3                   # C16
  rate_limit_per_min: 60         # C18
identity:                        # section F — what counts as "the same page" (H1)
  hash_routes: false             # F31 — true for hash-routed SPAs
  dedupe_queries: true           # F32
  drop_params: ["tab", "sort"]   # F32 — app-specific noise, beyond the defaults
capabilities:                    # section D — feature toggles
  screenshots: true
  accessibility_tree: true
  network: true
  probe: true
  analysis: true
  export_tests: false
safety:                          # section E
  block_words_extra: ["deactivate account"]
  never_touch: ["#danger-zone"]
  submit_forms: false
privacy:                         # section G
  redact_network_keys: ["token", "ssn", "account"]
  local_only: true
outputs:                         # section H
  formats: ["json", "markdown", "html"]      # H42
  dir: ./output                              # H43 — artifact root
  keep_history: false                        # H44 — true = one folder per run
  run_label: acme-portal                     # H45 — used when keep_history
```

Until `S1` (automated intake) ships, fill this file by hand and keep it beside
the run. It doubles as the audit record of *what was in scope and why*.

**Note on what's wired today.** `scope.yaml` itself is not yet consumed — the
config plumbing is ROADMAP `H5` + `R2`, and automated intake is `S1`. Right
now these answers map to flags:

| Answer | Flag today |
| --- | --- |
| A2 start URL | positional arg to `crawl` / `extract` / `probe` |
| A3 modules | one run per module, each with its own start URL |
| B10/B14 auth | `python -m ui_discovery.login` → `--auth-state session.json` |
| C15/C16 budget | `--max-pages` / `--max-depth` |
| F31 hash routing | `--hash-routes` |
| F32 query noise | `--dedupe-queries`, `--drop-param NAME` |
| H43 artifact root | `--output DIR` |
| H44 keep history | *(not yet native — use a dated `--output` root per run)* |
