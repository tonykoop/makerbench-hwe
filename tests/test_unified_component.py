"""Unified Component Model contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from makerbench.unified_component import (
    ComponentMetadata,
    Footprint,
    FootprintPad,
    PinPadMapEntry,
    SchematicSymbol,
    SymbolPin,
    UnifiedComponent,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _component(**overrides) -> UnifiedComponent:
    base = dict(
        metadata=ComponentMetadata(
            component_id="mb-test-switch",
            display_name="SPST switch",
            category="switch",
        ),
        symbol=SchematicSymbol(
            symbol_id="Switch:SW_SPST",
            reference_prefix="SW",
            pins=[
                SymbolPin(number="1", name="A", electrical_type="passive"),
                SymbolPin(number="2", name="B", electrical_type="passive"),
            ],
        ),
        footprint=Footprint(
            footprint_id="Button_Switch_SMD:SW_SPST_2P",
            pads=[
                FootprintPad(number="1", width_mm=1.2, height_mm=1.0),
                FootprintPad(number="2", x_mm=2.54, width_mm=1.2, height_mm=1.0),
                FootprintPad(
                    number="MH1",
                    type="mechanical",
                    shape="circle",
                    width_mm=0.9,
                    height_mm=0.9,
                    electrically_connected=False,
                ),
            ],
        ),
        pin_pad_map=[
            PinPadMapEntry(symbol_pin="1", footprint_pad="1"),
            PinPadMapEntry(symbol_pin="2", footprint_pad="2"),
        ],
    )
    base.update(overrides)
    return UnifiedComponent(**base)


def test_unified_component_round_trips_symbol_footprint_and_metadata():
    component = _component()
    loaded = UnifiedComponent.model_validate_json(component.model_dump_json())

    assert loaded.schema_version == "makerbench-unified-component-v1"
    assert loaded.metadata.component_id == "mb-test-switch"
    assert loaded.symbol.format == "sch-symbol-json"
    assert loaded.footprint.pads[2].electrically_connected is False
    assert loaded.pin_pad_map[0].symbol_pin == "1"


def test_unified_component_rejects_unmapped_symbol_pin():
    with pytest.raises(ValidationError, match="all schematic pins must be mapped"):
        _component(pin_pad_map=[PinPadMapEntry(symbol_pin="1", footprint_pad="1")])


def test_unified_component_rejects_unmapped_electrical_pad():
    footprint = Footprint(
        footprint_id="Connector_PinHeader_1x03",
        pads=[
            FootprintPad(number="1", width_mm=1.0, height_mm=1.0),
            FootprintPad(number="2", width_mm=1.0, height_mm=1.0),
            FootprintPad(number="3", width_mm=1.0, height_mm=1.0),
        ],
    )

    with pytest.raises(ValidationError, match="electrically connected footprint pads"):
        _component(footprint=footprint)


def test_unified_component_rejects_unknown_mapping_reference():
    with pytest.raises(ValidationError, match="unknown symbol pins"):
        _component(
            pin_pad_map=[
                PinPadMapEntry(symbol_pin="1", footprint_pad="1"),
                PinPadMapEntry(symbol_pin="3", footprint_pad="2"),
            ]
        )


def test_unified_component_rejects_duplicate_symbol_and_pad_numbers():
    with pytest.raises(ValidationError, match="schematic symbol pin numbers must be unique"):
        SchematicSymbol(
            symbol_id="Bad:Symbol",
            reference_prefix="U",
            pins=[
                SymbolPin(number="1", name="A"),
                SymbolPin(number="1", name="B"),
            ],
        )

    with pytest.raises(ValidationError, match="footprint pad numbers must be unique"):
        Footprint(
            footprint_id="Bad:Footprint",
            pads=[
                FootprintPad(number="1", width_mm=1.0, height_mm=1.0),
                FootprintPad(number="1", width_mm=1.0, height_mm=1.0),
            ],
        )


def test_exported_unified_component_schema_and_example_are_not_stale():
    proc = subprocess.run(
        [sys.executable, "scripts/export_unified_component_schema.py", "--check"],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_committed_unified_component_example_validates():
    example = json.loads(
        (REPO_ROOT / "schemas" / "examples" / "unified_component.example.json").read_text(
            encoding="utf-8"
        )
    )
    component = UnifiedComponent.model_validate(example)

    assert component.metadata.category == "resistor"
    assert {entry.symbol_pin for entry in component.pin_pad_map} == {"1", "2"}
