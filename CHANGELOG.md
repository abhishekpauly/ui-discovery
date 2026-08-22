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

### Added

- **`G1` Authorization is enforced, not merely recorded.** `authorized`,
  `authorized_by` and `environment` sat in the scope schema for five releases
  being read by nothing. An operator wrote them down, believed the run was
  accountable, and the engine never consulted them —
  `tests/test_no_dead_config.py` had to special-case all three to stay green.

  A config saying `environment: prod` (or `production`) is now refused unless
  it also carries `authorized: true` and a non-empty `authorized_by`. The
  refusal exits `3` — distinct from `1`, so a pipeline can tell "you may not
  run this" from "the config would not parse" — and happens before a URL is
  resolved, a session is read or a browser exists, so nothing is opened.
  `pipeline` and `crawl` share one gate; the rule itself is
  `Scope.authorization_refusal()`, pure and importable.

  Deliberately narrow: `staging`, `sandbox`, `preprod`, an absent
  `environment:` and every zero-config run are untouched. A gate that fired on
  staging would be switched off within a week, and a gate that is off protects
  nothing. The engine cannot verify that a person approved a capture; refusing
  to open production unattributed is the narrowest useful thing these fields
  can mean.

  The three fields left `DOCUMENTED_AS_METADATA`. That guard is a regex and
  its own docstring calls it "crude on purpose: a name that is merely
  mentioned passes" — `cliconfig.describe()` already mentioned two of them —
  so retiring the exemption proves nothing on its own and the real proof is in
  `tests/test_g1_authorization.py`.

### Fixed

- **The release workflow could not publish a retroactive tag.** Its smoke test
  runs `pytest -m "not browser"`, but the `browser` marker only exists from
  `0.16.0`, where `tests/conftest.py` started applying it. At an older tag that
  selects *everything*, Chromium is not installed in the release job, and the
  release fails for a reason that has nothing to do with the release. Those
  tags were tested by CI when they were current, so the step now skips itself
  when the marker is absent.
- **Release notes now come from the default branch's `CHANGELOG.md`.** They
  were read from the tag's own tree, which cannot work for a retroactive tag:
  `scripts/changelog_section.py` was added long after `v0.12.0`, so the step
  failed on every backfilled release. A tag's changelog also cannot describe
  releases made after it. `main` is the one complete record; the tag supplies
  only the code.
- **`v0.17.0` withdrawn.** It was applied to `adbbc5a`, whose `pyproject.toml`
  and `__version__` both say `0.18.0` — that one squashed commit carried both
  releases, so `0.17.0` never existed as a distinct state on `main`. The
  release workflow's version guard refused it, correctly. The changelog entry
  stands and names the commit the work arrived in; a tag pointing at code that
  calls itself something else would be a worse record than no tag.


### Added

- **`X7` Repo governance.** The project had a careful contract for the *code*
  and none at all for the repository around it: no tags, no releases, labels
  invented at click-time, and one branch per item with nothing above it. All
  four are now written down and, where possible, executed by a file rather than
  by memory.
  - `BRANCHING.md` — trunk-based development with **sprint integration
    branches**, so several sprints can run at once and each still merges to
    `main` as a reviewable, revertible unit. Squash into a sprint, merge commit
    into `main`. Cut: `sprint/1-governance`, `sprint/2-field-validation`,
    `sprint/3-devex`, `sprint/4-deferred`.
  - `RELEASING.md` — a release is an annotated `vX.Y.Z` tag; everything else is
    rendered from the repo. Covers the two version numbers, the bump policy,
    hotfixes, and why a tag is never moved.
  - `.github/labels.yml` — the label set as data, in six dimensions that mirror
    `PRODUCT_TRACKER.md` (`epic:`, `area:`, `P0`–`P3`, `effort:`, `sprint:`).
    Applied by `scripts/sync_labels.py`; additive unless asked to `--prune`.
  - `.github/workflows/release.yml` — a pushed tag builds the sdist and wheel,
    lifts that version's section out of `CHANGELOG.md`, and publishes the
    Release. It refuses to publish when the tag, `pyproject.toml` and
    `__version__` disagree, which is the only place a mistyped tag is still
    cheap to catch.
  - `.github/workflows/labels.yml` — shows the label plan on a pull request,
    applies it on merge.
  - `scripts/changelog_section.py` — the changelog is the release notes. There
    is one copy, so the two cannot disagree.
  - `scripts/bootstrap_github.sh` — labels, tags, releases and the board, made
    reproducible rather than remembered.
  - `.github/ISSUE_TEMPLATE/bug.yml` — a bug report that asks for a fixture and
    refuses real URLs, sessions and captures.
