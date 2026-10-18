#!/usr/bin/env bash
# Bring the local CADAM clone up to date with upstream and re-apply our patch set (#1101, epic #1099).
#
#   scripts/cadam_local_sync.sh [--dry-run] [--no-install]
#
# 1. stash local (tracked) changes   2. fast-forward pull   3. re-apply docs/cadam/local-patches/*.diff in order
# 4. npm install.  Conflicts are reported, never forced: nothing is --reject'ed, --3way'd or reset. The stash is kept.
#
# CADAM_DIR   the clone (default /mnt/c/Users/Tony/Documents/GitHub/_meta/CADAM)
# PATCH_DIR   the patch set (default: docs/cadam/local-patches next to this script's repo)
# This script never commits or pushes in the clone.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CADAM_DIR="${CADAM_DIR:-/mnt/c/Users/Tony/Documents/GitHub/_meta/CADAM}"
PATCH_DIR="${PATCH_DIR:-$here/../docs/cadam/local-patches}"
dry=0; install=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry=1 ;;
    --no-install) install=0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 64 ;;
  esac
done

say() { printf '[cadam-sync] %s\n' "$*"; }
git_c() { git -C "$CADAM_DIR" "$@"; }

[ -d "$CADAM_DIR/.git" ] || { say "not a git clone: $CADAM_DIR"; exit 64; }
shopt -s nullglob
patches=("$PATCH_DIR"/*.diff)
[ "${#patches[@]}" -gt 0 ] || { say "no patches in $PATCH_DIR"; exit 64; }
for marker in MERGE_HEAD REBASE_HEAD CHERRY_PICK_HEAD; do
  git_c rev-parse -q --verify "$marker" >/dev/null && { say "a $marker is in progress in the clone; finish or abort it first"; exit 65; }
done

dirty="$(git_c status --porcelain --untracked-files=no)"
if [ "$dry" = 1 ]; then
  say "dry run: would stash ${dirty:+local changes, }fast-forward from upstream, apply ${#patches[@]} patch(es)$([ "$install" = 1 ] && echo ", npm install")"
  [ -n "$dirty" ] && say "local changes present: $(printf '%s\n' "$dirty" | wc -l) file(s)"
  exit 0
fi

stash_ref=""
if [ -n "$dirty" ]; then
  tag="cadam-local-sync-$(date +%Y%m%dT%H%M%S)"
  git_c stash push -m "$tag" >/dev/null || { say "stash failed; nothing changed"; exit 66; }
  stash_ref="$(git_c stash list --format='%gd %gs' | awk -v t="$tag" '$0 ~ t {print $1; exit}')"
  say "stashed local changes as '$tag' ($stash_ref); kept, never auto-dropped"
fi

restore_hint() {
  [ -n "$stash_ref" ] && say "your previous local changes are in $stash_ref (git -C '$CADAM_DIR' stash show -p $stash_ref)"
}

if ! git_c pull --ff-only; then
  say "fast-forward pull failed (local commits or diverged history). No patches applied."
  if [ -n "$stash_ref" ]; then
    git_c stash pop >/dev/null 2>&1 && say "restored your stashed changes" || restore_hint
  fi
  exit 2
fi

applied=0; skipped=0
for patch in "${patches[@]}"; do
  name="$(basename "$patch")"
  if git_c apply --check "$patch" 2>/dev/null; then
    git_c apply "$patch" && { say "applied $name"; applied=$((applied + 1)); continue; }
  fi
  if git_c apply --check -R "$patch" 2>/dev/null; then
    say "skipped $name (already present - upstream may have merged it)"; skipped=$((skipped + 1)); continue
  fi
  say "CONFLICT: $name does not apply to the updated tree. Not forced; later patches not attempted."
  say "applied so far: $applied. Fix the patch (re-cut it against $(git_c rev-parse --short HEAD)) and re-run."
  restore_hint
  exit 3
done

if [ "$install" = 1 ]; then
  say "npm install"
  (cd "$CADAM_DIR" && npm install --no-audit --no-fund) || { say "npm install failed"; restore_hint; exit 4; }
fi
say "done: $applied applied, $skipped already present, upstream at $(git_c rev-parse --short HEAD)"
restore_hint
exit 0
