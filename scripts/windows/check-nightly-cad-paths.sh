#!/usr/bin/env bash
# Fail-fast path preflight for run-nightly-cad-arena.ps1.
#
# Usage: check-nightly-cad-paths.sh Name=/wsl/path [Name=/wsl/path ...]
#
# Each argument is a `ParamName=/path` pair. Every path is checked for
# existence; every missing one is reported with its parameter name so the
# operator can fix the scheduled-task arguments in one pass. Exits non-zero
# if any path is missing. Runs inside WSL; invoked by the Windows runner
# before it launches the arena, and directly by the WSL test suite.
set -u

missing=0
for spec in "$@"; do
    name="${spec%%=*}"
    path="${spec#*=}"
    if [ ! -e "$path" ]; then
        echo "ERROR: -${name} path does not exist: '${path}'" >&2
        missing=1
    fi
done

if [ "$missing" -ne 0 ]; then
    {
        echo "Nightly CAD arena aborted before launch: fix the missing path(s) above."
        echo "Canonical repo checkout: /mnt/c/Users/<you>/Documents/GitHub/makerbench_ecosystem/makerbench-hwe"
        echo "See docs/NIGHTLY_CAD_TASK.md for the canonical invocation."
    } >&2
    exit 1
fi