- **Tags `v0.12.0`–`v0.18.0`**, backfilled onto the commits that introduced each
  version, dated as those commits. The repo previously had none.
  `v0.1.0`–`v0.11.0` predate this git history and remain changelog-only.
- **Three epics and thirteen items in the backlog**, from a review of current
  web-crawl platform capabilities against this engine's principles. Specs only —
  no source changed.
  - `EPIC-MAP` (`M1`–`M3`) — the engine finds URLs one way, by walking what it
    has already rendered, so a module nothing links to is invisible and *what a
    crawl would do* cannot be known without running it. Sitemap ingestion, a
    `map` command that reports every URL with the rule that included or excluded
    it, and `--dry-run`.
  - `EPIC-FRESH` (`L1`–`L4`) — `requested_url` and `final_url` have both existed
    since V0 and no report has ever compared them, so a capture of forty login
    screens looks healthy. Per-page capture verdicts with evidence, capture age
    in the reports, and a `verify` command that asks an old capture whether it
    is still true. `L1` is what `QA.4` has been waiting on.
  - `EPIC-INTERACT` (`I1`–`I3`) — the probe explores but cannot be *told*
    anything, so the screens behind a filter selection are exactly the ones a
    capture misses. Declarative, config-declared step recipes that run through
    `safety.py` **unchanged**: a recipe narrows what is touched and can never
    widen it, and a refused step ends the recipe loudly. Carries
    `principle-risk` deliberately.
  - `H6`–`H8` — subdomain policy (`util.same_site` is exact-netloc today),
    external links recorded as edges rather than dropped in silence, and a
    ledger of what a crawl did *not* capture.
  - `X8` — a decision table for the nine commands, costed in wall-clock and
    pages.
