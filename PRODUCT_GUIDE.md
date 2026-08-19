# Product guide — what this crawler is, and how it works

For anyone deciding whether to use it, explaining it to someone else, or
judging how far to trust its output.

To actually run it, see [`RUNBOOK.md`](RUNBOOK.md).

---

## In one paragraph

Point it at a web application — including one behind a login — and it drives
a real browser through the UI, recording what is actually there: every screen,
every button and field, the navigation between them, the API calls behind
them, and a screenshot of each. It produces a structured, versioned snapshot
you can read, diff against a later one, generate documentation and test
skeletons from, and correlate back to your source code.

It never guesses. Everything it reports, it observed.

---

## What it can do

| Capability | What you get |
| --- | --- |
| **Capture a screen** | Every interactive and structural element, with its accessible name, role, state, position and a stable selector |
| **Crawl an app** | The whole same-domain surface, as a page graph with depth and navigation edges |
| **Get past a login** | You log in by hand once; it reuses that session. No passwords, SSO or OTP handling in the tool |
| **Probe behavior safely** | Clicks only reversible controls (tabs, disclosures, menus) and records what each one changed |
| **Discover the API** | The endpoints the UI actually calls, with methods and status codes |
| **See what changed** | A deterministic diff between two snapshots — including *renamed* controls, not just added/removed |
| **Generate documentation** | A UI reference doc assembled from the capture |
| **Generate test scenarios** | Candidate cases plus runnable Playwright skeletons using stable selectors |
| **Correlate to source** | Links what it saw at runtime to components, routes and API call sites in your repo, with confidence and evidence |

## What it deliberately does not do

- **It does not test.** It reports what exists. Whether that is correct is
  your judgement.
- **It does not click anything destructive.** See *Safety* below.
- **It does not submit forms.** It can fill them; it never sends them.
- **It does not know intent.** It can tell you a button was renamed. It cannot
  tell you whether that was deliberate.
- **It does not execute your source code.** Repo analysis is text only.
- **It does not need an LLM.** Every core capability is deterministic.

---

## How it works

### 1. It uses a real browser

Playwright drives Chromium. That means it sees the app as a user does —
after JavaScript has run, frameworks have rendered, and data has loaded. It
reads no source, bundles or build output to do this.

**It is framework-agnostic by construction.** It only ever asks the browser
standard questions: what is the accessible role of this element, what is its
name, is it visible. Nothing anywhere assumes React, Angular or Vue.

### 2. It waits for the page to actually be ready

The hardest part of capturing a modern app is knowing when to look. Three
signals, in order: the DOM loads, network traffic goes idle, then the DOM
must stop changing *with content in it*.

That last clause is doing real work. An app shell that hasn't started
rendering has an unchanging DOM, so a naive "has it stopped changing?" check
calls a blank page settled. Every readiness signal is recorded in the
snapshot (`dom_stable`, `dom_stable_wait_ms`, `networkidle`) so you can judge
whether a capture was taken against a settled page.

**Known limit:** an app that holds a websocket open never reaches network
idle, so this heuristic does all the work alone and can misjudge a pause
between fetches. Those apps need a fixed settle window — see `extra_wait` in
*Extending it*.

### 3. It gives every element a stable identity

Each element gets a fingerprint built from the most durable signal available:
a hand-written `data-testid`, else an `id`, else its structural position plus
role and name. Machine-generated ids (hashes, CSS-module suffixes) are
detected and skipped, because they change every build.

This is what makes diffing possible. It is also why a *renamed* control can
be recognised as the same control rather than reported as one thing vanishing
and another appearing.

### 4. It sees past boundaries other tools miss

- **Open shadow roots** are traversed — component libraries put real controls
  there. Closed roots are not, and cannot be: the browser does not expose them.
- **Same-origin iframes** are entered and merged, tagged with their frame.
- **Cross-origin iframes** are recorded but *not* entered — a scoping choice,
  not a technical limit. Third-party embedded content is not your product.

### 5. Everything is append-only and versioned

`crawl.json` is the source of truth; every report is rendered *from* it, never
the other way round. Each snapshot carries a schema version, so old captures
stay readable as the engine grows.

---

## Safety

The engine is pointed at real applications, sometimes shared ones. Two
independent gates must **both** pass before it clicks anything:

1. **The interaction type** must be on an allow-list of structurally
   reversible affordances — tab, disclosure, expander, menu. This is an
   allow-list, not a block-list, on purpose: its failure mode is "missed some
   coverage", never "clicked something destructive".
2. **The label** must not read as destructive. "Delete", "Pay", "Publish",
   "Send" and their relatives are refused *even when the type is safe*.

Anything failing either gate is **observed and recorded, not executed**.
Elements inside iframes are never clicked at all, because a frame-relative
selector resolved against the page could match a different element entirely.

Config can only ever make this *stricter*. You can add blocked words and
never-touch rules; there is no way to remove one, and enabling form submission
is rejected outright.

**Secrets** never enter the model: no request or response headers or bodies
are stored, and sensitive-looking query values are redacted. Saved sessions
live only on your machine and are gitignored.

**Politeness** is available for shared environments: request-rate caps,
concurrency limits, and robots.txt.

---

## Where AI fits — and where it doesn't

Every core capability is **deterministic and runs with zero tokens**: the
crawl, the analysis, the diff, the safety decisions, the test skeletons, the
source correlation.

An LLM is optional, off by default, and confined to *writing prose* — a
documentation overview, a test-strategy note, a change narrative. It is
handed findings that were already computed and asked to phrase them. It never
sits in the observation path and cannot alter a structured field. If a
provider fails or refuses, you get the deterministic text instead.

A build-time test fails the project if importing the engine so much as loads
an AI library.

---

## How far to trust the output

The engine distinguishes what it *saw* from what it *inferred*, and you
should too.

- **Observed facts** — elements, screens, navigation, network calls. These
  are recordings. Trust them, subject to readiness (above).
- **Structural inference** — components, regions, renames. Deterministic
  rules over observations. Reliable, but rules.
- **Correlation to source** — every link carries a confidence level
  (`confirmed` / `high` / `medium` / `low`) and the evidence behind it. An
  ambiguous match is reported as `low` *with its alternatives listed*, never
  silently resolved to a guess.
- **Generated prose** — labelled as AI-drafted whenever it is.

Two honest limitations worth knowing:

- **A diff needs two comparable snapshots.** Pages are matched by absolute
  URL, so this compares the same site over time — not staging against prod.
  And a live app with rotating content or timestamps will show those as
  changes, because they are.
- **Coverage is what the crawler could reach.** Pages behind a form
  submission, a paid action or a destructive confirmation will not appear.
  Absence from a capture is not evidence of absence from the product.

---

## Typical uses

- **Before a release** — capture, then diff against the previous capture to
  see exactly what moved.
- **Onboarding** — generate a UI reference for an app nobody documented.
- **QA planning** — get a screen inventory and candidate scenarios, and see
  which controls are destructive before anyone writes a test.
- **API discovery** — find what the frontend actually calls.
- **Audit** — a dated, versioned record of what an application looked like,
  with the scope config recording what was in and out of bounds.
