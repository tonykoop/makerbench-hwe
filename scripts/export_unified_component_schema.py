#!/usr/bin/env python3
"""Export JSON Schema and an example for the Unified Component Model.

Run after editing ``makerbench.unified_component``:

    python scripts/export_unified_component_schema.py

With ``--check`` it exits nonzero if the committed schema/example are stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from makerbench.unified_component import (
    ComponentMetadata,
    Footprint,
    FootprintPad,
    PinPadMapEntry,
    SchematicSymbol,
    SymbolPin,
    UnifiedComponent,
)

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _dumps(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _example_component() -> UnifiedComponent:
    return UnifiedComponent(
        metadata=ComponentMetadata(
            component_id="mb-passive-resistor-0603",
            display_name="0603 resistor",
            category="resistor",
            manufacturer="Generic",
            manufacturer_part_number="RES-0603",
            description="Public example resistor component for schema validation.",
            tags=["passive", "smd", "0603"],
        ),
        symbol=SchematicSymbol(
            symbol_id="Device:R",
            reference_prefix="R",
            pins=[
                SymbolPin(number="1", name="A", electrical_type="passive", orientation="left"),
                SymbolPin(number="2", name="B", electrical_type="passive", orientation="right"),
            ],
            graphics={"body": {"shape": "zigzag", "width_mm": 3.81, "height_mm": 1.27}},
        ),
        footprint=Footprint(
            footprint_id="Resistor_SMD:R_0603_1608Metric",
            pads=[
                FootprintPad(number="1", x_mm=-0.8, y_mm=0.0, width_mm=0.9, height_mm=0.95),
                FootprintPad(number="2", x_mm=0.8, y_mm=0.0, width_mm=0.9, height_mm=0.95),
            ],
            courtyard_width_mm=2.2,
            courtyard_height_mm=1.4,
        ),
        pin_pad_map=[
            PinPadMapEntry(symbol_pin="1", footprint_pad="1"),
            PinPadMapEntry(symbol_pin="2", footprint_pad="2"),
        ],
    )


def _targets() -> dict[Path, str]:
    return {
        SCHEMAS_DIR / "unified_component.schema.json": _dumps(
            UnifiedComponent.model_json_schema()
        ),
        SCHEMAS_DIR / "examples" / "unified_component.example.json": _dumps(
            _example_component().model_dump(mode="json")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero if committed files are stale instead of writing them.",
    )
    args = parser.parse_args()

    targets = _targets()
    stale: list[Path] = []
    for path, text in targets.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(SCHEMAS_DIR.parent)}")

    if args.check and stale:
        names = ", ".join(str(path.relative_to(SCHEMAS_DIR.parent)) for path in stale)
        print(f"stale schema export(s): {names}", file=sys.stderr)
        print("run: python scripts/export_unified_component_schema.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
