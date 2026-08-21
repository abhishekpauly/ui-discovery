# Contributing

The engine is deterministic, framework-agnostic and AI-free at runtime. Those
are not preferences; they are the properties that make a capture trustworthy.
`CLAUDE.md` states them in full and is the standing contract — read it first.

## The loop

1. **Pick one item** from `ROADMAP.md`, top to bottom. Every item has an ID
   (`O2`, `G1`, `X3`, `F6.4`) and a spec: goal, build, acceptance, files,
   effort, depends-on.
2. **Branch per item**: `feat/o2-run-event-stream`.
3. **Build the smallest version that satisfies the acceptance criteria.**
4. **Test it against fixtures**, never a live site. `fixtures/**` is the
   regression surface; `tests/conftest.py` serves it over localhost.
5. **`pytest -q` green** before you open the PR.
6. **Update the tracking docs in the same change** — `PRODUCT_TRACKER.md`
   status, `CHANGELOG.md` entry, version bump in `pyproject.toml` and
   `__init__.py`. `tests/test_release_hygiene.py` fails the build if you don't.
7. **Open a PR.** It needs Code Owner review; nobody merges their own.

## What CI does

| Workflow | When | Time | Gates merge |
| --- | --- | --- | --- |
| `fast` | every push and PR | ~60s | yes |
| `full` | every PR and `main` | ~4 min (6 shards) | yes, via `full-ok` |
| `capture` | every PR | ~2 min | no — it attaches the report |

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

## Setup

```bash
python -m venv .venv && .venv/Scripts/Activate.ps1   # POSIX: source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
pytest -q
```
