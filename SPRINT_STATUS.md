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

Regenerate the ledger with:

```bash
git fetch --all --prune
git branch -r --format='%(refname:short)'
git rev-list --left-right --count origin/main...<branch>   # behind / ahead
```

---

## Branch ledger · 2026-08-23

`main` is at `554fb02`, tagged **v0.20.0** — every governance item, `G1`–`G7`.

| Branch | Ahead of `main` | State |
| --- | --- | --- |
| `sprint/5-discovery` | 6 | **Complete as scoped, unmerged.** `H6`–`H9` + `X9`. See *Scope changed mid-sprint*. |
| `sprint/2-field-validation` | 0 | Cut empty from the pre-0.19.0 trunk, now far behind. Delete and re-cut when `QA.1`–`QA.4` start. |
| `sprint/4-deferred` | 0 | Same, and meant to stay empty — it exists so the deferral is visible. |

## Sprint 5 — discovery · `EPIC-MAP` (partial)

| Item | Pri | Status |
| --- | --- | --- |
| `H6` Subdomain policy | P2 | ✅ |
| `H7` External links recorded, never followed | P2 | ✅ |
| `H8` Crawl failure ledger | P2 | ✅ |
| `H9` Exclude the furniture | P2 | ✅ |
| `X9` Capture profiles | P2 | ✅ |
| `M1` Sitemap ingestion | P1 | 📋 deferred |
| `M2` `map` command | P1 | 📋 deferred |
| `M3` Scope dry-run | P2 | 📋 deferred |
| `M4` Orphan & dead-end screens | P1 | 📋 deferred |
| `H10` Capture an explicit URL list | P2 | 📋 deferred |

## Next actions, in order

1. **Merge `sprint/5-discovery`, then tag `v0.21.0`** — `RELEASING.md`.
2. **Run against a real product.** That was the point of this sprint: `H6` is
   the fix that decides whether a multi-subdomain portal captures at all, and
   `H8` is what tells you whether the result is complete. Read
   `run.json`'s `metrics.probe_share_of_crawl_pct` before tuning anything.
3. **Cut `sprint/12-map`** for the deferred `M1`–`M4` + `H10`.

## Scope changed mid-sprint

This sprint was cut as `M1`–`M4` + `H6`–`H8`, with `X9`, `H9` and `H10` pulled
forward from sprints 9 and 10 because they are small, they share
`config.py`/`crawler.py` with the rest, and they are what a first real run
actually needs.

It shipped as `H6`–`H9` + `X9`. The `M` series went out of scope rather than in
half-landed: `M1` and `M2` want two new modules and a new CLI between them, and
`M3` depends on `M2`, `M4` on `M1`, and `H10` on both `M2` and `H8`. That is a
second sprint's worth of work, and a release of finished items beats a release
with a half-written `discovery.py` in it.

The five that shipped stand on their own — none of them depends on the `M`
series — so nothing here is left in an intermediate state.

## What earlier sprints did differently

Kept because each was a decision, and the next sprint should make them
deliberately or not at all.

- **Sprint 8 was cut from sprint 1, not from `main`.** It cost the `v0.19.0`
  tag: sprint 1's version bump reached `main` on sprint 8's merge, so no commit
  ever declared `0.19.0` without also containing `G5`–`G7`. `v0.20.0` contains
  `G1`–`G7`, and the changelog says so.
- **`G6` and `G7` shared one work branch**, against the one-item-per-branch
  rule. They landed as separate commits, so the history reads correctly, but
  they could not be reviewed separately.
- **CodeQL blocked a push once**, for a fixture that read a URL out of
  `location.search`. The alert was correct. CodeQL is *not* in the `full-ok`
  required set, so it reports without gating — worth changing if security
  alerts should block merges.
- **The `sprint/**` ruleset did not prevent branch deletion**, though
  `BRANCHING.md` says it does. Worth reconciling the doc with the actual
  ruleset.
