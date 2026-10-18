#!/usr/bin/env bash
# Interactive step-by-step live CAD session (SolidWorks or Fusion), wired like the nightly driver.
#
#   scripts/live-cad-session.sh --cad solidworks --runtime codex --workdir <folder> [--model ID]
#                               [--secrets <env file>] [--prompt-file <md>] [--dry-run]
#
# The secrets file is read inside the launcher process and never echoed; bridge env reaches the
# connector by name only. See docs/NIGHTLY_CAD_TASK.md "Fixing a design step by step".
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m makerbench.cli arena live-cad-session "$@"
