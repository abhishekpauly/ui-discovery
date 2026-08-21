# Releasing

A release is a **tag**. Everything else — the GitHub Release page, the notes,
the built artifacts — is rendered from things already in the repo, in keeping
with the principle that structured data is the source of truth and reports are
generated from it, never the reverse.

So there is exactly one manual step: push an annotated tag. The rest is
`.github/workflows/release.yml`.

---

## Two version numbers

They move independently and mean different things.

| Number | Lives in | Bumped when |
| --- | --- | --- |
| **Product version** | `pyproject.toml` `version`, `src/ui_discovery/__init__.py` `__version__` | every release |
| **Schema version** | `src/ui_discovery/__init__.py` `SCHEMA_VERSION` | only when the JSON shape breaks a reader of an old snapshot |

Additive fields do not break readers, so `SCHEMA_VERSION` has stayed `0.1.0`
across every release so far. That is correct, not neglect. Renaming or removing
a field, or changing what an existing field means, is what moves it.

`tests/test_release_hygiene.py` fails the build if the two product-version
locations disagree, if the declared version has no `CHANGELOG.md` entry, if the
newest changelog entry is not the declared version, or if `src/` has drifted
more than three commits past the last version bump.

## Versioning policy

`0.x`, so [SemVer](https://semver.org/) with the `0.` caveat: minor bumps may
add capabilities freely, and may change behaviour when the change is the point.

| Bump | For | Example |
| --- | --- | --- |
| **minor** `0.N.0` | a sprint's worth of capability; a new command; a new artifact in a capture | `0.18.0` — stage metrics and the run index |
| **patch** `0.N.P` | fixes and hardening with no new capability | `0.15.1` — fixes from the first real-portal run |
| **major** `1.0.0` | when the library surface and `SCHEMA_VERSION` are ones we will support | not yet |

One release per sprint is the intent: a sprint merges to `main` as a merge
commit, and that merge is what gets tagged. Hotfixes get their own patch
release off `main` without waiting for a sprint.

## Tags

- Format `vX.Y.Z` — the leading `v` is what `.github/workflows/release.yml`
  triggers on.
- **Annotated**, always (`git tag -a`). A lightweight tag carries no author, no
  date and no message, which is three things a release should not be missing.
- Release candidates are `vX.Y.Z-rc.N` and are published as pre-releases.
- **A tag is never moved and never deleted.** People and CI caches have already
  fetched it. If a release is wrong, ship the next patch version.

---

## Cutting a release

```bash
# 0. on main, with the sprint merged and CI green
git switch main && git pull

# 1. the version is already bumped and described — the sprint's last PR did it:
#      pyproject.toml            version = "0.19.0"
#      src/ui_discovery/__init__.py  __version__ = "0.19.0"
#      CHANGELOG.md              ## [0.19.0] — <title>
pytest -q

# 2. tag it, with the changelog's own heading as the message
git tag -a v0.19.0 -m "0.19.0 — <the CHANGELOG heading>"

# 3. push the tag. This is the release.
git push origin v0.19.0
```

Pushing the tag starts `release.yml`, which:

1. re-checks that the tag, `pyproject.toml` and `__version__` all say the same
   thing, and refuses the release if they do not;
2. runs the browser-free test lane against the tagged tree;
3. builds the sdist and wheel;
4. extracts that version's section from `CHANGELOG.md` (`scripts/changelog_section.py`);
5. creates the GitHub Release with those notes and attaches the artifacts,
   marking `-rc.` tags as pre-releases.

Nothing is typed twice. The release notes on GitHub and the changelog in the
repo cannot disagree, because there is only one of them.

## Hotfixes

```bash
git switch -c hotfix/h4-session-expiry-false-positive main
# ... fix, test, bump to the next PATCH, add a CHANGELOG entry ...
gh pr create --base main --fill
# after it merges:
git switch main && git pull
git tag -a v0.18.1 -m "0.18.1 — <the CHANGELOG heading>"
git push origin v0.18.1
```

Then merge `main` into every live `sprint/**` branch the same day, so the fix is
not silently reverted by the next sprint that lands.

---

## Writing the notes

The `CHANGELOG.md` entry *is* the release notes, so write it for someone who was
not in the room. The existing entries are the model: a heading that says what
changed in plain language, a paragraph on why it mattered, then
`### Added` / `### Changed` / `### Fixed` / `### Tests`.

- Lead with the problem, not the diff.
- Name the backlog IDs the release closes (`Closes O4 and O5, completing EPIC-OBS.`).
- Record the test delta (`+22 (576 → 598)`) — it is the cheapest evidence that
  the work was tested.
- Call out anything that changes an existing capture's shape, even additively.
  Someone has a parser.
- Say what was deliberately *not* done, and why. The deferrals in `[Unreleased]`
  are as much a part of the record as the additions.

## Release checklist

- [ ] Sprint merged to `main`; `main` green (`full-ok`).
- [ ] `PRODUCT_TRACKER.md` statuses flipped for everything the release closes.
- [ ] `CHANGELOG.md` has the new version as its **newest** entry.
- [ ] `pyproject.toml` and `__init__.py` agree on the version.
- [ ] `SCHEMA_VERSION` bumped **only** if the JSON shape broke old readers.
- [ ] `pytest -q` green locally.
- [ ] Annotated tag pushed; `release.yml` succeeded; Release page reads correctly.

---

## History note

Tags `v0.12.0` through `v0.18.0` were created retroactively, in
`sprint/3-devex`, on the commits that introduced each version — the repo had no
tags before that. `v0.1.0`–`v0.11.0` predate this git history entirely and exist
only as `CHANGELOG.md` entries.

`0.17.0` and `0.18.0` are a known irregularity: both shipped through PR #16 and
PR #17, and the commit that carries the `0.17.0` changelog entry already
declares `version = "0.18.0"`. The tags follow the changelog rather than the
version file, because the changelog is what describes each release. From
`v0.19.0` onward, one release is one tag on one merge commit.
