"""Tests for landscape.sweeper and landscape.watchlist (epic #242).

Covers:
* landscape.sweeper   — SweepEntry, SweepResult, BatchSweeper, sweep
* landscape.watchlist — WatchlistEntry, VolatileWatchlist, load_watchlist
* landscape/schema.json validation against CommonResult.to_dict()
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from landscape.sweeper import SweepEntry, SweepResult, BatchSweeper, sweep
from landscape.watchlist import (
    WatchlistEntry,
    VolatileWatchlist,
    load_watchlist,
)
from landscape.registry import BenchmarkEntry, BenchmarkRegistry
from landscape.adapters import CommonResult, adapter_registry

YAML_AVAILABLE = False
try:
    import yaml  # noqa: F401
    YAML_AVAILABLE = True
except ImportError:
    pass

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _ROOT / "landscape" / "schema.json"


# ============================================================================
# Helpers
# ============================================================================

def _sample_entry(name: str) -> BenchmarkEntry:
    return BenchmarkEntry(
        name=name,
        domain=["hardware-engineering"],
        task_types=["manufacturability"],
        scoring_model="deterministic-geometric",
        url=f"https://example.com/{name.lower()}",
        kind="benchmark",
        entry_type="benchmark",
        recent=False,
        raw={"name": name},
    )


def _good_cadgenbench() -> dict[str, Any]:
    return {
        "task_id": "t1",
        "model": "test-model",
        "step_valid": True,
        "overall_score": 0.80,
    }


def _good_bendfm() -> dict[str, Any]:
    return {
        "entry_id": "p1",
        "process": "injection_molding",
        "checks": {
            "draft_angle": {"passed": True, "severity": None},
        },
    }


def _good_marb() -> dict[str, Any]:
    return {
        "assembly_id": "a1",
        "parts": [
            {"part_id": "p1", "interference_free": True, "dof_satisfied": True},
        ],
        "assembly_score": 0.95,
    }


# ============================================================================
# SweepEntry
# ============================================================================

class TestSweepEntry:
    def test_fields(self):
        e = SweepEntry("CADGenBench", {"task_id": "x"}, label="my-run")
        assert e.adapter_name == "CADGenBench"
        assert e.raw_result == {"task_id": "x"}
        assert e.label == "my-run"

    def test_default_label(self):
        e = SweepEntry("BenDFM", {})
        assert e.label == ""


# ============================================================================
# SweepResult
# ============================================================================

class TestSweepResult:
    def _make(self) -> SweepResult:
        cr1 = CommonResult("A", "a1", 0.9, grading_transparency=True)
        cr2 = CommonResult("B", "b1", 0.6, grading_transparency=False)
        cr3 = CommonResult("A", "a2", 0.8, grading_transparency=True)
        stats = {
            "A": {"count": 2, "score_count": 2, "avg_score": 0.85,
                  "min_score": 0.8, "max_score": 0.9, "transparent_count": 2},
            "B": {"count": 1, "score_count": 1, "avg_score": 0.6,
                  "min_score": 0.6, "max_score": 0.6, "transparent_count": 0},
        }
        return SweepResult(results=[cr1, cr2, cr3], errors={}, stats=stats)

    def test_success_count(self):
        sr = self._make()
        assert sr.success_count() == 3

    def test_error_count(self):
        sr = SweepResult(results=[], errors={0: "oops", 1: "bad"})
        assert sr.error_count() == 2

    def test_by_benchmark(self):
        sr = self._make()
        assert len(sr.by_benchmark("A")) == 2
        assert len(sr.by_benchmark("B")) == 1

    def test_deterministic_only(self):
        sr = self._make()
        det = sr.deterministic_only()
        assert len(det) == 2
        assert all(r.source_benchmark == "A" for r in det)

    def test_nondeterministic_only(self):
        sr = self._make()
        nd = sr.nondeterministic_only()
        assert len(nd) == 1
        assert nd[0].source_benchmark == "B"

    def test_scores(self):
        sr = self._make()
        assert sorted(sr.scores()) == pytest.approx(sorted([0.9, 0.6, 0.8]))

    def test_avg_score(self):
        sr = self._make()
        expected = (0.9 + 0.6 + 0.8) / 3
        assert sr.avg_score() == pytest.approx(expected)

    def test_avg_score_none_when_empty(self):
        sr = SweepResult()
        assert sr.avg_score() is None

    def test_to_dict_json_serialisable(self):
        sr = self._make()
        json.dumps(sr.to_dict())

    def test_to_dict_structure(self):
        sr = self._make()
        d = sr.to_dict()
        assert d["success_count"] == 3
        assert d["error_count"] == 0
        assert "stats" in d
        assert "results" in d


# ============================================================================
# BatchSweeper
# ============================================================================

class TestBatchSweeper:
    def test_single_entry_success(self):
        sweeper = BatchSweeper()
        result = sweeper.run([SweepEntry("CADGenBench", _good_cadgenbench())])
        assert result.success_count() == 1
        assert result.error_count() == 0

    def test_multiple_entries_mixed(self):
        sweeper = BatchSweeper()
        entries = [
            SweepEntry("CADGenBench", _good_cadgenbench()),
            SweepEntry("BenDFM", _good_bendfm()),
            SweepEntry("MARB / CADCLAW", _good_marb()),
        ]
        result = sweeper.run(entries)
        assert result.success_count() == 3
        assert result.error_count() == 0

    def test_unknown_adapter_recorded_as_error(self):
        sweeper = BatchSweeper()
        result = sweeper.run([SweepEntry("DoesNotExist", {})])
        assert result.error_count() == 1
        assert result.success_count() == 0

    def test_skip_unknown_suppresses_error(self):
        sweeper = BatchSweeper(skip_unknown=True)
        result = sweeper.run([SweepEntry("DoesNotExist", {})])
        assert result.error_count() == 0

    def test_bad_result_recorded_as_error(self):
        sweeper = BatchSweeper()
        # Missing mandatory field "task_id"
        result = sweeper.run([SweepEntry("CADGenBench", {"overall_score": 0.5})])
        assert result.error_count() == 1
        assert "task_id" in result.errors[0]

    def test_mixed_success_and_error(self):
        sweeper = BatchSweeper()
        entries = [
            SweepEntry("CADGenBench", _good_cadgenbench()),
            SweepEntry("CADGenBench", {}),  # missing task_id → error
        ]
        result = sweeper.run(entries)
        assert result.success_count() == 1
        assert result.error_count() == 1

    def test_stats_computed(self):
        sweeper = BatchSweeper()
        entries = [
            SweepEntry("CADGenBench", _good_cadgenbench()),
            SweepEntry("CADGenBench", {**_good_cadgenbench(), "task_id": "t2",
                                       "overall_score": 0.60}),
        ]
        result = sweeper.run(entries)
        assert "CADGenBench" in result.stats
        s = result.stats["CADGenBench"]
        assert s["count"] == 2
        assert s["avg_score"] == pytest.approx(0.70)

    def test_adapter_instance_cached(self):
        """Running twice should reuse the cached adapter instance."""
        sweeper = BatchSweeper()
        sweeper.run([SweepEntry("CADGenBench", _good_cadgenbench())])
        first = sweeper._cache.get("CADGenBench")
        sweeper.run([SweepEntry("CADGenBench", _good_cadgenbench())])
        second = sweeper._cache.get("CADGenBench")
        assert first is second  # same object

    def test_all_eight_adapters_in_batch(self):
        sweeper = BatchSweeper()
        entries = [
            SweepEntry("CADGenBench", _good_cadgenbench()),
            SweepEntry("BenDFM", _good_bendfm()),
            SweepEntry("MARB / CADCLAW", _good_marb()),
            SweepEntry("Hephaestus-CCX", {"submission_id": "s1",
                                           "scenarios": [], "overall_pass_rate": 0.8}),
            SweepEntry("FEABench", {"run_id": "r1", "questions": [], "accuracy": 0.7}),
            SweepEntry("MUSE", {"entry_id": "m1", "mixed_score": 0.75}),
            SweepEntry("UniCAD", {"run_id": "u1", "tasks": []}),
            SweepEntry("CADTestBench", {"submission_id": "c1", "tests": []}),
        ]
        result = sweeper.run(entries)
        assert result.success_count() == 8
        assert result.error_count() == 0


# ============================================================================
# sweep() convenience function
# ============================================================================

class TestSweepFunction:
    def test_basic(self):
        result = sweep([SweepEntry("CADGenBench", _good_cadgenbench())])
        assert result.success_count() == 1

    def test_skip_unknown(self):
        result = sweep(
            [SweepEntry("Ghost", {}), SweepEntry("CADGenBench", _good_cadgenbench())],
            skip_unknown=True,
        )
        assert result.success_count() == 1
        assert result.error_count() == 0

    def test_empty_entries(self):
        result = sweep([])
        assert result.success_count() == 0
        assert result.error_count() == 0
        assert result.avg_score() is None


# ============================================================================
# WatchlistEntry
# ============================================================================

class TestWatchlistEntry:
    def test_with_registry_entry(self):
        entry = _sample_entry("MUSE")
        we = WatchlistEntry(name="MUSE", registry_entry=entry)
        assert we.name == "MUSE"
        assert we.is_recent() is False
        assert "hardware-engineering" in we.axis()
        assert we.url() == "https://example.com/muse"

    def test_without_registry_entry(self):
        we = WatchlistEntry(name="Pending")
        assert we.is_recent() is False
        assert we.axis() == []
        assert we.scoring_model() == "unknown"
        assert we.url() == ""

    def test_recent_entry(self):
        entry = BenchmarkEntry(
            name="NewBench",
            domain=["code-cad"],
            task_types=[],
            scoring_model="executability",
            url="https://newbench.example",
            kind="benchmark",
            entry_type="benchmark",
            recent=True,
            raw={},
        )
        we = WatchlistEntry(name="NewBench", registry_entry=entry)
        assert we.is_recent() is True


# ============================================================================
# VolatileWatchlist
# ============================================================================

class TestVolatileWatchlist:
    def _make_wl(self) -> VolatileWatchlist:
        entries = [
            BenchmarkEntry(
                name="MUSE", domain=["code-cad"], task_types=[],
                scoring_model="mixed", url="https://muse.example",
                kind="benchmark", entry_type="benchmark", recent=True, raw={},
            ),
            BenchmarkEntry(
                name="UniCAD", domain=["hardware-engineering"], task_types=[],
                scoring_model="deterministic-geometric", url="https://unicad.example",
                kind="benchmark", entry_type="benchmark", recent=False, raw={},
            ),
        ]
        reg = BenchmarkRegistry.from_entries(entries)
        return VolatileWatchlist(
            names=["MUSE", "UniCAD", "Hephaestus-CCX"],  # Hephaestus not in registry
            registry=reg,
        )

    def test_len(self):
        wl = self._make_wl()
        assert len(wl) == 3

    def test_names(self):
        wl = self._make_wl()
        assert "MUSE" in wl.names()
        assert "UniCAD" in wl.names()

    def test_contains(self):
        wl = self._make_wl()
        assert "MUSE" in wl
        assert "DoesNotExist" not in wl

    def test_recent_entries(self):
        wl = self._make_wl()
        recent = wl.recent_entries()
        assert len(recent) == 1
        assert recent[0].name == "MUSE"

    def test_unfiled_entries(self):
        wl = self._make_wl()
        unfiled = wl.unfiled_entries()
        assert len(unfiled) == 1
        assert unfiled[0].name == "Hephaestus-CCX"

    def test_filed_entries(self):
        wl = self._make_wl()
        filed = wl.filed_entries()
        assert {e.name for e in filed} == {"MUSE", "UniCAD"}

    def test_by_axis(self):
        wl = self._make_wl()
        results = wl.by_axis("code-cad")
        assert len(results) == 1
        assert results[0].name == "MUSE"

    def test_to_dict_json_serialisable(self):
        wl = self._make_wl()
        json.dumps(wl.to_dict())

    def test_to_dict_structure(self):
        wl = self._make_wl()
        d = wl.to_dict()
        assert d["count"] == 3
        assert d["recent_count"] == 1
        assert d["unfiled_count"] == 1
        assert len(d["entries"]) == 3

    def test_summary_string(self):
        wl = self._make_wl()
        s = wl.summary()
        assert "MUSE" in s
        assert "UniCAD" in s
        assert "Hephaestus-CCX" in s

    def test_summary_flags_recent(self):
        wl = self._make_wl()
        s = wl.summary()
        assert "RECENT" in s

    def test_summary_flags_unfiled(self):
        wl = self._make_wl()
        s = wl.summary()
        assert "UNFILED" in s


# ============================================================================
# load_watchlist
# ============================================================================

class TestLoadWatchlist:
    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_watchlist_loads(self):
        wl = load_watchlist()
        assert len(wl) >= 6

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_watchlist_names(self):
        wl = load_watchlist()
        # These six names are the documented volatile watchlist.
        required = {"UniCAD", "Physics-in-the-Loop", "Hephaestus-CCX",
                    "GenCAD-3D", "CADGenBench", "MUSE"}
        assert required <= set(wl.names())

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_watchlist_all_filed(self):
        """Every volatile watchlist entry should have a landscape.yaml entry."""
        wl = load_watchlist()
        unfiled = wl.unfiled_entries()
        assert unfiled == [], (
            f"Volatile watchlist entries without a registry entry: "
            f"{[e.name for e in unfiled]} — add them to landscape.yaml"
        )

    def test_empty_watchlist_from_empty_registry(self):
        reg = BenchmarkRegistry.from_entries([])
        wl = VolatileWatchlist(names=[], registry=reg)
        assert len(wl) == 0
        assert wl.names() == []


# ============================================================================
# JSON Schema validation
# ============================================================================

class TestCommonResultSchema:
    """Validate that every adapter's output conforms to landscape/schema.json."""

    def _schema(self) -> dict[str, Any]:
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))

    def _validate(self, instance: dict[str, Any]) -> None:
        """Minimal manual schema validation without jsonschema dependency."""
        schema = self._schema()
        required = schema.get("required", [])
        for field in required:
            assert field in instance, f"required field {field!r} missing"
        # schema_version must be the const value
        assert instance.get("schema_version") == "landscape-common-result-v1"
        # score must be null or a number in [0,1]
        score = instance.get("score")
        if score is not None:
            assert isinstance(score, (int, float))
            assert 0 <= score <= 1
        # axis_coverage items must be in the allowed enum
        allowed_axes = {
            "spatial-intelligence", "hardware-engineering", "code-cad",
            "dfm", "reverse-engineering", "physics-sim",
        }
        for ax in instance.get("axis_coverage", []):
            assert ax in allowed_axes, f"invalid axis {ax!r}"
        # grading_transparency must be bool
        assert isinstance(instance.get("grading_transparency"), bool)

    def test_schema_file_exists_and_parses(self):
        schema = self._schema()
        assert schema["title"] == "Landscape CommonResult"
        assert schema["properties"]["schema_version"]["const"] == "landscape-common-result-v1"

    @pytest.mark.parametrize("adapter_name,raw_result", [
        ("CADGenBench", {"task_id": "t1", "overall_score": 0.85, "step_valid": True}),
        ("BenDFM", {"entry_id": "e1", "process": "cnc", "checks": {
            "tool_access": {"passed": True, "severity": None}}}),
        ("MARB / CADCLAW", {"assembly_id": "a1", "assembly_score": 0.9, "parts": []}),
        ("Hephaestus-CCX", {"submission_id": "s1", "scenarios": [],
                             "overall_pass_rate": 0.7}),
        ("FEABench", {"run_id": "r1", "questions": [], "accuracy": 0.65}),
        ("MUSE", {"entry_id": "m1", "mixed_score": 0.75}),
        ("UniCAD", {"run_id": "u1", "tasks": [], "accuracy": 0.80}),
        ("CADTestBench", {"submission_id": "c1", "language": "cadquery",
                           "tests": [], "overall_score": 0.9}),
    ])
    def test_adapter_output_conforms_to_schema(self, adapter_name, raw_result):
        """Each adapter's to_dict() output must satisfy landscape/schema.json."""
        from landscape.adapters import get_adapter
        adapter = get_adapter(adapter_name)
        result = adapter.adapt(raw_result)
        self._validate(result.to_dict())
