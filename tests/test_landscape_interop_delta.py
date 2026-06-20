"""Tests for landscape.interop_matrix, landscape.delta, and example fixtures (epic #242).

Covers:
* landscape.interop_matrix  — AdapterCoverageRow, InteropMatrix, build_interop_matrix
* landscape.delta           — FieldChange, EntryDelta, LandscapeDelta, diff_registries
* landscape/examples/*.json — every example file round-trips through its adapter
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from landscape.registry import BenchmarkEntry, BenchmarkRegistry, AXIS_VOCAB
from landscape.interop_matrix import AdapterCoverageRow, InteropMatrix, build_interop_matrix
from landscape.delta import (
    FieldChange,
    EntryDelta,
    LandscapeDelta,
    diff_registries,
)
from landscape.adapters import adapter_registry, get_adapter

YAML_AVAILABLE = False
try:
    import yaml  # noqa: F401
    YAML_AVAILABLE = True
except ImportError:
    pass

_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLES_DIR = _ROOT / "landscape" / "examples"


# ============================================================================
# Helpers
# ============================================================================

def _bench(name: str, domain: list[str], scoring: str = "deterministic-geometric",
           recent: bool = False, raw_extra: dict | None = None) -> BenchmarkEntry:
    raw: dict[str, Any] = {"name": name, "axis": domain, "grading": scoring,
                            "openness": "MIT", "agentic": "single-shot",
                            "anti_memorization": "", "dataset_scale": "small"}
    if raw_extra:
        raw.update(raw_extra)
    return BenchmarkEntry(
        name=name, domain=domain, task_types=[],
        scoring_model=scoring, url=f"https://example.com/{name.lower()}",
        kind="benchmark", entry_type="benchmark", recent=recent, raw=raw,
    )


def _small_reg(*names_and_domains) -> BenchmarkRegistry:
    """Helper: BenchmarkRegistry.from_entries([bench(n,d), ...])."""
    entries = []
    for name, domain in names_and_domains:
        entries.append(_bench(name, domain))
    return BenchmarkRegistry.from_entries(entries)


# ============================================================================
# InteropMatrix
# ============================================================================

class TestAdapterCoverageRow:
    def _row(self, axes: list[str], in_landscape: bool = True) -> AdapterCoverageRow:
        return AdapterCoverageRow(
            adapter_name="Test",
            source_benchmark="Test",
            axis_coverage=axes,
            in_landscape=in_landscape,
        )

    def test_covers_axis_true(self):
        r = self._row(["dfm", "hardware-engineering"])
        assert r.covers_axis("dfm") is True

    def test_covers_axis_false(self):
        r = self._row(["dfm"])
        assert r.covers_axis("physics-sim") is False

    def test_axis_overlap(self):
        r = self._row(["dfm", "hardware-engineering"])
        overlap = r.axis_overlap_with(["dfm", "code-cad"])
        assert overlap == ["dfm"]

    def test_axis_overlap_empty(self):
        r = self._row(["physics-sim"])
        assert r.axis_overlap_with(["dfm"]) == []


class TestInteropMatrix:
    def _matrix(self) -> InteropMatrix:
        """Matrix backed by synthetic adapter registry + empty landscape registry."""
        reg = BenchmarkRegistry.from_entries([])
        return InteropMatrix(registry=reg)

    def test_rows_non_empty(self):
        m = self._matrix()
        assert len(m.rows()) >= 8

    def test_by_axis_dfm(self):
        m = self._matrix()
        rows = m.by_axis("dfm")
        # BenDFMAdapter covers dfm
        names = [r.adapter_name for r in rows]
        assert "BenDFM" in names

    def test_by_axis_physics(self):
        m = self._matrix()
        rows = m.by_axis("physics-sim")
        names = [r.adapter_name for r in rows]
        assert "FEABench" in names
        assert "Hephaestus-CCX" in names

    def test_axes_with_adapters_subset_of_vocab(self):
        m = self._matrix()
        covered = set(m.axes_with_adapters())
        assert covered <= AXIS_VOCAB

    def test_axes_without_adapters_disjoint_from_covered(self):
        m = self._matrix()
        covered = set(m.axes_with_adapters())
        uncovered = set(m.axes_without_adapters())
        assert covered & uncovered == set()

    def test_axes_cover_all_vocab(self):
        m = self._matrix()
        covered = set(m.axes_with_adapters())
        uncovered = set(m.axes_without_adapters())
        assert covered | uncovered == AXIS_VOCAB

    def test_deterministic_adapters_non_empty(self):
        m = self._matrix()
        det = m.deterministic_adapters()
        names = [r.adapter_name for r in det]
        assert "CADGenBench" in names
        assert "MARB / CADCLAW" in names

    def test_nondeterministic_has_muse(self):
        """MUSE's registry entry declares 'mixed' grading → non-deterministic."""
        # Build a registry with MUSE's scoring model = "mixed"
        muse_entry = _bench("MUSE", ["code-cad", "spatial-intelligence"],
                            scoring="mixed")
        reg = BenchmarkRegistry.from_entries([muse_entry])
        m = InteropMatrix(registry=reg)
        nd = m.nondeterministic_adapters()
        names = [r.adapter_name for r in nd]
        assert "MUSE" in names

    def test_adapters_not_in_landscape_all_when_empty_registry(self):
        m = self._matrix()  # empty registry → all adapters not in landscape
        not_in = m.adapters_not_in_landscape()
        assert len(not_in) == len(m.rows())

    def test_adapters_in_landscape_when_registry_has_entry(self):
        reg = BenchmarkRegistry.from_entries([
            _bench("CADGenBench", ["code-cad"]),
        ])
        m = InteropMatrix(registry=reg)
        in_landscape = m.adapters_in_landscape()
        names = [r.adapter_name for r in in_landscape]
        assert "CADGenBench" in names

    def test_to_dict_json_serialisable(self):
        m = self._matrix()
        import json
        json.dumps(m.to_dict())

    def test_to_dict_structure(self):
        m = self._matrix()
        d = m.to_dict()
        assert "adapter_count" in d
        assert "axes_with_adapters" in d
        assert "axes_without_adapters" in d
        assert "rows" in d

    def test_to_markdown_table_has_all_adapters(self):
        m = self._matrix()
        md = m.to_markdown_table()
        for name in adapter_registry:
            assert name in md

    def test_to_markdown_table_has_all_axes(self):
        m = self._matrix()
        md = m.to_markdown_table()
        for ax in AXIS_VOCAB:
            assert ax in md

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_matrix_all_axes_covered(self):
        m = build_interop_matrix()
        uncovered = m.axes_without_adapters()
        # With 9 adapters, every documented axis should be covered
        assert uncovered == [], (
            f"Axes lacking any interop adapter: {uncovered}"
        )

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_matrix_makerbench_axes_covered(self):
        m = build_interop_matrix()
        mb_uncovered = m.makerbench_axes_without_adapters()
        assert mb_uncovered == [], (
            f"MakerBench-HWE axes lacking any interop adapter: {mb_uncovered}"
        )

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_all_adapters_in_landscape(self):
        """Every built-in adapter's source benchmark must be in landscape.yaml."""
        m = build_interop_matrix()
        not_in = m.adapters_not_in_landscape()
        assert not_in == [], (
            f"Adapter source benchmarks missing from landscape.yaml: "
            f"{[r.source_benchmark for r in not_in]}"
        )


