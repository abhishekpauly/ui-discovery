## What and why

<!-- What changes, and what problem it solves. Link the backlog item. -->

Item: <!-- e.g. O2, G1, X3, F6.4 — see ROADMAP.md / PRODUCT_TRACKER.md -->

## Checks

- [ ] `pytest -q` green locally
- [ ] README / RUNBOOK updated if behaviour changed
- [ ] `PRODUCT_TRACKER.md` status and Tests cell updated
- [ ] `CHANGELOG.md` entry added, `pyproject.toml` + `__init__.py` version bumped
- [ ] `SCHEMA_VERSION` bumped **only** if the JSON shape broke readers of old
      snapshots (additive fields do not count)

## Principles

- [ ] No framework branching (`if react / angular / vue`)
- [ ] No LLM in the observation, analysis or safety path
- [ ] New capability has a config toggle and a sensible default
- [ ] No secrets, internal hostnames or real captures added to the repo
