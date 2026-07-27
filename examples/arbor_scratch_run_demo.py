#!/usr/bin/env python3
"""Arbor hypothesis-tree demo with isolated scratch runs + persistence (#162).

Same toy CAD optimization as ``arbor_hypothesis_tree_demo.py`` (thin a vented
cover plate to minimize mass while staying printable and rigid), but each
hypothesis executor attempt runs in its **own isolated scratch directory** via
``scratch_run_executor``, and the full audit trail is written out with
``persist_report``.

Everything is deterministic and offline: the executor is the analytic stub from
the sibling demo. Swap it for a real OpenSCAD/worktree rollout and nothing else
changes. Run it::

    python examples/arbor_scratch_run_demo.py [out_dir]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from makerbench.hypothesis_tree import (
    Coordinator,
    dashboard_summary,
    persist_report,
    run_tree,
    scratch_run_executor,
)

from examples.arbor_hypothesis_tree_demo import (
    build_contract,
    heldout_evaluator,
    make_executor,
)


def build_tree() -> Coordinator:
    coord = Coordinator(build_contract())
    thin = coord.add_direction("Thin uniform wall")
    for t in (0.6, 0.8, 1.0):
        coord.add_hypothesis(
            thin.id, f"wall={t}mm",
            statement=f"A uniform {t} mm wall meets mass, printability, and deflection targets.",
        )
    ribbed = coord.add_direction("Ribbed wall (thicker base, stiffened)")
    for t in (1.2, 1.6):
        coord.add_hypothesis(
            ribbed.id, f"wall={t}mm",
            statement=f"A {t} mm ribbed wall trades mass for deflection headroom.",
        )
    return coord


def run(scratch_root: Path, out_dir: Path) -> dict:
    coord = build_tree()
    executor = scratch_run_executor(make_executor(), root_dir=scratch_root)
    report = run_tree(coord, executor, heldout_evaluator=heldout_evaluator)
    paths = persist_report(report, out_dir)
    return {"report": report, "paths": paths, "dashboard": dashboard_summary(report)}


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="arbor_out_"))
    scratch_root = out_dir / "scratch"
    result = run(scratch_root, out_dir)
    print(f"scratch runs: {scratch_root}")
    for key, path in result["paths"].items():
        print(f"{key}: {path}")
    print(f"\npromoted best: {result['dashboard']['best_node_id']}")


if __name__ == "__main__":  # pragma: no cover
    main()
