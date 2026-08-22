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

## Branch ledger · 2026-08-22

`main` is at `33964f6`. **Sprint 8 is merged** (PR #72) and no sprint is live.

| Branch | Tip | Ahead of `main` | State |
| --- | --- | --- | --- |
| `release/0.20.0` | — | 1 | Version bump + changelog section for the sprint 8 release. |
| `sprint/2-field-validation` | `7323c4d` | 0 | Cut empty from the pre-0.19.0 trunk, now 22 behind. Delete and re-cut when `QA.1`–`QA.4` actually start. |
| `sprint/4-deferred` | `7323c4d` | 0 | Same, and meant to stay empty — it exists so the deferral is visible. |

Deleted after merging: `sprint/1-governance` (#70), `sprint/8-redaction` (#72),
`feat/g2-safety-envelope`, `feat/g3-data-handling-posture`,
`feat/g4-retention`, `feat/g5-redact-the-model` (#71),
`docs/x7-backlog-expansion`.

## Sprint 8 — redaction · `EPIC-GOV` · ✅ closed

| Item | Pri | Status |
| --- | --- | --- |
| `G5` Redact the people out of the model | P0 | ✅ |
| `G6` Redact the people out of the screenshots | P0 | ✅ |
| `G7` Egress ledger | P1 | ✅ |

`EPIC-GOV` is complete: `G1`–`G7` all shipped.

## Next actions, in order

1. **Merge `release/0.20.0`, then tag `v0.20.0`** — `RELEASING.md`. Pushing the
   tag *is* the release; nothing else is manual.
2. **Re-run `QA.2`** against a real authenticated portal. It is the reason this
   sprint jumped the queue, and no fixture can validate redaction recall
   against real customer data. `QA.3` and `QA.4` follow from the same run.
3. **Cut `sprint/5-discovery`** from the new `main`, per `BRANCHING.md`'s
   *Cut after* order (`M1`–`M4`, `H6`–`H8`).

## Release history note

**`0.19.0` was never tagged, and cannot be retroactively.** Sprint 8 was cut
from `sprint/1-governance` rather than from `main`, so the commit bumping to
`0.19.0` reached `main` on sprint 8's merge (#72) rather than sprint 1's (#70).
At the sprint 1 merge the tree still declared `0.18.1`, and `release.yml`
refuses a tag whose version disagrees with `pyproject.toml` — so there is no
commit that could carry a `v0.19.0` tag without also containing `G5`–`G7`.
`v0.20.0` therefore contains `G1`–`G7`, and the `[0.19.0]` changelog section
stands as the record of the first half. `v0.18.1` is the previous tag.

## What sprint 8 did differently

Kept because each was a decision, and the next sprint should make them
deliberately or not at all.

- **Cut from sprint 1, not from `main`.** `G5` needed `G3`'s manifest posture,
  which had not reached `main`. It cost the `0.19.0` tag — see above. The rule
  in `BRANCHING.md` exists for exactly this.
- **`G5` merged as a merge commit, not a squash** (#71), against the
  work → sprint rule. `G6` and `G7` are one commit each, as intended.
- **`G6` and `G7` shared one work branch.** They landed as separate commits, so
  the history reads correctly and either is revertible alone — but they could
  not be reviewed separately, which is the cost the one-item-per-branch rule
  exists to prevent.
- **Two `G5` holes were closed inside `G6`.** `extract` and `probe` both read a
  scope config's `privacy` block and ignored it. Fixing them separately would
  have meant shipping `G6` on top of a known hole.
- **CodeQL blocked the first push.** An egress fixture read a URL from
  `location.search` into `img.src`. The alert was correct. Note that CodeQL is
  *not* in the `full-ok` required set, so it reported without gating — worth
  changing if security alerts should block merges.
