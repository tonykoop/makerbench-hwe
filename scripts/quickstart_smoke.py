#!/usr/bin/env python3
"""Run the QUICKSTART stub rung verbatim so the doc cannot rot (#844).

Extracts the first bash block under "## Rung 1" and the fenced "Expected" output that
follows it from docs/QUICKSTART.md, runs the commands from the current checkout (the
doc's `git clone` / `cd` lines are skipped: CI is already in the clone), and checks the
real output contains every expected line (whitespace-normalised, order kept).
Usage: python scripts/quickstart_smoke.py [--dry-run]   (exit 0 pass, 1 mismatch/failure)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent / "docs" / "QUICKSTART.md"
SKIP = re.compile(r"^(git clone|cd makerbench-hwe)\b")


def extract(text: str) -> tuple[list[str], list[str]]:
    """Return (commands, expected_lines) for Rung 1."""
    m = re.search(r"^## Rung 1\b.*?$(.*?)^## Rung 2\b", text, re.S | re.M)
    if not m:
        raise SystemExit("quickstart_smoke: Rung 1 section not found")
    blocks = re.findall(r"```(\w*)\n(.*?)```", m.group(1), re.S)
    bash = next((b for lang, b in blocks if lang == "bash"), None)
    if bash is None or len(blocks) < 2:
        raise SystemExit("quickstart_smoke: need a bash block followed by an expected-output block")
    expected = next(b for lang, b in blocks if lang == "")
    joined = re.sub(r"\\\n\s*", "", bash)  # fold line continuations
    cmds = [ln for ln in joined.splitlines() if ln.strip() and not SKIP.match(ln.strip())]
    lines = [" ".join(ln.split()) for ln in expected.splitlines() if ln.strip()]
    return cmds, lines


def _flat(s: str) -> str:
    """Drop table box-drawing characters and collapse all whitespace, so rich's
    terminal-width wrapping and borders do not matter."""
    return " ".join(re.sub(r"[\u2500-\u257f]", " ", s).split())


def missing(output: str, expected: list[str]) -> list[str]:
    """Expected lines not found, in order, in the flattened output."""
    have, pos, out = _flat(output), 0, []
    for want in expected:
        i = have.find(_flat(want), pos)
        if i < 0:
            out.append(want)
        else:
            pos = i + len(_flat(want))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print the extracted commands only")
    args = ap.parse_args(argv)
    cmds, expected = extract(DOC.read_text(encoding="utf-8"))
    print("commands:\n  " + "\n  ".join(cmds))
    if args.dry_run:
        print("expected:\n  " + "\n  ".join(expected))
        return 0
    script = "set -euo pipefail\n" + "\n".join(cmds) + "\n"
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=DOC.parent.parent)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode:
        print(f"FAIL quickstart_smoke: commands exited {r.returncode}", file=sys.stderr)
        return 1
    gone = missing(r.stdout, expected)
    if gone:
        print("FAIL quickstart_smoke: expected output not found:\n  " + "\n  ".join(gone), file=sys.stderr)
        return 1
    print("ok   quickstart_smoke: Rung 1 output matches QUICKSTART.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
