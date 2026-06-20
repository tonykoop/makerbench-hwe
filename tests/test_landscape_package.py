"""Tests for the ``landscape/`` package (epic #242 — competitive-landscape interop).

Covers:
* landscape.registry   — BenchmarkEntry, BenchmarkRegistry
* landscape.matrix     — ComparisonMatrix, MatrixRow, DIMENSIONS
* landscape.adapters   — CommonResult, BaseInteropAdapter, all five stubs
* landscape.gap_finder — CoverageGap, CoverageGapFinder

All tests use either synthetic / in-memory data or the real docs/landscape.yaml
(when PyYAML is available).  No network calls, no oracle access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ============================================================================
# Imports under test
# ============================================================================

from landscape.registry import (
    BenchmarkEntry,
    BenchmarkRegistry,
    AXIS_VOCAB,
    SCORING_VOCAB,
    KIND_VOCAB,
    _entry_from_dict,
    _ensure_list,
    _primary_token,
)
from landscape.matrix import ComparisonMatrix, MatrixRow, DIMENSIONS
from landscape.gap_finder import CoverageGap, CoverageGapFinder
from landscape.adapters import (
    CommonResult,
    BaseInteropAdapter,
    CADGenBenchAdapter,
    BenDFMAdapter,
    MARBAdapter,
    HephaestusCCXAdapter,
    FEABenchAdapter,
    adapter_registry,
    get_adapter,
)

# ============================================================================
# Shared fixtures / helpers
# ============================================================================

_ROOT = Path(__file__).resolve().parents[1]

YAML_AVAILABLE = False
try:
    import yaml  # noqa: F401
    YAML_AVAILABLE = True
except ImportError:
    pass


def _sample_entry(**overrides: Any) -> BenchmarkEntry:
    """Build a minimal BenchmarkEntry for tests that don't need real YAML data."""
    defaults: dict[str, Any] = dict(
        name="TestBench",
        domain=["hardware-engineering", "dfm"],
        task_types=["manufacturability", "3d-print"],
        scoring_model="deterministic-geometric",
        url="https://example.com/testbench",
        kind="benchmark",
        entry_type="benchmark",
        recent=False,
        raw={
            "name": "TestBench",
            "axis": ["hardware-engineering", "dfm"],
            "scope": "manufacturability",
            "fab_processes": ["3d-print"],
            "grading": "deterministic-geometric",
            "agentic": "single-shot",
            "openness": "MIT",
            "anti_memorization": "parametric templates",
            "dataset_scale": "small",
        },
    )
    defaults.update(overrides)
    return BenchmarkEntry(**defaults)


# ============================================================================
# registry tests
# ============================================================================

class TestBenchmarkEntryHelpers:
    def test_covers_axis_true(self):
        e = _sample_entry(domain=["dfm", "hardware-engineering"])
        assert e.covers_axis("dfm") is True

    def test_covers_axis_false(self):
        e = _sample_entry(domain=["dfm"])
        assert e.covers_axis("physics-sim") is False

    def test_is_deterministic_true(self):
        e = _sample_entry(scoring_model="deterministic-geometric")
        assert e.is_deterministic() is True

    def test_is_deterministic_false(self):
        e = _sample_entry(scoring_model="llm-judge")
        assert e.is_deterministic() is False

    def test_is_open_true_apache(self):
        e = _sample_entry(raw={"openness": "Apache-2.0"})
        assert e.is_open() is True

    def test_is_open_false(self):
        e = _sample_entry(raw={"openness": "proprietary, no benchmark"})
        assert e.is_open() is False

    def test_fab_processes_returns_list(self):
        e = _sample_entry(raw={"fab_processes": ["3d-print", "sheet-metal"]})
        assert e.fab_processes() == ["3d-print", "sheet-metal"]

    def test_fab_processes_empty_for_method(self):
        e = _sample_entry(raw={})
        assert e.fab_processes() == []

    def test_agentic_tier(self):
        e = _sample_entry(raw={"agentic": "tool-use"})
        assert e.agentic_tier() == "tool-use"

    def test_agentic_tier_default(self):
        e = _sample_entry(raw={})
        assert e.agentic_tier() == "none"


