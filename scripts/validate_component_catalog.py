#!/usr/bin/env python3
"""Validate on-disk Unified Component Model catalog entries.

Each catalog entry is a directory holding a manifest (``metadata.yaml`` /
``metadata.json``) plus the three CAD files it points at: ``symbol.json``,
``footprint.kicad_mod``, and ``model.step``. This validator asserts the files
exist, that symbol pins and footprint electrical pads agree, and that the STEP
solid has a non-degenerate bounding box.

Usage:

    # validate the bundled worked examples
    python scripts/validate_component_catalog.py

    # validate any catalog tree (e.g. a clone of tonykoop/offtheshelf)
    python scripts/validate_component_catalog.py path/to/components/electronic

Exits nonzero if any entry fails.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from makerbench.unified_component import find_catalog_manifest, validate_catalog_entry

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = [REPO_ROOT / "examples" / "component_catalog"]


def _discover_entries(root: Path) -> list[Path]:
    if find_catalog_manifest(root) is not None:
        return [root]
    return sorted(
        p.parent for p in root.rglob("*")
        if p.is_file() and p.name in {"metadata.yaml", "metadata.yml", "metadata.json"}
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Catalog entry dirs or trees to scan (default: bundled examples).",
    )
    parser.add_argument(
        "--require-all-files",
        action="store_true",
        help="Demand all three CAD files even for mechanical entries.",
    )
    args = parser.parse_args()

    roots = args.roots or DEFAULT_ROOTS
    entries: list[Path] = []
    for root in roots:
        entries.extend(_discover_entries(Path(root)))

    if not entries:
        print("no catalog entries found", file=sys.stderr)
        return 1

    failures = 0
    for entry in entries:
        override = True if args.require_all_files else None
        report = validate_catalog_entry(entry, require_all_files=override)
        status = "OK " if report.ok else "FAIL"
        bbox = (
            "x".join(f"{v:.2f}" for v in report.step_bbox_mm)
            if report.step_bbox_mm
            else "-"
        )
        print(
            f"[{status}] {report.entry_id} ({report.category}) "
            f"pins={report.symbol_pin_count} pads={report.footprint_pad_count} "
            f"bbox={bbox} mm"
        )
        for warning in report.warnings:
            print(f"        warn: {warning}")
        for error in report.errors:
            print(f"        error: {error}")
        if not report.ok:
            failures += 1

    if failures:
        print(f"\n{failures} entr{'y' if failures == 1 else 'ies'} failed", file=sys.stderr)
        return 1
    print(f"\nall {len(entries)} catalog entr{'y' if len(entries) == 1 else 'ies'} valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