# ============================================================================
# delta tests
# ============================================================================

class TestFieldChange:
    def test_meaningful_true(self):
        fc = FieldChange("grading", "mixed", "deterministic-geometric")
        assert fc.is_meaningful() is True

    def test_meaningful_false_whitespace(self):
        fc = FieldChange("grading", "mixed", "  mixed  ")
        assert fc.is_meaningful() is False

    def test_equality(self):
        a = FieldChange("f", "x", "y")
        b = FieldChange("f", "x", "y")
        assert a == b


class TestEntryDelta:
    def test_added(self):
        d = EntryDelta("Foo", "added")
        assert d.is_added() is True
        assert d.is_removed() is False
        assert d.is_modified() is False
        assert d.is_unchanged() is False

    def test_removed(self):
        d = EntryDelta("Foo", "removed")
        assert d.is_removed() is True

    def test_modified_with_changes(self):
        d = EntryDelta("Foo", "modified",
                       changes=[FieldChange("grading", "mixed", "det")])
        assert d.is_modified() is True
        assert d.changed_fields() == ["grading"]

    def test_unchanged(self):
        d = EntryDelta("Foo", "unchanged")
        assert d.is_unchanged() is True

    def test_changed_fields_empty_for_added(self):
        d = EntryDelta("Foo", "added")
        assert d.changed_fields() == []


