"""CADCLAW micro-DFM adapter tests (mb#78).

These exercise the adapter against the *real* portable component scorer
(``makerbench_core.score_file``) using tiny on-disk artifacts, plus a couple of
injected-scorer cases for the rollup math and gate policy.
"""

from __future__ import annotations

import json

import pytest

from makerbench.adapters import (
    AssemblyPart,
    AssemblySubGrade,
    BaseAdapter,
    CADCLAWAdapter,
    PartComponentReport,
)
from makerbench.adapters.base import BaseAdapter as BaseAdapterFromBase

# A minimal but structurally complete STEP file: ISO envelope, HEADER/DATA/
# terminator, product metadata, and a cartesian shape token — passes every
# portable STEP rule.
_GOOD_STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('bracket'),'2;1');
FILE_NAME('bracket.step','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));
ENDSEC;
DATA;
#1=PRODUCT('bracket','bracket','',(#2));
#2=PRODUCT_CONTEXT('',#3,'mechanical');
#10=CARTESIAN_POINT('',(0.0,0.0,0.0));
ENDSEC;
END-ISO-10303-21;
"""

# Missing the HEADER/DATA sections and product metadata -> fails STEP structure
# rules and drops below the 80% gate.
_BAD_STEP = """ISO-10303-21;
SOME GARBAGE THAT IS NOT A REAL STEP FILE
"""


def _write(tmp_path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content)
    return str(path)


def test_adapter_is_a_base_adapter_with_safe_provenance():
    adapter = CADCLAWAdapter()
    assert isinstance(adapter, BaseAdapter)
    assert BaseAdapter is BaseAdapterFromBase
    meta = adapter.describe()
    assert meta["adapter_id"] == "cadclaw-microdfm-v1"
    assert meta["ecosystem"] == "cadclaw"
    assert meta["oracle_access"] is False
    assert meta["llm_judge"] is False
    assert meta["generates_tasks"] is False
    assert meta["require_all_parts"] is True


def test_all_parts_manufacturable_passes_gate(tmp_path):
    parts = [
        AssemblyPart("bracket-a", _write(tmp_path, "a.step", _GOOD_STEP), quantity=2),
        AssemblyPart("bracket-b", _write(tmp_path, "b.step", _GOOD_STEP)),
    ]
    grade = CADCLAWAdapter().evaluate(parts, assembly_id="gearbox")

    assert isinstance(grade, AssemblySubGrade)
    assert grade.assembly_id == "gearbox"
    assert grade.gate_passed is True
    assert grade.parts_total == 2
    assert grade.parts_passed == 2
    assert grade.parts_failed == 0
    assert grade.failed_part_ids == []
    assert grade.assembly_score == 100.0
    assert all(isinstance(p, PartComponentReport) for p in grade.parts)


def test_one_unmanufacturable_part_fails_strict_gate(tmp_path):
    parts = [
        AssemblyPart("good", _write(tmp_path, "good.step", _GOOD_STEP)),
        AssemblyPart("bad", _write(tmp_path, "bad.step", _BAD_STEP)),
    ]
    grade = CADCLAWAdapter().evaluate(parts)

    assert grade.gate_passed is False
    assert grade.parts_failed == 1
    assert grade.failed_part_ids == ["bad"]
    bad = next(p for p in grade.parts if p.part_id == "bad")
    assert bad.passed is False
    assert bad.failed_rules  # carries the failing rule evidence
    assert "bad" in grade.gate_reason


def test_missing_file_fails_without_raising(tmp_path):
    grade = CADCLAWAdapter().evaluate(
        [AssemblyPart("ghost", str(tmp_path / "does_not_exist.step"))]
    )
    assert grade.gate_passed is False
    assert grade.parts[0].passed is False
    ghost_rule_ids = {r["id"] for r in grade.parts[0].failed_rules}
    assert "input.exists" in ghost_rule_ids


def test_empty_assembly_raises():
    with pytest.raises(ValueError, match="no parts"):
        CADCLAWAdapter().evaluate([])


def test_bare_path_and_dict_specs_are_coerced(tmp_path):
    path = _write(tmp_path, "panel.step", _GOOD_STEP)
    grade = CADCLAWAdapter().evaluate(
        [path, {"part_id": "lid", "artifact_path": path, "quantity": 3}]
    )
    assert grade.parts_total == 2
    # bare path derives part_id from the file stem
    assert grade.parts[0].part_id == "panel"
    lid = next(p for p in grade.parts if p.part_id == "lid")
    assert lid.quantity == 3


def test_assembly_part_validation():
    with pytest.raises(ValueError):
        AssemblyPart("", "x.step")
    with pytest.raises(ValueError):
        AssemblyPart("p", "")
    with pytest.raises(ValueError):
        AssemblyPart("p", "x.step", quantity=0)


# --- rollup math + policy with an injected deterministic scorer ---------------


class _FakeResult:
    def __init__(self, score: float, passed: bool, fmt: str = "step"):
        self._d = {
            "makerbench_dfm_score": score,
            "passed": passed,
            "profile": "portable-dfm-v1",
            "input": {"format": fmt, "sha256": "deadbeef"},
            "rules": [
                {"id": "format.step.sections", "passed": passed, "detail": "x"},
            ],
        }

    def to_dict(self):
        return dict(self._d)


def _fake_scorer(scores: dict[str, _FakeResult]):
    def _scorer(path, *, profile):  # noqa: ARG001
        return scores[str(path)]

    return _scorer


def test_assembly_score_is_quantity_weighted():
    scores = {
        "a": _FakeResult(100.0, True),
        "b": _FakeResult(60.0, False),
    }
    adapter = CADCLAWAdapter(scorer=_fake_scorer(scores))
    grade = adapter.evaluate(
        [
            AssemblyPart("a", "a", quantity=3),
            AssemblyPart("b", "b", quantity=1),
        ]
    )
    # weighted mean = (100*3 + 60*1) / 4 = 90.0
    assert grade.assembly_score == 90.0
    assert grade.min_part_score == 60.0
    assert grade.gate_passed is False  # strict: part b failed


def test_soft_rollup_passes_on_weighted_mean():
    scores = {
        "a": _FakeResult(100.0, True),
        "b": _FakeResult(60.0, False),
    }
    adapter = CADCLAWAdapter(
        scorer=_fake_scorer(scores),
        require_all_parts=False,
        assembly_pass_threshold=80.0,
    )
    grade = adapter.evaluate(
        [AssemblyPart("a", "a", quantity=3), AssemblyPart("b", "b", quantity=1)]
    )
    # weighted mean 90 >= 80 -> soft gate passes despite one failing part
    assert grade.gate_passed is True
    assert grade.parts_failed == 1


def test_stricter_part_threshold_overrides_scorer_pass():
    # Scorer says passed=True at 85, but adapter demands >= 90.
    scores = {"a": _FakeResult(85.0, True)}
    adapter = CADCLAWAdapter(scorer=_fake_scorer(scores), part_pass_threshold=90.0)
    grade = adapter.evaluate([AssemblyPart("a", "a")])
    assert grade.parts[0].passed is False
    assert grade.gate_passed is False


def test_threshold_bounds_validated():
    with pytest.raises(ValueError):
        CADCLAWAdapter(part_pass_threshold=101.0)
    with pytest.raises(ValueError):
        CADCLAWAdapter(assembly_pass_threshold=-1.0)


# --- output contract ----------------------------------------------------------


def test_as_cadclaw_gate_shape(tmp_path):
    grade = CADCLAWAdapter().evaluate(
        [AssemblyPart("p", _write(tmp_path, "p.step", _GOOD_STEP))]
    )
    gate = grade.as_cadclaw_gate()
    assert gate["gate_id"] == "makerbench.microdfm"
    assert gate["passed"] is True
    assert gate["max_score"] == 100.0
    assert gate["adapter_id"] == "cadclaw-microdfm-v1"
    assert gate["oracle_access"] is False
    assert gate["llm_judge"] is False


def test_report_json_roundtrips(tmp_path):
    grade = CADCLAWAdapter().evaluate(
        [AssemblyPart("p", _write(tmp_path, "p.step", _GOOD_STEP))]
    )
    payload = json.loads(grade.to_json())
    assert payload["adapter_id"] == "cadclaw-microdfm-v1"
    assert payload["schema_version"] == "makerbench-adapter-report-v1"
    assert payload["parts"][0]["part_id"] == "p"
    assert payload["provenance"]["oracle_access"] is False
