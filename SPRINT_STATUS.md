# Sprint status

Where the branches actually are, right now. Nothing else.

This file answers one question no other document does: **what is on which branch,
and what merges next.** It deliberately does not explain the branching model, the
sequencing of sprints, or what any backlog item is — those belong to
`BRANCHING.md` and `ROADMAP.md`, and a second copy of either is the copy that
goes stale.

| For | Read |
| --- | --- |
| how sprints are cut, merged and ordered | `BRANCHING.md` |
| what an item is — goal, build, acceptance | `ROADMAP.md` |
| what exists today, at what version | `PRODUCT_TRACKER.md` |
| how a release is cut | `RELEASING.md` |
| **where the branches are today** | this file |

```bash
git fetch --all --prune
git branch -r --format='%(refname:short)'
git rev-list --left-right --count origin/main...<branch>   # behind / ahead
```

---

## Branch ledger · 2026-08-25

`main` is at `f1af1d4`. **No sprint is live and nothing is in flight.**

| Branch | Ahead of `main` | State |
| --- | --- | --- |
| `sprint/2-field-validation` | 0 | Cut empty from the pre-0.19.0 trunk, long behind. Delete and re-cut when `QA.1`–`QA.4` start. |
| `sprint/4-deferred` | 0 | Same, and meant to stay empty — it exists so the deferral is visible. |

Released: **v0.20.0**, **v0.21.0**, **v0.22.0**, **v0.23.0**. `v0.19.0` was never
tagged and cannot be — see *Version history* below.

`0.23.1` is uncut: two commits touch `src/` since the 0.23.0 bump, under the
drift guard's limit of three, so nothing forces it.

## Epics closed

| Epic | Items | Released |
| --- | --- | --- |
| `EPIC-GOV` | `G1`–`G7` | 0.19.0 (untagged) · 0.20.0 |
| `EPIC-MAP` | `M1`–`M4`, `H6`–`H8` | 0.21.0 · 0.22.0 |

Plus `H9`, `H10`, `X9` (pulled forward) and `H12` (unplanned — see below).

## Next actions, in order

1. **Run against a real product again.** Every defect found in the last three
   days came from doing so, and none from the 985-test suite. Two runs worth
   having: a full `pipeline` pass with `collapse_instances: true` (only the
   `--no-probe --no-screenshots` path has been verified on a real target), and
   a second portal, because everything is currently tuned to one product's shape.
2. **`QA.3` (#13)** is arguably answerable now — probing measured at 27.2% and
   33.3% of crawl time on two real runs. The acceptance is a number to read
   rather than a judgement to make, and the number exists.
3. **`X8` (#38)** — open, but `sprint/3-devex` merged as PR #68 and
   `PRODUCT_GUIDE.md` has no decision table. Either the sprint landed incomplete
   or the tracker is stale.
4. **`sprint/6-liveness`** (`L1`–`L3`, `C3`) per `BRANCHING.md`'s *Cut after*
   order — `L1` has a concrete case: a capture reported `/platform/rag` as a
   screen when it was a redirect to the dashboard, and nothing said so.
5. **`sprint/7-reachability`** (`I1`–`I3`) — modals are structurally unreachable
   because `button` is not on the safety allow-list, by design. Recipes are the
   designed answer, and a real Manage Agent capture is the case for them.

## Version history

**`v0.19.0` was never tagged and cannot be.** Sprint 8 was cut from
`sprint/1-governance` rather than from `main`, so sprint 1's version bump reached
`main` on sprint 8's merge (#72) rather than its own (#70). At the sprint 1 merge
the tree still declared `0.18.1`, and `release.yml` refuses a tag whose version
disagrees with `pyproject.toml` — so no commit could carry a `v0.19.0` tag
without also containing `G5`–`G7`. `v0.20.0` therefore contains `G1`–`G7`.

Every sprint since has been cut from a **merged** `main`, and each release has
been tagged before the next sprint started. That ordering is the whole fix.

## What real runs found that the suite could not

Recorded because it is the strongest argument in this repo for `EPIC-QA`. Three
defects of **one shape** — a feature built before a later one and never wired to
it — none visible to a fixture-only suite:

- `G7`'s egress ledger predated `H6` and ignored its subdomain policy, reporting
  the product's own API subdomain as a third party.
- `M2`'s map predated `H10` and ignored its URL list, so `--dry-run` reported one
  URL where the crawl captured seven.
- `H12`'s own config key was read by nothing, so the feature shipped dead in
  0.23.0 through the only path an operator uses.

Plus: a config pointing at a route that redirects, and screenshot failures
swallowed silently. `test_no_dead_config` now checks that a crawl-shaping value
*reaches the crawl* rather than that a name exists somewhere in `src/`.

## Practices worth keeping

- **One item per work branch**, squashed into its sprint. `G6`/`G7` shared a
  branch and could not be reviewed separately.
- **Tag before the next sprint starts.** See *Version history*.
- **Run the suite in the foreground.** Detached runs on at least one machine came
  in at 42 min, 2h10m, 4h59m and 15h16m against 5–9 minutes per sixth. Every one
  passed — it is wall-clock, not correctness — but it makes the suite useless as
  a gate.
- **CodeQL reports without gating**, and the `sprint/**` ruleset did not prevent
  branch deletion, though `BRANCHING.md` says it does. Worth reconciling the doc
  with the actual rulesets.
