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
| **where the branches are today** | this file |

Regenerate the ledger with:

```bash
git fetch --all --prune
git rev-list --left-right --count origin/main...<branch>   # behind / ahead
git branch -r --no-merged origin/main
```

---

## Branch ledger · 2026-08-22

`main` is at `9668803`. It carries every governance item through `G4`, at
version 0.19.0.

| Branch | Tip | Ahead of `main` | State |
| --- | --- | --- | --- |
| `sprint/8-redaction` | `7341b89` | 9 | **Complete, unmerged.** `G5`–`G7` all ✅. Ready to PR to `main`. |
| `sprint/2-field-validation` | `7323c4d` | 0 | Cut, empty, and 21 behind. `QA.1`–`QA.4` are manual runs, not commits. |
| `sprint/4-deferred` | `7323c4d` | 0 | Cut, empty, and meant to stay that way — it exists so the deferral is visible. |

Merged and safe to delete: `sprint/1-governance` (PR #70),
`feat/g2-safety-envelope`, `feat/g3-data-handling-posture`, `feat/g4-retention`,
`feat/g5-redact-the-model` (PR #71), `docs/x7-backlog-expansion`,
`docs/correct-recorded-test-counts`.

`sprint/2-field-validation` and `sprint/4-deferred` were cut from the pre-0.19.0
trunk and have not been kept current. `BRANCHING.md` calls a sprint branch that
has not seen `main` in a week a fork; both are empty, so the cheap fix is to
delete and re-cut them when their work actually starts.

## Sprint 8 — redaction · `EPIC-GOV`

Status mirrors `PRODUCT_TRACKER.md`; that table is the source, this is the view.

| Item | Pri | Status |
| --- | --- | --- |
| `G5` Redact the people out of the model | P0 | ✅ merged to `sprint/8-redaction` |
| `G6` Redact the people out of the screenshots | P0 | ✅ |
| `G7` Egress ledger | P1 | ✅ |

## Next actions, in order

1. **Push `sprint/8-redaction` and open its PR to `main`** (merge commit, not a
   squash — the merge commit *is* the sprint). Suite is green locally at 776
   passed / 3 skipped.
2. **Decide the version.** `G5`–`G7` sit in `[Unreleased]` and `pyproject`
   still reads 0.19.0; `RELEASING.md` governs what the sprint merge is tagged.
3. **Re-run `QA.2`** against a real authenticated portal. It is the reason this
   sprint jumped the queue, and fixtures cannot validate redaction recall on
   real customer data.
4. Re-cut `sprint/5-discovery` from the new `main`, per `BRANCHING.md`'s
   *Cut after* order. `sprint/2-field-validation` and `sprint/4-deferred` are
   21 commits stale — delete and re-cut rather than merging `main` into empty
   branches.

## Notes on how sprint 8 was run

- **It was cut from `sprint/1-governance`, not from `main`.** `BRANCHING.md` says
  to cut it *after* sprint 1 merges; instead it branched off sprint 1 directly,
  because `G5` needed `G3`'s manifest posture and that had not reached `main`
  yet. Sprint 1 has since landed (PR #70) and `main` has been merged back in, so
  the two histories have rejoined and the shortcut cost nothing. Recorded because
  it was a decision, not an accident.
- **`G5` merged as a merge commit, not a squash.** `BRANCHING.md` asks for a
  squash at the work → sprint layer. PR #71 merged normally, so
  `sprint/8-redaction` carries `G5` as three commits rather than one. `G6` and
  `G7` are one commit each, as the rule intends.
- **`G6` and `G7` shared one work branch** (`feat/g6-redact-screenshots`),
  against the one-item-per-branch rule. They landed as two separate commits, so
  the sprint history reads correctly and either item is still revertible alone —
  but they could not have been reviewed separately, which is the cost the rule
  exists to prevent.
- **Two `G5` holes were closed here rather than in their own item.** `extract`
  and `probe` both read a scope config's `privacy` block and ignored it, so
  `redact_content: true` produced an unredacted `page.json`. Found while wiring
  `G6` through the same capture paths; fixing them separately would have meant
  shipping `G6` on top of a known hole.
