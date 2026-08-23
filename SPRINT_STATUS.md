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

`main` is at `e5d4da1`, carrying `G1`–`G7` and `H6`–`H9` + `X9`.

| Branch | Ahead of `main` | State |
| --- | --- | --- |
| `sprint/12-map` | 7 | **Complete, unmerged.** `M1`–`M4` + `H10`, at 0.22.0. |
| `sprint/2-field-validation` | 0 | Cut empty from the pre-0.19.0 trunk, far behind. Delete and re-cut when `QA.1`–`QA.4` start. |
| `sprint/4-deferred` | 0 | Same, and meant to stay empty — it exists so the deferral is visible. |

## Sprint 12 — map · `EPIC-MAP` · complete

| Item | Pri | Status |
| --- | --- | --- |
| `M1` Sitemap ingestion | P1 | ✅ |
| `M2` `map` command | P1 | ✅ |
| `M3` Scope dry-run | P2 | ✅ |
| `M4` Orphan & dead-end screens | P1 | ✅ |
| `H10` Capture an explicit URL list | P2 | ✅ |

`EPIC-MAP` is complete. With `EPIC-GOV` closed at 0.20.0, the backlog's two
finished epics are governance and discovery.

## Next actions, in order

1. **Merge `sprint/12-map`, then tag `v0.22.0`** — `RELEASING.md`.
2. **Run against a real product.** Everything since 0.20.0 was built for this.
   `GETTING_STARTED.md` §7 is the order to do it in; `crawl --dry-run` first.
3. **`QA.2`/`QA.3`/`QA.4`** — the real-portal validations. No fixture can stand
   in for them, and three sprints have now been justified by them.
4. Next sprint by `BRANCHING.md`'s *Cut after* order: `sprint/6-liveness`
   (`L1`–`L3`, `C3`).

## Version history note

**`v0.19.0` was never tagged and cannot be.** Sprint 8 was cut from sprint 1,
so sprint 1's version bump reached `main` on sprint 8's merge and no commit ever
declared `0.19.0` without also containing `G5`–`G7`. `v0.20.0` contains
`G1`–`G7`; the changelog says so.

Sprint 12 was cut from a **merged** `main` for exactly this reason, and
`v0.21.0` was tagged before it started.

## What earlier sprints did differently

Kept because each was a decision, and the next sprint should make them
deliberately or not at all.

- **Sprint 8 cut from sprint 1, not `main`** — cost the `v0.19.0` tag.
- **`G6`/`G7` shared one work branch**, against the one-item-per-branch rule.
  They landed as separate commits, but could not be reviewed separately.
- **Sprint 5 shipped five of ten planned items.** `M1`–`M4` + `H10` were
  deferred rather than half-landed, and became this sprint.
- **CodeQL blocked a push once**, for a fixture reading a URL from
  `location.search`. The alert was correct. CodeQL is *not* in the `full-ok`
  required set, so it reports without gating — worth changing if security
  alerts should block merges.
- **The `sprint/**` ruleset did not prevent branch deletion**, though
  `BRANCHING.md` says it does. Worth reconciling the doc with the ruleset.
