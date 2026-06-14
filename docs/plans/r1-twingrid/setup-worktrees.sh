#!/usr/bin/env bash
# Reproducible TwinGrid worktree setup. Creates 18 worktrees (9 personas x A/B)
# on persistent disk from persona-map.tsv. Side A = Claude Opus grid, B = codex gpt-5.5 grid.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
GH="${GH_ROOT:-/mnt/c/Users/Tony/Documents/GitHub}"
WT="${WT_ROOT:-/home/tony/hwe-wt}"
mkdir -p "$WT"
tail -n +2 "$HERE/persona-map.tsv" | grep -v '^#' | while IFS=$'\t' read -r persona slug remote localdir issues title; do
  [ -n "$persona" ] || continue
  base="$GH/$localdir"
  git -C "$base" worktree prune 2>/dev/null || true
  for side in a b; do
    dest="$WT/${persona}-${side}"; br="${persona}/${side}-r1-${slug}"
    git -C "$base" branch -D "$br" 2>/dev/null || true
    rm -rf "$dest" 2>/dev/null || true
    git -C "$base" worktree add -b "$br" "$dest" main >/dev/null 2>&1 \
      && echo "OK  ${persona}-${side}  $localdir  $br" || echo "FAIL ${persona}-${side}"
  done
done
