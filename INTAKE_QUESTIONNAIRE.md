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
2. **Start URL(s):** ______
3. **Environment:** prod / staging / sandbox  → *(default & strong preference: non-prod)*
4. **In-scope URL patterns** (paths to include): ______  *(default: everything same-domain)*
5. **Out-of-scope URL patterns** (paths to exclude — e.g. `/admin`, `/billing`, `/logout`): ______
6. **Domain scope:** same-domain only / include subdomains / specific hosts  *(default: same-domain)*
7. **Authorized to test?** yes / no — **owner who approved:** ______

## B. Authentication & identity
8. **Requires login?** yes / no  *(default: no)*
9. **Auth type:** form / SSO / OTP / MFA / API key / other: ______
10. **Test account available?** yes / no — **role/permissions:** ______
11. **Capture multiple roles?** (e.g. admin vs standard) yes / no — which: ______
12. **Session gotchas:** timeout length, "remember me", anything that force-logs-out: ______
    *(Session is captured with `ui_discovery.login` → `--auth-state session.json`.)*

## C. Crawl budget & politeness
13. **Max pages:** ______  *(default: 25)*
14. **Max depth:** ______  *(default: 3)*
15. **Max runtime:** ______  *(default: none)*
16. **Rate limit / concurrency cap** (be gentle on shared/prod): ______  *(default: engine default)*
17. **Known heavy areas to cap or skip** (huge tables, reports): ______

## D. What to capture (capability toggles)
18. **Screenshots?** yes / no · full-page? yes / no  *(default: yes / yes)*
19. **Accessibility tree?** yes / no  *(default: yes)*
20. **Network / API observation?** yes / no  *(default: yes)*
21. **Interaction probe (safe-only)?** yes / no  *(default: yes)*
22. **Component/fingerprint analysis?** yes / no  *(default: yes)*
23. **Playwright test-skeleton export?** yes / no  *(default: no)*

## E. Interaction safety envelope
24. **Extra BLOCK words** beyond the defaults (delete/pay/send/…): ______
25. **Custom SAFE controls to allow** (non-standard but reversible): ______
26. **Forms:** skip / fill-with-safe-dummy-data  *(default: skip — never submit)*
27. **Regions/elements to never touch** (selectors or descriptions): ______
28. **Anything genuinely destructive that must never be clicked** (call it out explicitly): ______

## F. Application characteristics (helps handling; engine stays framework-agnostic)
29. **SPA?** yes / no — **routing:** path-based / hash-based  *(affects page identity — see H1)*
30. **Infinite scroll / virtualized lists?** yes / no
31. **iframes / shadow DOM / canvas UI?** yes / no — which
32. **WebSockets / realtime?** yes / no
33. **File uploads / downloads in flows?** yes / no

## G. Sensitive data & compliance
34. **PII / PHI / financial data visible on screen?** yes / no — what
35. **Redaction needs** (screenshots and/or network — mask which fields): ______
36. **Can outputs leave this machine?** yes / no  *(default: local-only; nothing is uploaded)*
37. **Anything that must NOT be screenshotted:** ______

## H. Outputs & delivery
38. **Wanted outputs:** crawl report / analysis / probe / test skeletons / docs  *(default: crawl + analysis)*
39. **Formats:** JSON / Markdown / HTML  *(default: all three)*
40. **Save location:** ______  *(default: `./output`)*

## I. Source-code correlation (V4 — optional)
41. **Frontend repo available?** yes / no — **path or URL:** ______
42. **Framework / build tool** (informational): ______

## J. Semantic / LLM layer (V5 — optional)
43. **LLM features allowed?** yes / no — **provider:** ______
44. **Data-sharing constraints** (what may/may not be sent to an LLM): ______

---

## From answers → config

The engine is **configurable, not hardcoded**: these answers become a scope
config (target-specific YAML), and every capability above is a toggle. Sketch of
where the automated intake (ROADMAP `S1`) will land:

```yaml
# scope.yaml  (generated from this questionnaire; consumed by all commands)
target: acme-portal
start_url: https://portal.acme.example/
scope:
  domain: same-domain            # A6
  include: ["/app/**"]           # A4
  exclude: ["/admin/**", "/billing/**", "/logout"]   # A5
auth:
  required: true                 # B8
  state_file: session.json       # B12
  login_url_pattern: "/login"    # H4 expiry detection
budget:
  max_pages: 25                  # C13
  max_depth: 3                   # C14
  rate_limit_per_min: 60         # C16
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
  formats: ["json", "markdown", "html"]
  dir: ./output
```

Until `S1` (automated intake) ships, fill this file by hand and keep it beside
the run. It doubles as the audit record of *what was in scope and why*.