class TestLandscapeDelta:
    def _make_delta(self) -> LandscapeDelta:
        return LandscapeDelta(
            before_count=3,
            after_count=4,
            deltas=[
                EntryDelta("A", "unchanged"),
                EntryDelta("B", "modified",
                           changes=[FieldChange("grading", "mixed", "det")]),
                EntryDelta("C", "removed"),
                EntryDelta("D", "added"),
            ],
        )

    def test_added(self):
        d = self._make_delta()
        assert [e.name for e in d.added()] == ["D"]

    def test_removed(self):
        d = self._make_delta()
        assert [e.name for e in d.removed()] == ["C"]

    def test_modified(self):
        d = self._make_delta()
        assert [e.name for e in d.modified()] == ["B"]

    def test_unchanged(self):
        d = self._make_delta()
        assert [e.name for e in d.unchanged()] == ["A"]

    def test_net_change(self):
        d = self._make_delta()
        assert d.net_change() == 0  # +1 added, -1 removed

    def test_has_changes_true(self):
        d = self._make_delta()
        assert d.has_changes() is True

    def test_has_changes_false_all_unchanged(self):
        d = LandscapeDelta(
            before_count=2, after_count=2,
            deltas=[EntryDelta("A", "unchanged"), EntryDelta("B", "unchanged")],
        )
        assert d.has_changes() is False

    def test_summary_contains_counts(self):
        d = self._make_delta()
        s = d.summary()
        assert "+1" in s
        assert "-1" in s
        assert "~1" in s
        assert "=1" in s

    def test_to_dict_json_serialisable(self):
        d = self._make_delta()
        json.dumps(d.to_dict())

    def test_to_dict_structure(self):
        d = self._make_delta()
        dd = d.to_dict()
        assert "added" in dd
        assert "removed" in dd
        assert "modified" in dd
        assert dd["added"] == ["D"]
        assert dd["removed"] == ["C"]

    def test_to_text(self):
        d = self._make_delta()
        text = d.to_text()
        assert "+ D" in text
        assert "- C" in text
        assert "~ B" in text


class TestDiffRegistries:
    def test_identical_registries_no_changes(self):
        reg = _small_reg(("A", ["dfm"]), ("B", ["code-cad"]))
        delta = diff_registries(reg, reg)
        assert not delta.has_changes()
        assert delta.net_change() == 0

    def test_added_entry(self):
        before = _small_reg(("A", ["dfm"]))
        after = _small_reg(("A", ["dfm"]), ("B", ["code-cad"]))
        delta = diff_registries(before, after)
        assert len(delta.added()) == 1
        assert delta.added()[0].name == "B"
        assert delta.net_change() == 1

    def test_removed_entry(self):
        before = _small_reg(("A", ["dfm"]), ("B", ["code-cad"]))
        after = _small_reg(("A", ["dfm"]))
        delta = diff_registries(before, after)
        assert len(delta.removed()) == 1
        assert delta.removed()[0].name == "B"
        assert delta.net_change() == -1

    def test_modified_grading(self):
        before = BenchmarkRegistry.from_entries([
            _bench("A", ["dfm"], scoring="mixed"),
        ])
        after = BenchmarkRegistry.from_entries([
            _bench("A", ["dfm"], scoring="deterministic-geometric"),
        ])
        delta = diff_registries(before, after)
        assert len(delta.modified()) == 1
        assert "grading" in delta.modified()[0].changed_fields()

    def test_modified_axis(self):
        before = BenchmarkRegistry.from_entries([
            _bench("A", ["dfm"]),
        ])
        after = BenchmarkRegistry.from_entries([
            _bench("A", ["dfm", "code-cad"]),
        ])
        delta = diff_registries(before, after)
        assert len(delta.modified()) == 1
        assert "axis" in delta.modified()[0].changed_fields()

    def test_modified_openness(self):
        before = BenchmarkRegistry.from_entries([
            _bench("A", ["dfm"], raw_extra={"openness": "proprietary"}),
        ])
        after = BenchmarkRegistry.from_entries([
            _bench("A", ["dfm"], raw_extra={"openness": "Apache-2.0"}),
        ])
        delta = diff_registries(before, after)
        mod = delta.modified()
        assert len(mod) == 1
        assert "openness" in mod[0].changed_fields()

    def test_counts(self):
        before = _small_reg(("A", ["dfm"]), ("B", ["code-cad"]))
        after = _small_reg(("A", ["dfm"]), ("C", ["physics-sim"]))
        delta = diff_registries(before, after)
        assert delta.before_count == 2
        assert delta.after_count == 2
        assert len(delta.added()) == 1
        assert len(delta.removed()) == 1
        assert len(delta.unchanged()) == 1

    def test_empty_before(self):
        before = BenchmarkRegistry.from_entries([])
        after = _small_reg(("A", ["dfm"]), ("B", ["code-cad"]))
        delta = diff_registries(before, after)
        assert len(delta.added()) == 2
        assert not delta.removed()

    def test_empty_after(self):
        before = _small_reg(("A", ["dfm"]))
        after = BenchmarkRegistry.from_entries([])
        delta = diff_registries(before, after)
        assert len(delta.removed()) == 1
        assert not delta.added()

    @pytest.mark.skipif(not YAML_AVAILABLE, reason="PyYAML not installed")
    def test_real_same_registry_no_changes(self):
        from landscape.registry import BenchmarkRegistry
        reg = BenchmarkRegistry()
        delta = diff_registries(reg, reg)
        assert not delta.has_changes()

    def test_to_dict_json_serialisable(self):
        before = _small_reg(("A", ["dfm"]))
        after = _small_reg(("A", ["dfm"]), ("B", ["code-cad"]))
        delta = diff_registries(before, after)
        json.dumps(delta.to_dict())