class TestEnsureList:
    def test_none(self):
        assert _ensure_list(None) == []

    def test_list(self):
        assert _ensure_list(["a", "b"]) == ["a", "b"]

    def test_scalar(self):
        assert _ensure_list("hello") == ["hello"]

    def test_int(self):
        assert _ensure_list(42) == ["42"]


class TestPrimaryToken:
    def test_simple_vocab_token(self):
        assert _primary_token("deterministic-geometric") == "deterministic-geometric"

    def test_compound_grading(self):
        assert _primary_token("mixed (geometry + VLM)") == "mixed"

    def test_na_prefix(self):
        assert _primary_token("n/a (proprietary)") == "n/a"

    def test_extra_spaces(self):
        assert _primary_token("  executability  ") == "executability"


class TestEntryFromDict:
    def test_basic(self):
        raw = {
            "name": "Foo",
            "axis": ["dfm"],
            "scope": "shape",
            "fab_processes": ["3d-print"],
            "grading": "deterministic-geometric",
            "kind": "benchmark",
            "type": "benchmark",
            "source": "https://example.com",
            "recent": False,
        }
        e = _entry_from_dict(raw)
        assert e.name == "Foo"
        assert e.domain == ["dfm"]
        assert "shape" in e.task_types
        assert "3d-print" in e.task_types
        assert e.scoring_model == "deterministic-geometric"
        assert e.url == "https://example.com"

    def test_recent_true(self):
        raw = {"name": "X", "recent": True, "axis": [], "grading": "mixed",
               "kind": "benchmark", "type": "benchmark", "source": "https://x.com"}
        e = _entry_from_dict(raw)
        assert e.recent is True


class TestBenchmarkRegistry:
    def _make_registry(self) -> BenchmarkRegistry:
        entries = [
            _sample_entry(name="A", domain=["dfm"], scoring_model="deterministic-geometric", kind="benchmark", recent=True),
            _sample_entry(name="B", domain=["physics-sim"], scoring_model="simulation-fea", kind="benchmark"),
            _sample_entry(name="C", domain=["code-cad"], scoring_model="llm-judge", kind="method"),
            _sample_entry(name="D", domain=["dfm", "hardware-engineering"], scoring_model="deterministic-geometric", kind="dataset"),
        ]
        return BenchmarkRegistry.from_entries(entries)

    def test_all_returns_all(self):
        reg = self._make_registry()
        assert len(reg.all()) == 4

    def test_len(self):
        reg = self._make_registry()
        assert len(reg) == 4

    def test_by_domain(self):
        reg = self._make_registry()
        dfm = reg.by_domain("dfm")
        assert {e.name for e in dfm} == {"A", "D"}

    def test_by_scoring_model(self):
        reg = self._make_registry()
        det = reg.by_scoring_model("deterministic-geometric")
        assert {e.name for e in det} == {"A", "D"}

    def test_by_kind_benchmark(self):
        reg = self._make_registry()
        benches = reg.by_kind("benchmark")
        assert {e.name for e in benches} == {"A", "B"}

    def test_benchmarks_helper(self):
        reg = self._make_registry()
        assert {e.name for e in reg.benchmarks()} == {"A", "B"}

    def test_methods_helper(self):
        reg = self._make_registry()
        assert {e.name for e in reg.methods()} == {"C"}

    def test_recent(self):
        reg = self._make_registry()
        assert [e.name for e in reg.recent()] == ["A"]

    def test_get_found(self):
        reg = self._make_registry()
        assert reg.get("B").name == "B"

    def test_get_not_found(self):
        reg = self._make_registry()
        assert reg.get("Z") is None

    def test_search_multiple_filters(self):
        reg = self._make_registry()
        result = reg.search(domain="dfm", kind="benchmark")
        assert [e.name for e in result] == ["A"]

    def test_search_recent(self):
        reg = self._make_registry()
        result = reg.search(recent=True)
        assert [e.name for e in result] == ["A"]

    def test_empty_registry_from_entries(self):
        reg = BenchmarkRegistry.from_entries([])
        assert len(reg) == 0
        assert reg.all() == []

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_loads_real_landscape_yaml(self):
        reg = BenchmarkRegistry()
        assert len(reg) >= 5, "expected at least 5 entries from landscape.yaml"
        mb = reg.get("MakerBench-HWE")
        assert mb is not None
        assert mb.url.startswith("https://")
        assert "hardware-engineering" in mb.domain

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_all_entries_have_valid_axis(self):
        reg = BenchmarkRegistry()
        for e in reg.all():
            for ax in e.domain:
                assert ax in AXIS_VOCAB, f"{e.name}: unknown axis {ax!r}"

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_all_entries_have_valid_kind(self):
        reg = BenchmarkRegistry()
        for e in reg.all():
            assert e.kind in KIND_VOCAB, f"{e.name}: unknown kind {e.kind!r}"

    def test_repr(self):
        reg = self._make_registry()
        assert "BenchmarkRegistry" in repr(reg)


