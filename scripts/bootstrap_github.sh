#!/usr/bin/env bash
#
# Put the GitHub side of this repo into the state the docs describe: labels
# from `.github/labels.yml`, annotated tags and Releases for every version in
# `CHANGELOG.md`, and the project board.
#
# Idempotent — everything checks before it creates, so a second run reports
# and changes nothing. It exists so the GitHub side is reproducible rather than
# remembered: if the repo is ever forked, transferred or rebuilt, this is how
# it gets its labels and its history back.
#
#   ./scripts/bootstrap_github.sh              # everything
#   ./scripts/bootstrap_github.sh labels       # one section
#   ./scripts/bootstrap_github.sh tags releases
#
# Needs `gh` authenticated with `repo` and `project` scopes:
#   gh auth login --scopes "repo,read:org,project,workflow"
set -euo pipefail

cd "$(dirname "$0")/.."

OWNER=abhishekpauly
REPO=ui-discovery
NWO="$OWNER/$REPO"
PROJECT_TITLE="UI Discovery Engine"
PY=${PYTHON:-python}

# version:commit — the commit that *introduced* each version, which in this
# repo is the release commit itself (the bump lands with the changelog entry).
# Deliberately starts at 0.12.0: 0.1.0-0.8.0 predate this git history and exist
# only as CHANGELOG entries.
TAG_MAP=(
  "0.12.0:00accf1"
  "0.13.0:749f54f"
  "0.14.0:a54433c"
  "0.15.0:012ba6f"
  "0.15.1:110e22c"
  "0.15.2:c0fbbba"
  "0.16.0:839e82d"
  "0.17.0:adbbc5a"   # declares 0.18.0 in pyproject — see RELEASING.md
  "0.18.0:f3a11e6"
)
LATEST=0.18.0

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }

# Only `labels`, `releases` and `project` talk to the API. Tagging is plain git,
# and is deliberately runnable without gh so a fresh clone can rebuild the tag
# history with nothing but a checkout.
need_gh() {
  command -v gh >/dev/null || { echo "gh is not on PATH"; exit 2; }
  gh auth status >/dev/null 2>&1 || {
    echo "gh is not authenticated. Run:"
    echo "  gh auth login --scopes \"repo,read:org,project,workflow\""
    exit 2
  }
}

do_labels() {
  say "Labels"
  need_gh
  "$PY" scripts/sync_labels.py --repo "$NWO"
}

do_tags() {
  say "Tags"
  git fetch --tags --quiet origin || true
  local created=()
  for entry in "${TAG_MAP[@]}"; do
    local version="${entry%%:*}" commit="${entry##*:}" tag="v${entry%%:*}"
    if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
      note "$tag already exists"
      continue
    fi
    local title
    title=$("$PY" scripts/changelog_section.py "$version" --title)
    # Date the tag as the commit it points at, so `git tag --sort=creatordate`
    # tells the truth about a retroactive backfill instead of claiming every
    # release happened this afternoon.
    local when
    when=$(git show -s --format=%cI "$commit")
    GIT_COMMITTER_DATE="$when" git tag -a "$tag" "$commit" -m "$title"
    note "created $tag -> $commit  ($title)"
    created+=("$tag")
  done
  if [ ${#created[@]} -gt 0 ]; then
    git push origin "${created[@]}"
    note "pushed ${#created[@]} tag(s)"
  fi
}

do_releases() {
  say "Releases"
  need_gh
  local notes title flags
  notes=$(mktemp)
  for entry in "${TAG_MAP[@]}"; do
    local version="${entry%%:*}" tag="v${entry%%:*}"
    if gh release view "$tag" --repo "$NWO" >/dev/null 2>&1; then
      note "$tag already released"
      continue
    fi
    "$PY" scripts/changelog_section.py "$version" -o "$notes"
    title=$("$PY" scripts/changelog_section.py "$version" --title)
    flags=(--repo "$NWO" --title "$title" --notes-file "$notes" --verify-tag)
    # Exactly one release is `latest`; the backfilled ones are history.
    if [ "$version" = "$LATEST" ]; then flags+=(--latest); else flags+=(--latest=false); fi
    gh release create "$tag" "${flags[@]}"
    note "released $tag"
  done
  rm -f "$notes"
}

do_project() {
  say "Project board"
  need_gh
  local number
  number=$(gh project list --owner "$OWNER" --format json \
           | "$PY" -c "import json,sys;print(next((p['number'] for p in json.load(sys.stdin)['projects'] if p['title']=='$PROJECT_TITLE'),''))")
  if [ -z "$number" ]; then
    gh project create --owner "$OWNER" --title "$PROJECT_TITLE" >/dev/null
    number=$(gh project list --owner "$OWNER" --format json \
             | "$PY" -c "import json,sys;print(next(p['number'] for p in json.load(sys.stdin)['projects'] if p['title']=='$PROJECT_TITLE'))")
    note "created project #$number"
  else
    note "project #$number already exists"
  fi

  gh project link "$number" --owner "$OWNER" --repo "$NWO" >/dev/null 2>&1 \
    && note "linked to $NWO" || note "already linked to $NWO"

  # Only fields the labels cannot express. Epic, area, priority, effort and
  # sprint are labels (see .github/labels.yml) and show in the board's Labels
  # column; duplicating them as fields would create a second, disagreeing copy.
  local have
  have=$(gh project field-list "$number" --owner "$OWNER" --format json \
         | "$PY" -c "import json,sys;print('\n'.join(f['name'] for f in json.load(sys.stdin)['fields']))")
  add_field() {  # add_field NAME TYPE [options]
    if grep -qxF "$1" <<<"$have"; then note "field '$1' exists"; return; fi
    if [ -n "${3:-}" ]; then
      gh project field-create "$number" --owner "$OWNER" --name "$1" \
        --data-type "$2" --single-select-options "$3" >/dev/null
    else
      gh project field-create "$number" --owner "$OWNER" --name "$1" --data-type "$2" >/dev/null
    fi
    note "created field '$1'"
  }
  add_field "Backlog ID" TEXT
  add_field "Target release" TEXT

  note "Status options are set from the board UI; see BRANCHING.md for what each means."
  note "Board: https://github.com/users/$OWNER/projects/$number"
}

main() {
  local sections=("$@")
  [ ${#sections[@]} -eq 0 ] && sections=(labels tags releases project)
  for section in "${sections[@]}"; do
    case "$section" in
      labels)   do_labels ;;
      tags)     do_tags ;;
      releases) do_releases ;;
      project)  do_project ;;
      *) echo "unknown section: $section (labels|tags|releases|project)"; exit 2 ;;
    esac
  done
  say "Done"
}

main "$@"
