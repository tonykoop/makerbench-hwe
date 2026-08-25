#!/usr/bin/env bash
# Reproducible TwinGrid worktree setup. Creates 18 worktrees (9 personas x A/B)
# on persistent disk from persona-map.tsv. Side A = Claude Opus grid, B = codex gpt-5.5 grid.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
GH="${GH_ROOT:-/mnt/c/Users/Tony/Documents/GitHub}"
WT="${WT_ROOT:-/home/tony/hwe-wt}"
mkdir -p "$WT"

# Preflight the entire grid before creating anything. A retained worktree or
# branch may contain uncommitted sprint material, so setup must never "clean up"
# a collision on the operator's behalf.
tail -n +2 "$HERE/persona-map.tsv" | grep -v '^#' | while IFS=$'\t' read -r persona slug remote localdir issues title; do
  [ -n "$persona" ] || continue
  base="$GH/$localdir"
  if ! git -C "$base" rev-parse --git-dir >/dev/null 2>&1; then
    echo "REFUSE: repository is unavailable: $base" >&2
    exit 1
  fi
  if ! git -C "$base" show-ref --verify --quiet refs/heads/main; then
    echo "REFUSE: local main branch is unavailable: $base" >&2
    exit 1
  fi
  for side in a b; do
    dest="$WT/${persona}-${side}"; br="${persona}/${side}-r1-${slug}"
    if git -C "$base" show-ref --verify --quiet "refs/heads/$br"; then
      echo "REFUSE: branch already exists: $localdir $br" >&2
      exit 1
    fi
    if [ -e "$dest" ]; then
      echo "REFUSE: destination already exists: $dest" >&2
      exit 1
    fi
    if git -C "$base" worktree list --porcelain | grep -Fqx -- "worktree $dest"; then
      echo "REFUSE: destination is already registered: $dest" >&2
      exit 1
    fi
  done
done

tail -n +2 "$HERE/persona-map.tsv" | grep -v '^#' | while IFS=$'\t' read -r persona slug remote localdir issues title; do
  [ -n "$persona" ] || continue
  base="$GH/$localdir"
  for side in a b; do
    dest="$WT/${persona}-${side}"; br="${persona}/${side}-r1-${slug}"
    git -C "$base" worktree add -b "$br" "$dest" main >/dev/null
    echo "OK  ${persona}-${side}  $localdir  $br"
  done
done