# ============================================================================
# matrix tests
# ============================================================================

class TestDimensions:
    def test_dimensions_list_is_non_empty(self):
        assert len(DIMENSIONS) >= 5

    def test_required_dimensions_present(self):
        for dim in ("axis", "grading", "agentic", "openness"):
            assert dim in DIMENSIONS


class TestMatrixRow:
    def test_get_present(self):
        row = MatrixRow(name="X", url="https://x.com",
                        dimensions={"grading": "det"})
        assert row.get("grading") == "det"

    def test_get_default(self):
        row = MatrixRow(name="X", url="https://x.com", dimensions={})
        assert row.get("grading", "n/a") == "n/a"

    def test_compare_to_equal(self):
        a = MatrixRow("A", "https://a.com", {"grading": "det"})
        b = MatrixRow("B", "https://b.com", {"grading": "det"})
        c = a.compare_to(b, "grading")
        assert c["equal"] is True

    def test_compare_to_not_equal(self):
        a = MatrixRow("A", "https://a.com", {"grading": "det"})
        b = MatrixRow("B", "https://b.com", {"grading": "llm"})
        c = a.compare_to(b, "grading")
        assert c["equal"] is False
        assert c["self_value"] == "det"
        assert c["other_value"] == "llm"


class TestComparisonMatrix:
    def _make_matrix(self) -> ComparisonMatrix:
        entries = [
            _sample_entry(name="MakerBench-HWE",
                          domain=["hardware-engineering", "dfm"],
                          scoring_model="deterministic-geometric",
                          kind="benchmark",
                          raw={
                              "name": "MakerBench-HWE",
                              "axis": ["hardware-engineering", "dfm"],
                              "scope": "manufacturability",
                              "fab_processes": ["3d-print", "sheet-metal"],
                              "grading": "deterministic-geometric",
                              "agentic": "perception-loop",
                              "openness": "Apache-2.0",
                              "anti_memorization": "parametric templates",
                              "dataset_scale": "~12 families",
                          }),
            _sample_entry(name="OtherBench",
                          domain=["code-cad"],
                          scoring_model="executability",
                          kind="benchmark",
                          raw={
                              "name": "OtherBench",
                              "axis": ["code-cad"],
                              "scope": "shape",
                              "fab_processes": [],
                              "grading": "executability",
                              "agentic": "single-shot",
                              "openness": "MIT",
                              "anti_memorization": "none claimed",
                              "dataset_scale": "100 tasks",
                          }),
        ]
        reg = BenchmarkRegistry.from_entries(entries)
        return ComparisonMatrix(registry=reg)

    def test_build_returns_rows(self):
        mat = self._make_matrix()
        rows = mat.build()
        assert len(rows) == 2

    def test_build_first_is_makerbench(self):
        mat = self._make_matrix()
        rows = mat.build()
        assert rows[0].name == "MakerBench-HWE"

    def test_build_dimension_subset(self):
        mat = self._make_matrix()
        rows = mat.build(dimensions=["grading"])
        for row in rows:
            assert list(row.dimensions.keys()) == ["grading"]

    def test_build_kind_filter(self):
        mat = self._make_matrix()
        rows = mat.build(kinds=["benchmark"])
        assert all(r.name in ("MakerBench-HWE", "OtherBench") for r in rows)

    def test_makerbench_row_found(self):
        mat = self._make_matrix()
        row = mat.makerbench_row()
        assert row is not None
        assert row.name == "MakerBench-HWE"

    def test_makerbench_grading_dimension(self):
        mat = self._make_matrix()
        row = mat.makerbench_row()
        assert row.get("grading") == "deterministic-geometric"

    def test_makerbench_openness_dimension(self):
        mat = self._make_matrix()
        row = mat.makerbench_row()
        assert "Apache-2.0" in row.get("openness")

    def test_coverage_per_dimension_axis(self):
        mat = self._make_matrix()
        cov = mat.coverage_per_dimension("axis")
        assert "hardware-engineering" in cov
        assert "MakerBench-HWE" in cov["hardware-engineering"]

    def test_compare_entry_to_makerbench(self):
        mat = self._make_matrix()
        results = mat.compare_entry_to_makerbench("OtherBench", dimensions=["grading"])
        assert len(results) == 1
        assert results[0]["self_name"] == "MakerBench-HWE"
        assert results[0]["other_name"] == "OtherBench"
        assert results[0]["equal"] is False  # det-geometric vs executability

    def test_compare_missing_name_returns_empty(self):
        mat = self._make_matrix()
        assert mat.compare_entry_to_makerbench("DoesNotExist") == []

    def test_to_csv_round_trip(self):
        mat = self._make_matrix()
        csv_text = mat.to_csv(dimensions=["grading"])
        lines = [l for l in csv_text.splitlines() if l]
        assert len(lines) == 3  # header + 2 data rows
        assert "Grading model" in lines[0]
        assert "MakerBench-HWE" in lines[1]

    def test_to_markdown_table_has_header_separator(self):
        mat = self._make_matrix()
        md = mat.to_markdown_table(dimensions=["grading"])
        lines = md.splitlines()
        assert lines[1].startswith("| ---")

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_matrix_has_makerbench_row(self):
        mat = ComparisonMatrix()
        row = mat.makerbench_row()
        assert row is not None
        assert row.get("grading") == "deterministic-geometric"

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_csv_parseable(self):
        import csv
        import io
        mat = ComparisonMatrix()
        csv_text = mat.to_csv()
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) >= 5


