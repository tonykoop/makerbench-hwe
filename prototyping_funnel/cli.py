"""Command-line interface for the deflationary prototyping funnel.

    makerbench-protofunnel path/to/part.step \\
        --name "motor-mount" \\
        --material-volume-mm3 12000 \\
        --removed-volume-mm3 3000 \\
        --hole-count 6 \\
        --sale-price 99.0 \\
        --quantity 200

Outputs a JSON or human-readable report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="makerbench-protofunnel",
        description=(
            "Run a part through the deflationary prototyping funnel: "
            "concept → DFM → quote (multi-process bridge) → ROI. "
            "Deterministic, offline-safe, no CAD kernel required."
        ),
    )
    p.add_argument("artifact", help="Path to geometry file (STEP, STL, SVG, …)")
    p.add_argument("--name", default="", help="Design name label for the report")
    p.add_argument("--material-volume-mm3", type=float, default=0.0)
    p.add_argument("--removed-volume-mm3", type=float, default=0.0)
    p.add_argument("--sheet-area-mm2", type=float, default=0.0)
    p.add_argument("--cut-length-mm", type=float, default=0.0)
    p.add_argument("--bend-count", type=int, default=0)
    p.add_argument("--hole-count", type=int, default=0)
    p.add_argument("--print-time-minutes", type=float, default=0.0)
    p.add_argument("--support-volume-mm3", type=float, default=0.0)
    p.add_argument("--sheet-nesting-yield", type=float, default=1.0)
    p.add_argument(
        "--dfm-pass-threshold",
        type=float,
        default=80.0,
        help="Minimum DFM score to proceed past the DFM gate (default: 80.0)",
    )
    p.add_argument(
        "--sale-price",
        type=float,
        default=None,
        metavar="USD",
        help="Unit sale price in USD — enables ROI/breakeven projection",
    )
    p.add_argument(
        "--quantity",
        type=int,
        default=1,
        help="Production quantity for ROI analysis (default: 1)",
    )
    p.add_argument(
        "--concept-cost",
        type=float,
        default=0.0,
        metavar="USD",
        help="CAD/design NRE cost for the concept stage (default: 0)",
    )
    p.add_argument(
        "--profile-ids",
        nargs="*",
        default=None,
        metavar="PROFILE_ID",
        help="Restrict bridge evaluation to these preset profile IDs (default: all)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit the full structured JSON report",
    )
    p.add_argument(
        "--fail-under-roi",
        type=float,
        default=None,
        metavar="FRACTION",
        help="Exit non-zero when ROI at target volume < this fraction (e.g. 0.0)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry-point for the ``makerbench-protofunnel`` CLI."""
    # Late imports so the CLI module doesn't import heavy makerbench sub-packages
    # at module load time (mirrors the pattern in makerbench_core.cli).
    from .mfg_bridge import DesignSpec
    from .runner import FunnelRunConfig, run_funnel_stages

    args = build_parser().parse_args(argv)
    path = Path(args.artifact)

    design = DesignSpec(
        name=args.name or path.stem,
        material_volume_mm3=args.material_volume_mm3,
        removed_volume_mm3=args.removed_volume_mm3,
        sheet_area_mm2=args.sheet_area_mm2,
        cut_length_mm=args.cut_length_mm,
        bend_count=args.bend_count,
        hole_count=args.hole_count,
        print_time_minutes=args.print_time_minutes,
        support_material_volume_mm3=args.support_volume_mm3,
        sheet_nesting_yield=args.sheet_nesting_yield,
    )

    config = FunnelRunConfig(
        dfm_pass_threshold=args.dfm_pass_threshold,
        concept_cost_usd=args.concept_cost,
        unit_sale_price_usd=args.sale_price,
        quantity=args.quantity,
        bridge_profile_ids=args.profile_ids,
    )

    result = run_funnel_stages(path, design, config)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"artifact:     {path}")
        print(f"sha256:       {result.geometry_sha256[:16]}…")
        print(f"dfm_score:    {result.dfm_score:.1f}%")
        print(f"dfm_passed:   {result.dfm_passed}")
        print(f"gates_passed: {result.gates_passed}")
        if result.blocked_at:
            print(f"blocked_at:   {result.blocked_at}")
        if result.bridge:
            cheapest = result.bridge.cheapest()
            if cheapest:
                print(
                    f"cheapest:     {cheapest.profile_id} @ "
                    f"${cheapest.total_usd:.2f} (rank {cheapest.rank})"
                )
            print(f"processes:    {len(result.bridge.results)}")
        if result.roi:
            print(f"viable:       {result.roi.viable}")
            print(f"breakeven:    {result.roi.breakeven_units_ceil} units")
            print(f"roi@target:   {result.roi.roi_at_target:.1%}")
        for note in result.notes:
            print(f"note:         {note}")

    if args.fail_under_roi is not None:
        if result.roi is None or result.roi.roi_at_target < args.fail_under_roi:
            return 1
    if result.blocked_at is not None:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