- **`ROADMAP.md` § K — Considered and declined.** What that same review rejected
  and on which principle: hosted LLM extraction, natural-language browser
  agents, arbitrary script execution against the page, webhooks and hosted job
  APIs, URL discovery via third-party indexes, and following links off the
  target domain. Written down so the proposals are not re-litigated every few
  months, and so a *changed circumstance* is what reopens one. The second
  review added thirteen more entries, three of which are worth stating plainly
  because they define what this engine is not: **page content as markdown for
  LLM consumption** (the first line of `CLAUDE.md` — this is not a scraper, and
  `docgen` writes documentation *from* the model, which is the opposite
  direction), **document parsing** (a PDF is not a user interface; the taxonomy
  records that a screen offers a download and what control does it), and
  **anti-bot proxy escalation** (an authorized target does not need to be
  evaded, and building evasion would make principle #11's "nothing but the
  target" false).
- Labels `epic:MAP`, `epic:FRESH`, `epic:INTERACT` and `sprint:5-discovery`,
  `sprint:6-liveness`, `sprint:7-reachability`.
- **A second capability review, and three more epics plus nineteen items.**
  Specs only — no source changed.
  - **`G5`–`G7` privacy, and they jump the queue.** The engine redacts typed
    values, password fields and sensitive query keys, and does not redact
    *rendered page content*: on a logged-in portal, `Element.text`, accessible
    names, the ARIA snapshot, `elements.csv` and every screenshot carry real
    customer names, addresses and account references. `G5` redacts the model
    deterministically at capture time; `G6` masks the screenshots using the
    element geometry already recorded; `G7` puts every host contacted into the
    manifest. These are the only P0s in the queued set, and `QA.2` is in
    flight — `G4` governs how long a capture survives, `G5`/`G6` govern whether
    the data is written at all, and the weaker guarantee does not go first.
  - **`EPIC-WATCH` (`W1`–`W4`)** — the engine produces a capture and `C1`
    compares two of them; nothing has ever run it twice. A `watch` command that
    captures, diffs against the last run, and exits `0`/`1`/`2`, plus
    deterministic rules for what counts as worth waking up for, a renderer for
    the `runs.jsonl` nothing has read since 0.18.0, and an estimate of what a
    cadence will cost. **No daemon, no notifications** — every environment that
    would schedule this already has cron and already knows how to deliver bad
    news.
  - **`EPIC-VARIANT` (`PV1`–`PV3`)** — each screen is captured once, at one
    viewport, in one locale, in whatever colour scheme the browser defaulted to,
    and the result is called a model of the UI. Viewport, locale,
    `prefers-color-scheme` and `prefers-reduced-motion` are browser-level
    inputs, which makes this exactly what principle #1 was written for. `PV3` is
    the payoff: *state the difference* — which controls the mobile variant hides
    and what reveals them.
  - **`EPIC-VOCAB` (`T1`–`T3`)** — the engine models controls and misses the
    material they are made of. Design tokens derived from `getComputedStyle`
    (with contrast ratios, since it is arithmetic and the data is already in
    hand), the semantics a page declares about itself (`lang`, meta, Open Graph,
    JSON-LD), and token drift across instances of one component — the finding
    no design review catches because no reviewer opens nine screens side by side.
  - `M4` orphan and dead-end screens (a URL that works and nothing links to),
    `H9` excluding cookie banners and chat widgets from the model, `H10`
    capturing an explicit URL list, `H11` TLS verification as a *recorded*
    decision, `C3` teaching the diff what noise looks like, and `X9` capture
    profiles.
- Labels `epic:WATCH`, `epic:VARIANT`, `epic:VOCAB`, `area:privacy`, and
  `sprint:8-redaction` / `9-watch` / `10-variants` / `11-vocabulary`.
- **`scripts/sync_board.py`**, and a `board` section in
  `bootstrap_github.sh`. The board answers "who is on it and where has it got
  to" — but only for issues that are on it, and adding fifty by hand is a job
  nobody repeats after the first sprint. The backlog ID is not invented: it is
  read from the issue title, which already carries it because the issue
  templates require that format. Idempotent, never removes a card, and needs
  `gh auth refresh -s project,read:project` because the project scope is not
  part of a default login. `tests/test_sync_board.py` covers the title parse
  against both dash styles in circulation — issues #1–#14 predate the em-dash
  convention, and renaming them would break every reference already written
  down.

### Changed

- `fast` and `full` now also run on pushes to `sprint/**`, so a shared sprint
  branch is known-green before its pull request rather than at the moment it is
  due.
- `CONTRIBUTING.md` points at the branching and release models instead of
  implying a single flat branch, and says where issues, labels and the board fit.

### Known irregularity

`0.17.0` and `0.18.0` both shipped through PR #16/#17, and the commit carrying
the `0.17.0` changelog entry already declares `version = "0.18.0"`. The
backfilled tags follow the changelog, because the changelog is what describes a
release. `release.yml` warns rather than fails for tags at or below `0.18.0` for
this reason, and fails from `0.19.0` onward.

---

`G1`–`G4` (governance) are specified in `ROADMAP.md` and tracked as `EPIC-GOV`,
followed by `G5`–`G7`, then `M1`–`M4` + `H6`–`H8`, `L1`–`L3` + `C3`, `I1`–`I3`,
`W1`–`W4` + `H10` + `X9`, `PV1`–`PV3` + `H9` + `H11`, and `T1`–`T3` — consecutive
sprints rather than parallel ones, because all of them contend `models.py`,
`config.py`, `crawler.py` and `reports.py`. The one departure from "sequenced by
contention" is `G5`–`G7`, which go first on severity.

Still deliberately deferred: `X4` (incremental crawl) is a speculative
optimization until crawl times actually hurt; `X6` (storage backend) waits on
data volume — `O5`'s `runs.jsonl` is the deliberate non-database answer. `L4` is
the time-boxed spike that could end `X4`'s deferral with a number rather than a
judgement, now that `O4` reports where a run's time goes.

---

## [0.18.1] — pytest 9 (security advisory)

### Fixed

- **`pytest` 8.3.4 → 9.0.3.** GHSA: vulnerable tmpdir handling, medium
  severity, flagged by Dependabot the moment the repo went public. Dev-only
  and local in nature — it does not touch the shipped library — but it is a
  one-line pin and the whole point of a public repo with alerts on is to act
  on them.

  A major version bump, so it was worth checking rather than assuming: the
  full suite passes unchanged on 9.0.3 (595 passed, 6 skipped), with no
  deprecation fallout to fix.
- **`pytest-split` 0.10.0 → 0.11.0**, which is what actually made the above
  installable: 0.10.0 pins `pytest<9`. Verifying locally with
  `pip install pytest==9.0.3` bypassed the resolver and proved nothing — CI
  caught it on the first honest `pip install -e ".[dev]"`.

---

## [0.18.0] — Where the time went (O4-O5)

**Where this landed.** This release and `0.17.0` both reached `main` in a
single squashed commit — `adbbc5a` (#16) — whose subject names only `0.17.0`.
`f3a11e6` (#17) followed with the end-to-end `O4`/`O5` assertions and a
pluralisation fix. Both entries describe what shipped accurately; the commit
subject does not, and `git log` is the first place someone looks. Recorded
here rather than rewritten: `main` is protected, and two merged pull requests
point at those commits.

`0.17.0` made a run identifiable. This makes it *measurable*. `QA.3` asks
whether probing every page by default is too slow for a real portal — a
question that has been answered from impression since the day probing became
the default, because nothing in a capture recorded what interacting cost.

Closes `O4` and `O5`, completing `EPIC-OBS`.

### Added

- **`O4` Stage metrics.** A `metrics` block on the manifest: per-stage
  durations and shares, what each stage produced, seconds per screen, screens
  per minute, and the slowest stage. Everything is derived from the stage
  records already in the manifest — nothing is measured twice, so nothing can
  disagree.
- **Probe cost is measured, not remembered.** `CrawlStats.probe_ms` accumulates
  the time each page spent being interacted with — counted in the crawler, so a
  `crawl` invoked directly can answer the question too. The manifest reports it
  as a share of the crawl: *"interacting accounted for 18.0s, 58% of the
  crawl"*. That is `QA.3`, answerable from a file.
- **`Where the time went` in `summary.md`.** The timing table lands in the file
  a person actually opens, spliced in once every stage has finished. The
  summary is still written the moment the crawl ends — it is the artifact you
  would most regret losing to a later stage falling over — so the block is
  added afterwards rather than the summary held back.
- **`O5` Run index.** `runs.jsonl` at the output *root*, one line per run:
  when, against what, outcome, duration, screens, elements, seconds per screen,
  engine version, config digest, and the folder to go and read. Deliberately
  not a database (`X6`): a hundred runs is a 30KB file you can `tail`.
- Per-stage counts (`run.count(...)`) and `run.stage()` now yielding the stage's
  own record, so a stage can say what it produced.
- `resolve_output_root()` in `cliconfig` — the index belongs above the dated and
  per-product folders, or it only ever sees today's captures.
- Public API: `read_index`.

### Notes

`probe_ms` is cumulative across pages, so under concurrency it can exceed the
crawl's wall clock. It is reported as a share of the *work*, and the summary
says so rather than leaving a reader to discover it from an impossible number.

Indexing is idempotent per run: `finish()` can be reached twice — a caller that
finishes explicitly inside a `with` block passes through `__exit__` too — and a
run counted twice would corrupt exactly the trend the index exists to show.

### Tests

+18 (583 → 601 collected; 595 passed and 6 skipped when this shipped),
covering share arithmetic that accounts for the whole run,
the measured probe cost against a real crawl, splicing that preserves the rest
of the summary and replaces itself rather than accumulating, one line per run
under repetition, and a failed run still reaching the index.

---

## [0.17.0] — A run can account for itself (O1-O3)

Shipped in `adbbc5a` (#16) — which carried `0.18.0` with it. See that entry
for why the commit subject names only this release.

The engine could say what it found. It could not say who ran it, against what,
under whose authorization, how long each stage took, or what happened along the
way. `crawl_id` existed, but a *pipeline run* spanning crawl → analyze →
semantic → docgen → qagen had no identity of its own.

Closes `O1`, `O2`, `O3` (#4, #5, #6) — the first sprint tracked as GitHub
issues, and the first change to land through the branch ruleset.

### Added

- **`run.py`** — `RunContext`: run identity, an event stream, and a manifest.
  Files only. No service, no exporter, no new dependency, nothing listening; a
  run is accountable because it writes down what it did, which keeps principle
  #11 intact and means the record survives on a laptop with no network exactly
  as it does in CI.
- **`O1` Run identity.** One `run_id` per pipeline run, with `crawl_id` as its
  child and `Crawl.run_id` recording the link. Twelve hex characters, matching
  `crawl_id`, so the two read as siblings in a log. A `crawl` invoked directly
  still has no run and is still a complete artifact.
- **`O2` Event stream.** `events.jsonl` beside the capture: `run.started`,
  `stage.started`/`finished`/`skipped`, `page.captured`, `page.skipped` (with
  the budget that stopped it), `probe.executed`, `probe.refused` (with the
  safety verdict and reason), `state.captured`, `auth.rejected`,
  `budget.exhausted`, `run.finished`/`failed`. Flushed as they happen, because
  the run that dies is precisely the run whose events you want.
- **`O3` Manifest.** `run.json`: ids, versions, outcome, target, operator, host,
  per-stage timings and status, a stats rollup, every artifact written, and
  `config_sha256` over the *resolved* scope — so two runs are provably the same
  configuration even when one used flags and the other a config file, and
  differ the moment one setting does.
- Public API: `RunContext`, `config_digest`, `read_events`, `write_manifest`.

### Notes on what is deliberately absent

The manifest records `auth_used`, the credential *source* and hours to expiry.
It never contains the session. `command_line()` keeps the session filename —
useful — and drops its directory, so a manifest never advertises where
credentials live.

### Fixed

- **`emit()` silently dropped every `state.captured` event.** The crawler
  passes the state's own name, which collided with the event-name parameter —
  a `TypeError` that `emit`'s never-raises guarantee swallowed. A real crawl
  reported `states_captured=4` beside zero such events. The event name is now
  positional-only in both `emit()` and the crawler wrapper, so a payload key
  can never bind to it, and a test asserts the count matches the probe stats.

### Tests

+24 (552 → 576), covering ordering, flush-on-write, crash survival, secret
absence, and the keyword collision above.

---

## [0.16.0] — Publishable repo, CI, and a tracked backlog

Infrastructure, not engine. The code worked; everything around it was manual —
no remote, no CI, no gate on `main`, and a backlog that lived only in markdown.

### Added

- **CI (`X3`)**, which had been blocked on "a git remote existing first". Three
  workflows: `fast` (ruff + the 117 browser-free tests, ~3s locally, on Python
  3.11 and 3.14), `full` (the browser suite sharded across 6 runners), and
  `capture` — which runs the pipeline against `fixtures/forms/` and attaches
  `report.html` to the PR. Reviewing *whether the output got better* was the
  slowest step in reviewing a reporting change; now it is a download.
- **Automatic `browser` test marking** (`tests/conftest.py`). A module that
  imports `extract_page`, `crawl_site`, `probe_page` or `sync_playwright` — or
  a test using the `serve` fixture — is marked without anyone maintaining a
  list of 28 files.
- **`ruff`**, configured narrowly (`E,F,I,W`, `E501` ignored) on a codebase that
  had never been linted. The one-time cleanup fixed 34 findings automatically
  and 12 by hand; the only substantive one was a discarded return value in
  `pipeline.py`, now used to report which module folders were written.
- **Repo scaffolding**: `CODEOWNERS`, a PR template that checks the
  non-negotiable principles, story/epic issue templates that require a backlog
  ID, and `CONTRIBUTING.md`.
- **`O1`–`O5` and `G1`–`G4`** specified in `ROADMAP.md` and tracked in
  `PRODUCT_TRACKER.md` — run identity, event stream, manifest, metrics, run
  index; authorization enforcement, safety envelope on the record,
  data-handling posture, retention. A run currently cannot say who ran it,
  against what, or under whose authorization.

### Changed

- **The repo is publishable.** It named an internal QA host, its route map and
  a work email — in files and in four commits, with the email in the author
  field of all 46. The target-specific example config becomes
  `examples/authenticated-spa.scope.yaml`, keeping every lesson that config
  taught (per-instance excludes, `extra_wait` for websocket SPAs, politeness
  caps) and losing only the identifying detail. History was rewritten with
  `git-filter-repo`; the 48 commits and their messages survive intact.
- `*.local.yaml` joins `session.json` in `.gitignore`. A config naming a real
  internal host is reconnaissance and stays out of the repo.

### Fixed

- **The suite only ran under `python -m pytest`.** Ten test modules do
  `from tests.conftest import Server`, which needs the repository root on
  `sys.path`. `-m` puts the working directory there as a side effect; the
  `pytest` console script does not — so the first CI run failed five of six
  shards with `ModuleNotFoundError`. A root `conftest.py` fixes it, because
  pytest prepends the directory of every conftest it collects. Latent since
  the helper was first shared; CI is what surfaced it.

### Tests

`ruff check` clean; 117 tests run without a browser. The suite now runs
identically under `pytest` and `python -m pytest`.

---

## [0.15.2] — An exact page budget, and names the browser already knew

Both fixes come from measuring the 0.15.1 validation run rather than trusting
its numbers.

### Fixed

- **`max_pages` was approximate: 25 became 38.** Crawlee's
  `max_requests_per_crawl` counts *completed* requests and is checked before
  dispatching the next one, so anything in flight — or being retried — does not
  count yet. The real portal retried 29 requests on a slow SPA, and thirteen
  extra pages slipped through while the counter lagged. The handler now claims
  its budget slot on entry, with no `await` between the check and the claim, so
  the count holds exactly under any concurrency. Verified 5/10/25/40 on a
  sixty-page interlinked fixture; previously every one overshot.
- **Elements lost accessible names the browser computes.** `<th>Order</th>` had
  no name at all. Name-from-content was keyed off a hardcoded tag list plus an
  *explicit* `role=` attribute, so every element with an implicit
  name-from-content role — column headers, cells, tabs, menu items — was
  skipped. 119 of 345 unnamed elements in the real capture had visible text
  sitting right there, 44 of them column headers. The rule now follows the
  WAI-ARIA "name from content" table against the element's *computed* role.
- **Unnamed controls were dropped from the report silently.** Having nothing to
  call them, the actions table omitted them — making every screen look emptier
  than it was, with no explanation. They are now counted and stated, per screen
  and per capture, as what they are: an accessibility defect in the application
  that also makes the product unnavigable by screen reader. Deliberately *not*
  invented from the only identifiers available — on the real portal those were
  framework-generated (`radix-:r9:`) or CSS classes, and presenting either as a
  name would be a fabrication.

### Tests

+10 (542 → 552).

---

## [0.15.1] — Fixes from the first real-portal run

Running 0.15.0 against a live QA portal (38 screens) surfaced three defects
that fixture-sized captures could not.

### Fixed

- **Repeated components were photographed once per instance.** A grid of model
  cards each carrying a "Try out" button opened the same drawer 37 times, and
  the report listed all 37; across the capture, 158 states collapsed to 14
  distinct affordances. A labelled trigger now *is* the state's identity — the
  same control opening the same kind of thing is one affordance, reported once
  with the number of controls that open it. Unlabelled (icon-only) triggers
  still key on what they reveal, so genuinely different menus stay distinct.
- **Unnamed containers were named with their own body text.** States came out
  titled "What's New (V2.14.0)Version 2.14.0Aug 10, 2026What's New in ACME
  We've been busy. Here's everything that landed recentl" — which was also the
  image alt text. A container with no short name of its own now falls back to
  the label of the control that opened it.
- **A few blank pages condemned a whole capture.** Three `agent-builder/<uuid>`
  deep links rendered nothing (they need query params the crawler did not
  have), which flagged all 38 screens as "the login/blank state, not the
  product" while 35 held real content. A login page reached while holding a
  session still means expiry on sight; a *blank* page now has to be the
  dominant outcome before the capture is condemned. Telling someone to throw
  away a good capture is as bad as missing a bad one.

### Tests

+9 (533 → 542).

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