# ============================================================================
# adapter tests
# ============================================================================

class TestCommonResult:
    def test_to_dict_schema_version(self):
        r = CommonResult(
            source_benchmark="Test",
            artifact_id="abc",
            score=0.9,
        )
        d = r.to_dict()
        assert d["schema_version"] == "landscape-common-result-v1"
        assert d["source_benchmark"] == "Test"
        assert d["artifact_id"] == "abc"
        assert d["score"] == pytest.approx(0.9)

    def test_to_dict_json_serialisable(self):
        r = CommonResult(
            source_benchmark="Test",
            artifact_id="abc",
            score=None,
            dimensions={"a": 1},
            axis_coverage=["dfm"],
        )
        json.dumps(r.to_dict())  # must not raise

    def test_raw_not_in_to_dict(self):
        r = CommonResult("X", "y", 0.5, raw={"secret": True})
        assert "raw" not in r.to_dict()


class TestBaseInteropAdapterInvariants:
    """Every concrete adapter must honour the three safety invariants."""

    _ADAPTERS = [
        CADGenBenchAdapter(),
        BenDFMAdapter(),
        MARBAdapter(),
        HephaestusCCXAdapter(),
        FEABenchAdapter(),
    ]

    @pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.source_benchmark)
    def test_generates_tasks_false(self, adapter):
        assert adapter.generates_tasks is False

    @pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.source_benchmark)
    def test_calls_llm_false(self, adapter):
        assert adapter.calls_llm is False

    @pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.source_benchmark)
    def test_calls_oracle_false(self, adapter):
        assert adapter.calls_oracle is False

    @pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.source_benchmark)
    def test_describe_returns_invariants(self, adapter):
        d = adapter.describe()
        assert d["generates_tasks"] is False
        assert d["calls_llm"] is False
        assert d["calls_oracle"] is False
        assert d["source_benchmark"] == adapter.source_benchmark

    @pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.source_benchmark)
    def test_source_benchmark_non_empty(self, adapter):
        assert adapter.source_benchmark and adapter.source_benchmark != "unknown"


class TestAdapterRegistry:
    def test_all_five_registered(self):
        expected = {"CADGenBench", "BenDFM", "MARB / CADCLAW", "Hephaestus-CCX", "FEABench"}
        assert expected <= set(adapter_registry.keys())

    def test_get_adapter_returns_instance(self):
        adapter = get_adapter("CADGenBench")
        assert isinstance(adapter, CADGenBenchAdapter)

    def test_get_adapter_unknown_raises_key_error(self):
        with pytest.raises(KeyError):
            get_adapter("DoesNotExist")


