# Contributing

The engine is deterministic, framework-agnostic and AI-free at runtime. Those
are not preferences; they are the properties that make a capture trustworthy.
`CLAUDE.md` states them in full and is the standing contract — read it first.

## The loop

1. **Pick one item** from `ROADMAP.md`, top to bottom. Every item has an ID
   (`O2`, `G1`, `X3`, `F6.4`) and a spec: goal, build, acceptance, files,
   effort, depends-on.
2. **Branch per item**, cut from the sprint branch that owns it:
   `git switch -c feat/o2-run-event-stream sprint/1-governance`.
   `BRANCHING.md` has the full model — what the sprint branches are, how they
   merge, and how several run in parallel without colliding.
3. **Build the smallest version that satisfies the acceptance criteria.**
4. **Test it against fixtures**, never a live site. `fixtures/**` is the
   regression surface; `tests/conftest.py` serves it over localhost.
5. **`pytest -q` green** before you open the PR.
6. **Update the tracking docs in the same change** — `PRODUCT_TRACKER.md`
   status, `CHANGELOG.md` entry, version bump in `pyproject.toml` and
   `__init__.py`. `tests/test_release_hygiene.py` fails the build if you don't.
7. **Open a PR** against the sprint branch. It needs Code Owner review; nobody
   merges their own. The sprint branch is what later merges to `main`, and that
   merge is what gets tagged — `RELEASING.md`.

## What CI does

| Workflow | When | Time | Gates merge |
| --- | --- | --- | --- |
| `fast` | every PR, and pushes to `main` / `sprint/**` | ~60s | yes |
| `full` | every PR, and pushes to `main` / `sprint/**` | ~4 min (6 shards) | yes, via `full-ok` |
| `capture` | every PR | ~2 min | no — it attaches the report |
| `labels` | `.github/labels.yml` changes | ~20s | no — it applies the label set |
| `release` | a `v*` tag is pushed | ~2 min | n/a — it publishes the Release |

`fast` runs `ruff` and the tests that need no browser, on Python 3.11 and 3.14.
`full` runs everything against a real Chromium, sharded across runners.
`capture` runs the pipeline against `fixtures/forms/` and uploads
`report.html` — download it to see whether a reporting change actually made the
output better.

Tests are marked `browser` automatically (see `tests/conftest.py`); you do not
need to mark anything by hand.

## Things that will get a PR rejected

- **Branching on a framework.** No `if react / angular / vue`. Only
  browser and web-standards signals.
- **An LLM in the observation, analysis or safety path.** AI is permitted only
  in V5, only under the optional `[semantic]` extra, only writing to separate
  files. `tests/test_no_ai_runtime.py` enforces it.
- **A capability with no config toggle**, or a config key nothing reads
  (`tests/test_no_dead_config.py` enforces the second).
- **Weakening the safety envelope.** Config can make the engine more cautious,
  never less. There is no way to remove a block word, and forms are never
  submitted.
- **Secrets, internal hostnames, or a real capture** in the repo. Scope configs
  naming a real target belong in an untracked `*.local.yaml`; the sanitised
  shape is `examples/authenticated-spa.scope.yaml`.
- **Persisting what someone typed.** Values are recorded only when they are a
  *choice* (checkbox, radio, select, number, date). Free text, email and
  passwords never are — in the model or the ARIA snapshot.

## Issues, labels and the board

Work is tracked in three places that each answer a different question, and none
of them restates another:

| Where | Answers |
| --- | --- |
| `ROADMAP.md` | what the item *is* — goal, build, acceptance, files, effort |
| `PRODUCT_TRACKER.md` | what exists today, at what version, with what coverage |
| GitHub issues + the **UI Discovery Engine** project | who is on it and where it has got to |

An issue points at its spec; it does not restate it. Use the **Epic**, **Story**
or **Bug** template — blank issues are off for exactly this reason.

Labels are defined in `.github/labels.yml` and applied by
`scripts/sync_labels.py`. Add a label by editing that file in a PR, not by
clicking in the web UI; the `labels` workflow shows the plan on the PR and
applies it on merge. An issue normally carries one label from each of `epic:`,
`area:`, `P0`–`P3`, `effort:` and `sprint:`. Where it sits in the workflow is
the project board's **Status** field, deliberately *not* a label.

Two labels are worth knowing about:

- **`principle-risk`** — the change brushes against a `CLAUDE.md`
  non-negotiable. That needs an explicit decision recorded in the issue, not a
  review nod.
- **`needs-real-run`** — fixtures cannot validate it. `QA.1`–`QA.4` are the
  standing examples.

## Setup

```bash
python -m venv .venv && .venv/Scripts/Activate.ps1   # POSIX: source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
pytest -q
```
