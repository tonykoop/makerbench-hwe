"""Tests for landscape report generator, CLI, and new adapters (epic #242).

Covers:
* landscape.report       — LandscapeReport, generate_report
* landscape.__main__     — CLI arg parsing and output modes
* landscape.adapters.*   — MUSEAdapter, UniCADAdapter, CADTestBenchAdapter
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from landscape.registry import BenchmarkEntry, BenchmarkRegistry
from landscape.report import LandscapeReport, generate_report
from landscape.adapters import (
    MUSEAdapter,
    UniCADAdapter,
    CADTestBenchAdapter,
    adapter_registry,
    get_adapter,
)
from landscape.adapters.base import CommonResult

YAML_AVAILABLE = False
try:
    import yaml  # noqa: F401
    YAML_AVAILABLE = True
except ImportError:
    pass


# ============================================================================
# Helpers
# ============================================================================

def _sample_entry(name: str, domain: list[str], kind: str = "benchmark",
                  scoring: str = "deterministic-geometric",
                  raw_extra: dict | None = None) -> BenchmarkEntry:
    raw: dict[str, Any] = {
        "name": name,
        "axis": domain,
        "scope": "shape",
        "fab_processes": [],
        "grading": scoring,
        "agentic": "single-shot",
        "openness": "MIT" if kind != "product" else "proprietary",
        "anti_memorization": "",
        "dataset_scale": "small",
    }
    if raw_extra:
        raw.update(raw_extra)
    return BenchmarkEntry(
        name=name,
        domain=domain,
        task_types=["shape"],
        scoring_model=scoring,
        url=f"https://example.com/{name.lower()}",
        kind=kind,
        entry_type=kind,
        recent=False,
        raw=raw,
    )


def _small_registry() -> BenchmarkRegistry:
    return BenchmarkRegistry.from_entries([
        _sample_entry("MakerBench-HWE", ["hardware-engineering", "dfm"],
                      raw_extra={"fab_processes": ["3d-print", "sheet-metal"],
                                 "scope": "manufacturability",
                                 "grading": "deterministic-geometric",
                                 "agentic": "perception-loop",
                                 "openness": "Apache-2.0"}),
        _sample_entry("PhysBench", ["physics-sim"], scoring="simulation-fea"),
        _sample_entry("CodeCADBench", ["code-cad"], scoring="executability"),
        _sample_entry("MethodA", ["spatial-intelligence"], kind="method",
                      scoring="llm-judge"),
    ])


# ============================================================================
# report tests
# ============================================================================

class TestLandscapeReport:
    def _report(self) -> LandscapeReport:
        reg = _small_registry()
        return generate_report(registry=reg)

    def test_total_entries(self):
        r = self._report()
        assert r.total_entries == 4

    def test_benchmark_count(self):
        r = self._report()
        assert r.benchmark_count == 3

    def test_method_count(self):
        r = self._report()
        assert r.method_count == 1

    def test_recent_count_zero(self):
        r = self._report()
        assert r.recent_count == 0

    def test_axis_coverage_keys(self):
        r = self._report()
        assert "hardware-engineering" in r.axis_coverage
        assert "physics-sim" in r.axis_coverage

    def test_grading_mix_populated(self):
        r = self._report()
        assert "deterministic-geometric" in r.grading_mix
        assert r.grading_mix["deterministic-geometric"] >= 1

    def test_gaps_populated(self):
        r = self._report()
        assert isinstance(r.gaps, list)
        assert len(r.gaps) > 0

    def test_matrix_csv_has_header(self):
        r = self._report()
        assert "name" in r.matrix_csv.lower() or "Name" in r.matrix_csv

    def test_matrix_csv_has_makerbench_row(self):
        r = self._report()
        assert "MakerBench-HWE" in r.matrix_csv

    def test_matrix_markdown_has_separator(self):
        r = self._report()
        assert "---" in r.matrix_markdown

    def test_to_dict_json_serialisable(self):
        r = self._report()
        json.dumps(r.to_dict())

    def test_to_json_parses(self):
        r = self._report()
        parsed = json.loads(r.to_json())
        assert parsed["total_entries"] == 4

    def test_to_text_contains_makerbench(self):
        r = self._report()
        text = r.to_text()
        assert "MakerBench" in text
        assert "competitor" in text.lower() or "coverage" in text.lower()

    def test_to_text_contains_axis_section(self):
        r = self._report()
        text = r.to_text()
        assert "Axis coverage" in text

    def test_to_markdown_contains_table(self):
        r = self._report()
        md = r.to_markdown()
        assert "|" in md
        assert "Axis coverage" in md

    def test_to_markdown_contains_comparison_matrix(self):
        r = self._report()
        md = r.to_markdown()
        assert "Comparison matrix" in md

    def test_empty_registry_report(self):
        reg = BenchmarkRegistry.from_entries([])
        r = generate_report(registry=reg)
        assert r.total_entries == 0
        assert r.gaps == []

    def test_dimension_subset(self):
        reg = _small_registry()
        r = generate_report(registry=reg, matrix_dimensions=["grading"])
        reader = csv.DictReader(io.StringIO(r.matrix_csv))
        headers = reader.fieldnames or []
        assert "Grading model" in headers

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_report_total_entries_nonzero(self):
        r = generate_report()
        assert r.total_entries >= 5

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_report_to_json_valid(self):
        r = generate_report()
        parsed = json.loads(r.to_json())
        assert parsed["benchmark_count"] >= 3

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_report_text_output(self):
        r = generate_report()
        text = r.to_text()
        assert "deterministic-geometric" in text


# ============================================================================
# CLI tests
# ============================================================================

class TestCLI:
    """Tests for landscape.__main__ using captured stdout."""

    def _run(self, args: list[str]) -> tuple[str, int]:
        from landscape.__main__ import main
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            code = main(args)
        finally:
            sys.stdout = old_stdout
        return buf.getvalue(), code

    def test_default_text_format(self):
        output, code = self._run([])
        assert code == 0
        assert "MakerBench" in output or "Total entries" in output

    def test_json_format(self):
        output, code = self._run(["--format", "json"])
        assert code == 0
        parsed = json.loads(output)
        assert "total_entries" in parsed

    def test_markdown_format(self):
        output, code = self._run(["--format", "markdown"])
        assert code == 0
        assert "|" in output

    def test_csv_format(self):
        output, code = self._run(["--format", "csv"])
        assert code == 0
        # Should be valid CSV with at least a header row
        lines = [l for l in output.splitlines() if l]
        assert len(lines) >= 1

    def test_dims_subset(self):
        output, code = self._run(["--format", "csv", "--dims", "grading"])
        assert code == 0
        # Header should only have grading column
        first_line = [l for l in output.splitlines() if l][0]
        assert "Grading" in first_line

    def test_unknown_dim_returns_2(self):
        from landscape.__main__ import main
        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            code = main(["--dims", "nonexistent_dim"])
        finally:
            sys.stderr = old_stderr
        assert code == 2


# ============================================================================
# MUSEAdapter tests
# ============================================================================

class TestMUSEAdapter:
    _ADAPTER = MUSEAdapter()

    def _good(self, **kw):
        base = {
            "entry_id": "muse-chair-01",
            "domain": "furniture",
            "geometric_valid": True,
            "shape_fidelity": 0.88,
            "vlm_score": 0.72,
            "surface_quality": 0.80,
        }
        base.update(kw)
        return base

    def test_happy_path(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.source_benchmark == "MUSE"
        assert r.artifact_id == "muse-chair-01"
        assert r.score == pytest.approx((0.88 + 0.72) / 2)

    def test_mixed_score_preferred(self):
        r = self._ADAPTER.adapt(self._good(mixed_score=0.99))
        assert r.score == pytest.approx(0.99)

    def test_grading_transparency_false(self):
        """MUSE has a VLM component — not fully deterministic."""
        r = self._ADAPTER.adapt(self._good())
        assert r.grading_transparency is False

    def test_domain_in_dimensions(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.dimensions["domain"] == "furniture"

    def test_vlm_score_in_dimensions(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.dimensions["vlm_score"] == pytest.approx(0.72)

    def test_missing_entry_id_raises(self):
        with pytest.raises(ValueError, match="entry_id"):
            self._ADAPTER.adapt({"domain": "furniture"})

    def test_schema_version(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.schema_version == "landscape-common-result-v1"

    def test_axis_coverage(self):
        r = self._ADAPTER.adapt(self._good())
        assert "spatial-intelligence" in r.axis_coverage

    def test_generates_tasks_false(self):
        assert self._ADAPTER.generates_tasks is False

    def test_calls_llm_false(self):
        assert self._ADAPTER.calls_llm is False


class TestUniCADAdapter:
    _ADAPTER = UniCADAdapter()

    def _good(self, **kw):
        base = {
            "run_id": "unicad-run-77",
            "representation": "brep",
            "tasks": [
                {"task_id": "t1", "representation": "brep", "score": 0.90, "valid_geometry": True},
                {"task_id": "t2", "representation": "csg", "score": 0.70, "valid_geometry": True},
                {"task_id": "t3", "representation": "mesh", "score": 0.50, "valid_geometry": False},
            ],
        }
        base.update(kw)
        return base

    def test_happy_path(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.source_benchmark == "UniCAD"
        assert r.artifact_id == "unicad-run-77"
        assert r.score == pytest.approx((0.90 + 0.70 + 0.50) / 3)

    def test_valid_geometry_count(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.dimensions["valid_geometry_count"] == 2

    def test_repr_breakdown_in_dims(self):
        r = self._ADAPTER.adapt(self._good())
        assert "repr_brep_avg" in r.dimensions
        assert r.dimensions["repr_brep_avg"] == pytest.approx(0.90)
        assert r.dimensions["repr_mesh_avg"] == pytest.approx(0.50)

    def test_explicit_accuracy_preferred(self):
        r = self._ADAPTER.adapt(self._good(accuracy=0.42))
        assert r.score == pytest.approx(0.42)

    def test_missing_run_id_raises(self):
        with pytest.raises(ValueError, match="run_id"):
            self._ADAPTER.adapt({"tasks": []})

    def test_grading_transparency_true(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.grading_transparency is True

    def test_axis_coverage_code_cad(self):
        r = self._ADAPTER.adapt(self._good())
        assert "code-cad" in r.axis_coverage

    def test_nine_adapters_in_registry(self):
        expected = {
            "CADGenBench", "BenDFM", "MARB / CADCLAW", "Hephaestus-CCX",
            "FEABench", "MUSE", "UniCAD", "CADTestBench", "ERI Benchmark",
        }
        assert expected <= set(adapter_registry.keys())


class TestCADTestBenchAdapter:
    _ADAPTER = CADTestBenchAdapter()

    def _good(self, **kw):
        base = {
            "submission_id": "ctb-sub-001",
            "language": "openscad",
            "tests": [
                {"test_id": "t1", "executable": True, "geometry_match": 0.95, "editability": 0.88},
                {"test_id": "t2", "executable": True, "geometry_match": 0.72, "editability": 0.65},
                {"test_id": "t3", "executable": False, "geometry_match": None, "editability": None},
            ],
        }
        base.update(kw)
        return base

    def test_happy_path(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.source_benchmark == "CADTestBench"
        assert r.artifact_id == "ctb-sub-001"
        assert r.score is not None

    def test_executability_rate(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.dimensions["executable_count"] == 2
        assert r.dimensions["executability_rate"] == pytest.approx(2 / 3)

    def test_avg_geometry_match(self):
        r = self._ADAPTER.adapt(self._good())
        expected = (0.95 + 0.72) / 2
        assert r.dimensions["avg_geometry_match"] == pytest.approx(expected)

    def test_avg_editability(self):
        r = self._ADAPTER.adapt(self._good())
        expected = (0.88 + 0.65) / 2
        assert r.dimensions["avg_editability"] == pytest.approx(expected)

    def test_explicit_overall_score(self):
        r = self._ADAPTER.adapt(self._good(overall_score=0.99))
        assert r.score == pytest.approx(0.99)

    def test_missing_submission_id_raises(self):
        with pytest.raises(ValueError, match="submission_id"):
            self._ADAPTER.adapt({"language": "openscad", "tests": []})

    def test_grading_transparency_true(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.grading_transparency is True

    def test_language_in_dimensions(self):
        r = self._ADAPTER.adapt(self._good())
        assert r.dimensions["language"] == "openscad"

    def test_axis_coverage(self):
        r = self._ADAPTER.adapt(self._good())
        assert "code-cad" in r.axis_coverage

    def test_get_adapter_cadtestbench(self):
        adapter = get_adapter("CADTestBench")
        assert isinstance(adapter, CADTestBenchAdapter)


# ============================================================================
# Cross-adapter invariant sweep (all 9 adapters)
# ============================================================================

class TestAllAdaptersInvariants:
    """All nine adapters must satisfy the three safety invariants."""

    _ALL = list(adapter_registry.values())

    @pytest.mark.parametrize("cls", _ALL, ids=lambda c: c.source_benchmark)
    def test_generates_tasks_false(self, cls):
        assert cls.generates_tasks is False

    @pytest.mark.parametrize("cls", _ALL, ids=lambda c: c.source_benchmark)
    def test_calls_llm_false(self, cls):
        assert cls.calls_llm is False

    @pytest.mark.parametrize("cls", _ALL, ids=lambda c: c.source_benchmark)
    def test_calls_oracle_false(self, cls):
        assert cls.calls_oracle is False