class TestCADGenBenchAdapter:
    _ADAPTER = CADGenBenchAdapter()

    def _good_result(self, **overrides):
        base = {
            "task_id": "chair-001",
            "model": "gpt-5",
            "step_valid": True,
            "solid_count": 1,
            "surface_count": 24,
            "bbox_iou": 0.93,
            "overall_score": 0.87,
        }
        base.update(overrides)
        return base

    def test_happy_path(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.source_benchmark == "CADGenBench"
        assert r.artifact_id == "chair-001"
        assert r.score == pytest.approx(0.87)
        assert r.grading_transparency is True
        assert "step_valid" in r.dimensions
        assert r.dimensions["step_valid"] is True

    def test_score_maps_to_overall(self):
        r = self._ADAPTER.adapt(self._good_result(overall_score=0.5))
        assert r.score == pytest.approx(0.5)

    def test_null_score_is_none(self):
        r = self._ADAPTER.adapt(self._good_result(overall_score=None))
        assert r.score is None

    def test_missing_task_id_raises(self):
        with pytest.raises(ValueError, match="task_id"):
            self._ADAPTER.adapt({"model": "x", "overall_score": 0.9})

    def test_raw_stored(self):
        raw = self._good_result()
        r = self._ADAPTER.adapt(raw)
        assert r.raw == raw

    def test_axis_coverage(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert "code-cad" in r.axis_coverage

    def test_schema_version_stamped(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.schema_version == "landscape-common-result-v1"

    def test_to_dict_round_trip(self):
        r = self._ADAPTER.adapt(self._good_result())
        d = r.to_dict()
        assert d["artifact_id"] == "chair-001"
        assert d["score"] == pytest.approx(0.87)


class TestBenDFMAdapter:
    _ADAPTER = BenDFMAdapter()

    def _good_result(self, **overrides):
        base = {
            "entry_id": "part-42",
            "process": "injection_molding",
            "checks": {
                "draft_angle": {"passed": True, "severity": None},
                "min_wall_thickness": {"passed": False, "severity": "critical"},
                "undercut": {"passed": True, "severity": None},
            },
        }
        base.update(overrides)
        return base

    def test_happy_path(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.source_benchmark == "BenDFM"
        assert r.artifact_id == "part-42"
        assert r.score is not None
        assert 0 <= r.score <= 1
        assert r.grading_transparency is True

    def test_pass_rate_computed(self):
        r = self._ADAPTER.adapt(self._good_result())
        # 2 of 3 checks pass → pass_rate = 2/3
        assert r.dimensions["pass_rate"] == pytest.approx(2 / 3)

    def test_explicit_pass_rate_used(self):
        r = self._ADAPTER.adapt(self._good_result(pass_rate=0.99))
        assert r.dimensions["pass_rate"] == pytest.approx(0.99)

    def test_check_dimensions_present(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert "check_draft_angle_passed" in r.dimensions
        assert r.dimensions["check_draft_angle_passed"] is True
        assert r.dimensions["check_min_wall_thickness_passed"] is False

    def test_missing_entry_id_raises(self):
        with pytest.raises(ValueError, match="entry_id"):
            self._ADAPTER.adapt({"checks": {}})

    def test_checks_must_be_dict(self):
        with pytest.raises(ValueError, match="checks"):
            self._ADAPTER.adapt({"entry_id": "x", "checks": []})

    def test_axis_coverage_includes_dfm(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert "dfm" in r.axis_coverage


class TestMARBAdapter:
    _ADAPTER = MARBAdapter()

    def _good_result(self, **overrides):
        base = {
            "assembly_id": "asm-007",
            "parts": [
                {"part_id": "p1", "interference_free": True, "dof_satisfied": True, "joint_score": 0.95},
                {"part_id": "p2", "interference_free": False, "dof_satisfied": True, "joint_score": 0.60},
            ],
            "grading_engine": "cadclaw-v1",
        }
        base.update(overrides)
        return base

    def test_happy_path(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.source_benchmark == "MARB / CADCLAW"
        assert r.artifact_id == "asm-007"
        assert r.grading_transparency is True

    def test_assembly_score_derived_from_parts(self):
        r = self._ADAPTER.adapt(self._good_result())
        # interference_free: 1/2; dof_satisfied: 2/2 → (1+2)/(2*2) = 0.75
        assert r.score == pytest.approx(0.75)

    def test_explicit_assembly_score_preferred(self):
        r = self._ADAPTER.adapt(self._good_result(assembly_score=0.42))
        assert r.score == pytest.approx(0.42)

    def test_part_count_dimension(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.dimensions["part_count"] == 2
        assert r.dimensions["interference_free_count"] == 1
        assert r.dimensions["dof_ok_count"] == 2

    def test_missing_assembly_id_raises(self):
        with pytest.raises(ValueError, match="assembly_id"):
            self._ADAPTER.adapt({"parts": []})

    def test_empty_parts_score_zero(self):
        r = self._ADAPTER.adapt(self._good_result(parts=[]))
        assert r.score is None  # no parts → can't derive score

    def test_axis_coverage(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert "hardware-engineering" in r.axis_coverage


class TestHephaestusCCXAdapter:
    _ADAPTER = HephaestusCCXAdapter()

    def _good_result(self, **overrides):
        base = {
            "submission_id": "sub-001",
            "model_id": "bracket-v2",
            "scenarios": [
                {"scenario_id": "s1", "max_stress_mpa": 120.5, "max_displacement_mm": 0.3, "structural_pass": True},
                {"scenario_id": "s2", "max_stress_mpa": 310.0, "max_displacement_mm": 1.2, "structural_pass": False},
            ],
            "solver": "ccx",
        }
        base.update(overrides)
        return base

    def test_happy_path(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.source_benchmark == "Hephaestus-CCX"
        assert r.artifact_id == "bracket-v2"
        assert r.grading_transparency is True

    def test_pass_rate_derived(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.score == pytest.approx(0.5)  # 1 of 2 pass

    def test_avg_stress_computed(self):
        r = self._ADAPTER.adapt(self._good_result())
        expected = (120.5 + 310.0) / 2
        assert r.dimensions["avg_max_stress_mpa"] == pytest.approx(expected)

    def test_explicit_pass_rate_preferred(self):
        r = self._ADAPTER.adapt(self._good_result(overall_pass_rate=0.8))
        assert r.score == pytest.approx(0.8)

    def test_submission_id_fallback_artifact_id(self):
        result = self._good_result()
        del result["model_id"]
        r = self._ADAPTER.adapt(result)
        assert r.artifact_id == "sub-001"

    def test_missing_submission_id_raises(self):
        with pytest.raises(ValueError, match="submission_id"):
            self._ADAPTER.adapt({"model_id": "x"})

    def test_axis_coverage_includes_physics_sim(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert "physics-sim" in r.axis_coverage


class TestFEABenchAdapter:
    _ADAPTER = FEABenchAdapter()

    def _good_result(self, **overrides):
        base = {
            "run_id": "run-99",
            "model": "gemini-pro",
            "questions": [
                {"question_id": "q1", "correct": True, "relative_error": 0.01},
                {"question_id": "q2", "correct": False, "relative_error": 0.35},
                {"question_id": "q3", "correct": True, "relative_error": 0.02},
            ],
        }
        base.update(overrides)
        return base

    def test_happy_path(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.source_benchmark == "FEABench"
        assert r.artifact_id == "run-99"

    def test_accuracy_derived(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.score == pytest.approx(2 / 3)
        assert r.dimensions["correct_count"] == 2

    def test_explicit_accuracy_preferred(self):
        r = self._ADAPTER.adapt(self._good_result(accuracy=0.99))
        assert r.score == pytest.approx(0.99)

    def test_avg_relative_error_computed(self):
        r = self._ADAPTER.adapt(self._good_result())
        expected = (0.01 + 0.35 + 0.02) / 3
        assert r.dimensions["avg_relative_error"] == pytest.approx(expected)

    def test_missing_run_id_raises(self):
        with pytest.raises(ValueError, match="run_id"):
            self._ADAPTER.adapt({"model": "x"})

    def test_grading_transparency_true(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert r.grading_transparency is True

    def test_axis_coverage_includes_physics(self):
        r = self._ADAPTER.adapt(self._good_result())
        assert "physics-sim" in r.axis_coverage


# ============================================================================
# gap_finder tests
# ============================================================================

class TestCoverageGap:
    def test_has_gap_true(self):
        g = CoverageGap("axis", {"dfm"}, {"dfm", "physics-sim"}, gap={"physics-sim"})
        assert g.has_gap() is True

    def test_has_gap_false(self):
        g = CoverageGap("axis", {"dfm", "physics-sim"}, {"dfm"}, gap=set())
        assert g.has_gap() is False

    def test_has_lead_true(self):
        g = CoverageGap("axis", {"dfm", "reverse-engineering"}, {"dfm"},
                        lead={"reverse-engineering"})
        assert g.has_lead() is True

    def test_summary_line_with_gap(self):
        g = CoverageGap("axis", {"dfm"}, {"dfm", "physics-sim"}, gap={"physics-sim"})
        line = g.summary_line()
        assert "gap" in line
        assert "physics-sim" in line

    def test_summary_line_no_gap_no_lead(self):
        g = CoverageGap("axis", {"dfm"}, {"dfm"}, gap=set(), lead=set())
        assert "no gap" in g.summary_line()


class TestCoverageGapFinder:
    def _make_finder(self) -> CoverageGapFinder:
        mb_raw = {
            "name": "MakerBench-HWE",
            "axis": ["hardware-engineering", "dfm", "reverse-engineering"],
            "scope": "manufacturability",
            "fab_processes": ["3d-print", "sheet-metal", "laser-2d"],
            "grading": "deterministic-geometric",
            "agentic": "perception-loop",
        }
        comp1_raw = {
            "name": "PhysBench",
            "axis": ["physics-sim", "hardware-engineering"],
            "scope": "physics",
            "fab_processes": [],
            "grading": "simulation-fea",
            "agentic": "single-shot",
        }
        comp2_raw = {
            "name": "CADBench",
            "axis": ["code-cad", "spatial-intelligence"],
            "scope": "shape",
            "fab_processes": [],
            "grading": "deterministic-geometric",
            "agentic": "single-shot",
        }

        entries = [
            BenchmarkEntry(
                name="MakerBench-HWE",
                domain=["hardware-engineering", "dfm", "reverse-engineering"],
                task_types=["manufacturability", "3d-print", "sheet-metal", "laser-2d"],
                scoring_model="deterministic-geometric",
                url="https://github.com/TonyKoop/makerbench-hwe",
                kind="benchmark",
                entry_type="benchmark",
                recent=False,
                raw=mb_raw,
            ),
            BenchmarkEntry(
                name="PhysBench",
                domain=["physics-sim", "hardware-engineering"],
                task_types=["physics"],
                scoring_model="simulation-fea",
                url="https://example.com/physbench",
                kind="benchmark",
                entry_type="benchmark",
                recent=True,
                raw=comp1_raw,
            ),
            BenchmarkEntry(
                name="CADBench",
                domain=["code-cad", "spatial-intelligence"],
                task_types=["shape"],
                scoring_model="deterministic-geometric",
                url="https://example.com/cadbench",
                kind="benchmark",
                entry_type="benchmark",
                recent=False,
                raw=comp2_raw,
            ),
        ]
        reg = BenchmarkRegistry.from_entries(entries)
        return CoverageGapFinder(registry=reg)

    def test_makerbench_entry_found(self):
        finder = self._make_finder()
        mb = finder.makerbench_entry()
        assert mb is not None
        assert mb.name == "MakerBench-HWE"

    def test_competitors_excludes_makerbench(self):
        finder = self._make_finder()
        names = {e.name for e in finder.competitors()}
        assert "MakerBench-HWE" not in names
        assert "PhysBench" in names
        assert "CADBench" in names

    def test_find_gaps_returns_list(self):
        finder = self._make_finder()
        gaps = finder.find_gaps()
        assert isinstance(gaps, list)
        assert len(gaps) > 0

    def test_axis_gap_detects_physics_sim(self):
        finder = self._make_finder()
        axis_gap = finder.axis_gap()
        assert "physics-sim" in axis_gap.gap, (
            "physics-sim is covered by PhysBench but not MakerBench-HWE"
        )

    def test_axis_lead_has_reverse_engineering(self):
        finder = self._make_finder()
        axis_gap = finder.axis_gap()
        assert "reverse-engineering" in axis_gap.lead, (
            "reverse-engineering is MakerBench's unique axis vs these two competitors"
        )

    def test_gaps_only_subset_of_find_gaps(self):
        finder = self._make_finder()
        all_gaps = finder.find_gaps()
        gaps_only = finder.gaps_only()
        assert all(g.has_gap() for g in gaps_only)
        assert len(gaps_only) <= len(all_gaps)

    def test_leads_only_subset_of_find_gaps(self):
        finder = self._make_finder()
        all_gaps = finder.find_gaps()
        leads_only = finder.leads_only()
        assert all(g.has_lead() for g in leads_only)
        assert len(leads_only) <= len(all_gaps)

    def test_summary_returns_string(self):
        finder = self._make_finder()
        s = finder.summary()
        assert isinstance(s, str)
        assert "MakerBench-HWE" in s
        assert "competitor" in s

    def test_to_dict_json_serialisable(self):
        finder = self._make_finder()
        d = finder.to_dict()
        json.dumps(d)  # must not raise

    def test_to_dict_structure(self):
        finder = self._make_finder()
        d = finder.to_dict()
        assert d["makerbench"] == "MakerBench-HWE"
        assert d["competitor_count"] == 2
        assert isinstance(d["gaps"], list)
        for gap in d["gaps"]:
            assert "dimension" in gap
            assert "gap" in gap
            assert "lead" in gap
            assert "has_gap" in gap
            assert "has_lead" in gap

    def test_no_registry_loaded_returns_empty(self):
        # Pass an empty registry — makerbench entry not found
        reg = BenchmarkRegistry.from_entries([])
        finder = CoverageGapFinder(registry=reg)
        assert finder.makerbench_entry() is None
        assert finder.find_gaps() == []

    def test_summary_when_missing_makerbench(self):
        reg = BenchmarkRegistry.from_entries([])
        finder = CoverageGapFinder(registry=reg)
        s = finder.summary()
        assert "not found" in s

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_gap_finder_loads_from_yaml(self):
        finder = CoverageGapFinder()
        mb = finder.makerbench_entry()
        assert mb is not None
        gaps = finder.find_gaps()
        assert len(gaps) > 0

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_axis_gap_is_gap_instance(self):
        finder = CoverageGapFinder()
        ag = finder.axis_gap()
        assert isinstance(ag, CoverageGap)
        assert ag.dimension == "axis"

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_to_dict_is_json_serialisable(self):
        finder = CoverageGapFinder()
        d = finder.to_dict()
        json.dumps(d)


# ============================================================================
# Integration test: end-to-end from registry → matrix → gap → adapter
# ============================================================================

class TestEndToEnd:
    """Quick sanity check that all four modules compose without errors."""

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_e2e_registry_to_csv(self):
        from landscape import ComparisonMatrix
        mat = ComparisonMatrix()
        csv_text = mat.to_csv(dimensions=["axis", "grading"])
        assert "MakerBench-HWE" in csv_text
        assert "deterministic-geometric" in csv_text

    def test_e2e_adapter_result_is_json_safe(self):
        adapter = CADGenBenchAdapter()
        result = adapter.adapt({
            "task_id": "test-001",
            "overall_score": 0.75,
            "step_valid": True,
        })
        json.dumps(result.to_dict())

    def test_e2e_gap_finder_with_synthetic_registry(self):
        mb = BenchmarkEntry(
            name="MakerBench-HWE",
            domain=["dfm"],
            task_types=["manufacturability"],
            scoring_model="deterministic-geometric",
            url="https://example.com",
            kind="benchmark",
            entry_type="benchmark",
            raw={"axis": ["dfm"], "scope": "manufacturability",
                 "fab_processes": [], "grading": "deterministic-geometric",
                 "agentic": "none"},
        )
        comp = BenchmarkEntry(
            name="FooCAD",
            domain=["physics-sim"],
            task_types=["physics"],
            scoring_model="simulation-fea",
            url="https://foo.example",
            kind="benchmark",
            entry_type="benchmark",
            raw={"axis": ["physics-sim"], "scope": "physics",
                 "fab_processes": [], "grading": "simulation-fea",
                 "agentic": "single-shot"},
        )
        reg = BenchmarkRegistry.from_entries([mb, comp])
        finder = CoverageGapFinder(registry=reg)
        axis_gap = finder.axis_gap()
        assert "physics-sim" in axis_gap.gap
        d = finder.to_dict()
        assert d["competitor_count"] == 1
