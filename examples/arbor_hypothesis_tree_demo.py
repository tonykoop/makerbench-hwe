#!/usr/bin/env python3
"""Arbor-style hypothesis-tree demo (issue #162).

A self-contained, oracle-free run of the scientific-method loop in
``makerbench.hypothesis_tree`` over a toy CAD optimization: thin a vented cover
plate to minimize mass while keeping it printable (min wall thickness) and rigid
(no excessive deflection under a fixed load).

The executor here is a *deterministic analytic stub* standing in for a real CAD /
physics rollout (OpenSCAD compile + trimesh mass, or a StreamForce sim in an
isolated worktree). Swap it for the real thing and the Coordinator/report code is
unchanged. Run it::

    python examples/arbor_hypothesis_tree_demo.py
"""

from __future__ import annotations

from makerbench.hypothesis_tree import (
    Coordinator,
    HypothesisEvidence,
    IdeaNode,
    ResearchContract,
    SuccessMetric,
    dashboard_summary,
    render_report_markdown,
    run_tree,
)

# Fixed plate footprint and material (public fixtures).
PLATE_AREA_MM2 = 120.0 * 80.0          # 120 x 80 mm cover
DENSITY_G_PER_MM3 = 1.24e-3            # PETG
LOAD_N = 5.0                           # service load for the deflection proxy


def _measure(thickness_mm: float) -> dict[str, float]:
    """Analytic stand-in for compile+grade: mass, min wall, and a deflection proxy."""
    mass_g = PLATE_AREA_MM2 * thickness_mm * DENSITY_G_PER_MM3
    # Deflection of a uniform plate scales ~ 1/t^3 under fixed load.
    deflection_mm = LOAD_N * 1.5 / (thickness_mm ** 3)
    return {
        "mass_g": round(mass_g, 3),
        "min_wall_mm": round(thickness_mm, 3),
        "max_deflection_mm": round(deflection_mm, 4),
    }


def make_executor():
    """Build a deterministic executor that reads the candidate thickness off the node."""

    def executor(contract: ResearchContract, node: IdeaNode) -> HypothesisEvidence:
        # Each hypothesis title encodes its candidate thickness, e.g. "wall=1.0mm".
        thickness = float(node.title.split("=")[1].rstrip("mm"))
        measurements = _measure(thickness)
        return HypothesisEvidence(
            commands=[
                f"openscad -D wall={thickness} -o cover.stl cover.scad",
                "python -m makerbench grade --artifact cover.stl",
            ],
            geometry_params={"wall_mm": thickness},
            measurements=measurements,
            artifacts=["renders/cover_iso.png"],
            tokens_spent=4200,
            runtime_seconds=18.0,
        )

    return executor


def heldout_evaluator(contract: ResearchContract, node: IdeaNode) -> bool:
    """Stub held-out gate: re-checks dev metrics with a tighter mass ceiling.

    In a real maintainer run this re-grades on the official held-out seeds; the
    public demo just simulates a stricter hidden bar so promotion is non-trivial.
    """
    meas = node.evidence.measurements if node.evidence else {}
    return meas.get("mass_g", 1e9) <= 38.0 and meas.get("min_wall_mm", 0.0) >= 0.8


def build_contract() -> ResearchContract:
    return ResearchContract(
        objective="Minimize vented-cover mass while staying printable and rigid",
        task_id="vented_cover_plate",
        fabrication_domain="enclosure",
        fixtures={"area_mm2": PLATE_AREA_MM2, "density_g_per_mm3": DENSITY_G_PER_MM3, "load_N": LOAD_N},
        allowed_tools=["openscad", "trimesh", "streamforce"],
        success_metrics=[
            SuccessMetric(name="mass_g", target=40.0, direction="minimize", unit="g"),
            SuccessMetric(name="min_wall_mm", target=0.8, direction="maximize", unit="mm"),
            SuccessMetric(name="max_deflection_mm", target=2.0, direction="minimize", unit="mm"),
        ],
        token_budget=40_000,
        runtime_budget_seconds=300.0,
    )


def main() -> None:
    coord = Coordinator(build_contract())

    # Depth-1 directions, Depth-2 concrete testable hypotheses (candidate walls).
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

    report = run_tree(coord, make_executor(), heldout_evaluator=heldout_evaluator)

    print(render_report_markdown(report))
    print("\n--- dashboard summary ---")
    summary = dashboard_summary(report)
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