# ============================================================================
# Example fixtures round-trip tests
# ============================================================================

class TestExampleFixtures:
    """Each example JSON in landscape/examples/ must round-trip through its adapter."""

    _FIXTURE_ADAPTER_MAP = {
        "cadgenbench_result.json": "CADGenBench",
        "bendfm_result.json": "BenDFM",
        "marb_result.json": "MARB / CADCLAW",
        "hephaestus_result.json": "Hephaestus-CCX",
        "feabench_result.json": "FEABench",
        "eribench_result.json": "ERI Benchmark",
    }

    def test_all_fixture_files_exist(self):
        for fname in self._FIXTURE_ADAPTER_MAP:
            path = _EXAMPLES_DIR / fname
            assert path.exists(), f"example fixture missing: {path}"

    @pytest.mark.parametrize("fname,adapter_name", [
        ("cadgenbench_result.json", "CADGenBench"),
        ("bendfm_result.json", "BenDFM"),
        ("marb_result.json", "MARB / CADCLAW"),
        ("hephaestus_result.json", "Hephaestus-CCX"),
        ("feabench_result.json", "FEABench"),
        ("eribench_result.json", "ERI Benchmark"),
    ])
    def test_fixture_round_trips_through_adapter(self, fname, adapter_name):
        path = _EXAMPLES_DIR / fname
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Strip the _comment key (not part of the actual schema)
        raw.pop("_comment", None)

        adapter = get_adapter(adapter_name)
        result = adapter.adapt(raw)

        assert result.source_benchmark == adapter_name
        assert result.artifact_id  # non-empty
        # Score must be None or [0,1]
        if result.score is not None:
            assert 0 <= result.score <= 1
        # to_dict() must be JSON-serialisable
        json.dumps(result.to_dict())

    def test_cadgenbench_fixture_score(self):
        raw = json.loads((_EXAMPLES_DIR / "cadgenbench_result.json").read_text())
        raw.pop("_comment", None)
        r = get_adapter("CADGenBench").adapt(raw)
        assert r.score == pytest.approx(0.87)

    def test_bendfm_fixture_fail_rate(self):
        raw = json.loads((_EXAMPLES_DIR / "bendfm_result.json").read_text())
        raw.pop("_comment", None)
        r = get_adapter("BenDFM").adapt(raw)
        # 3 of 5 checks pass → pass_rate = 3/5 = 0.6
        assert r.dimensions["pass_rate"] == pytest.approx(0.6)

    def test_marb_fixture_part_count(self):
        raw = json.loads((_EXAMPLES_DIR / "marb_result.json").read_text())
        raw.pop("_comment", None)
        r = get_adapter("MARB / CADCLAW").adapt(raw)
        assert r.dimensions["part_count"] == 4
        assert r.dimensions["interference_free_count"] == 3

    def test_hephaestus_fixture_pass_rate(self):
        raw = json.loads((_EXAMPLES_DIR / "hephaestus_result.json").read_text())
        raw.pop("_comment", None)
        r = get_adapter("Hephaestus-CCX").adapt(raw)
        assert r.score == pytest.approx(2 / 3)

    def test_feabench_fixture_accuracy(self):
        raw = json.loads((_EXAMPLES_DIR / "feabench_result.json").read_text())
        raw.pop("_comment", None)
        r = get_adapter("FEABench").adapt(raw)
        # 3 of 5 correct → accuracy = 0.6
        assert r.score == pytest.approx(0.6)

    def test_eribench_fixture_score_and_axes(self):
        raw = json.loads((_EXAMPLES_DIR / "eribench_result.json").read_text())
        raw.pop("_comment", None)
        r = get_adapter("ERI Benchmark").adapt(raw)
        # overall_score from fixture is 0.73
        assert r.score == pytest.approx(0.73)
        assert r.dimensions["task_count"] == 3
        assert r.dimensions["reconstructed_count"] == 2
        assert "reverse-engineering" in r.axis_coverage
        assert "spatial-intelligence" in r.axis_coverage
        assert r.grading_transparency is True
