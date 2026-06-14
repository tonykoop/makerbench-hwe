"""Adapter API tests (#76).

The adapter boundary is a *public, deterministic component-score* surface for
neighboring 3D benchmarks. These tests pin the three guarantees the design note
promises: no task generation (an adapter only consumes an externally owned
artifact), determinism/transparency (identical input -> identical score with
disclosed thresholds), and component-score-only (never a `GradeResult`, never
L1-L4). They also keep the committed example JSON honest.
"""

import json
from pathlib import Path

import pytest

from makerbench.adapters import (
    BENDFM_TAXONOMY,
    AdapterDescriptor,
    BenDfmTaxonomyAdapter,
    CadGenBenchStepAdapter,
    ComponentScore,
    ExternalArtifact,
    adapter_manifest,
    builtin_adapters,
    validate_adapter_descriptor,
)

EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "examples" / "adapter_component_score.example.json"


# ---------------------------------------------------------------------------
# ExternalArtifact — the input boundary
# ---------------------------------------------------------------------------

def test_external_artifact_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown artifact kind"):
        ExternalArtifact(kind="brep", metadata={"x": 1})  # type: ignore[arg-type]


def test_external_artifact_requires_some_payload():
    with pytest.raises(ValueError, match="at least one of"):
        ExternalArtifact(kind="feature_metrics")


def test_external_artifact_rejects_non_numeric_or_bool_metrics():
    with pytest.raises(ValueError, match="must be a real number"):
        ExternalArtifact(kind="feature_metrics", feature_metrics={"min_wall_mm": "thin"})  # type: ignore[dict-item]
    # bool is an int subclass but is not a real measurement — reject it explicitly.
    with pytest.raises(ValueError, match="must be a real number"):
        ExternalArtifact(kind="feature_metrics", feature_metrics={"watertight": True})  # type: ignore[dict-item]


def test_external_artifact_accepts_real_numbers():
    art = ExternalArtifact(kind="feature_metrics", feature_metrics={"min_wall_mm": 1.4, "solid_count": 2})
    assert art.feature_metrics["min_wall_mm"] == 1.4


# ---------------------------------------------------------------------------
# TaxonomyMapping — vocabulary bridge, no task adoption
# ---------------------------------------------------------------------------

def test_taxonomy_mapping_is_bidirectional():
    assert BENDFM_TAXONOMY.to_external("printable_min_wall") == "wall_thickness"
    assert BENDFM_TAXONOMY.to_external("not_a_check") is None
    # Multiple MakerBench checks can collapse to one neighbor term; reverse is a set.
    reverse = BENDFM_TAXONOMY.from_external("wall_thickness")
    assert "printable_min_wall" in reverse and "min_wall_ok" in reverse
    assert reverse == tuple(sorted(reverse))


def test_taxonomy_coverage_counts_mapped_and_unmapped():
    cov = BENDFM_TAXONOMY.coverage(["printable_min_wall", "no_interference", "experimental_check"])
    assert cov["n_total"] == 3
    assert cov["n_mapped"] == 2
    assert cov["unmapped"] == ["experimental_check"]
    assert cov["mapped"] == ["no_interference", "printable_min_wall"]


# ---------------------------------------------------------------------------
# BenDfmTaxonomyAdapter (core) — relabel + coverage score
# ---------------------------------------------------------------------------

BENDFM_INPUT = ExternalArtifact(
    kind="feature_metrics",
    artifact_id="demo-part-A",
    metadata={
        "dfm_checks": {
            "printable_min_wall": True,
            "no_interference": True,
            "mass_under_target": False,
            "experimental_check": True,
        }
    },
)


def test_bendfm_adapter_relabels_and_scores_coverage():
    score = BenDfmTaxonomyAdapter().score(BENDFM_INPUT)
    assert isinstance(score, ComponentScore)
    assert score.component_score_only is True
    assert score.kind == "taxonomy_mapping"
    # 3 of 4 disclosed checks pass; unmapped checks are still reported as checks.
    assert (score.score, score.max_score) == (3.0, 4.0)
    assert score.fraction == 0.75
    cov = score.metrics["coverage"]
    assert cov["n_mapped"] == 3 and cov["unmapped"] == ["experimental_check"]
    # Per-term rollup: material_budget fails because mass_under_target is False.
    assert score.metrics["taxonomy_terms"] == {
        "interference": True,
        "material_budget": False,
        "wall_thickness": True,
    }


def test_bendfm_adapter_descriptor_is_core_and_valid():
    desc = BenDfmTaxonomyAdapter().descriptor()
    assert desc.runtime_class == "core"
    assert desc.dependencies == ()
    assert validate_adapter_descriptor(desc) == []


# ---------------------------------------------------------------------------
# CadGenBenchStepAdapter — feature_metrics core path
# ---------------------------------------------------------------------------

def test_cadgenbench_scores_feature_metrics_against_public_thresholds():
    art = ExternalArtifact(
        kind="feature_metrics",
        feature_metrics={"watertight": 1, "solid_count": 2, "min_wall_mm": 1.4, "mass_g": 31.2},
    )
    score = CadGenBenchStepAdapter().score(art)
    assert score.available is True
    assert score.kind == "dfm_component"
    assert (score.score, score.max_score) == (3.0, 3.0)
    names = {c.name for c in score.checks}
    assert names == {"watertight", "solid_count", "min_wall"}
    # mass_g is carried in metrics but is not a scored DFM check.
    assert "mass_g" in score.metrics and score.metrics["mass_g"] == 31.2


def test_cadgenbench_min_wall_threshold_fails_thin_walls():
    art = ExternalArtifact(kind="feature_metrics", feature_metrics={"min_wall_mm": 0.4})
    score = CadGenBenchStepAdapter(min_wall_mm=1.0).score(art)
    assert score.fraction == 0.0
    (check,) = score.checks
    assert check.name == "min_wall" and check.ok is False


def test_cadgenbench_respects_max_solids_and_watertight_config():
    art = ExternalArtifact(kind="feature_metrics", feature_metrics={"watertight": 0, "solid_count": 5})
    # require_watertight=False drops the watertight check; max_solids=2 fails 5 solids.
    score = CadGenBenchStepAdapter(require_watertight=False, max_solids=2).score(art)
    names = {c.name for c in score.checks}
    assert names == {"solid_count"}
    (check,) = score.checks
    assert check.ok is False and "<=2" in str(check.expected)


def test_cadgenbench_rejects_min_wall_config_of_zero():
    with pytest.raises(ValueError, match="min_wall_mm must be positive"):
        CadGenBenchStepAdapter(min_wall_mm=0)


def test_cadgenbench_empty_metrics_is_scoreless_but_available():
    art = ExternalArtifact(kind="feature_metrics", feature_metrics={"mass_g": 10.0})
    score = CadGenBenchStepAdapter().score(art)
    assert score.checks == ()
    assert score.fraction is None  # nothing to score -> not zero
    assert "no scorable metrics" in score.reason


def test_cadgenbench_rejects_wrong_artifact_kind():
    art = ExternalArtifact(kind="vector", path="part.svg")
    with pytest.raises(ValueError, match="consumes"):
        CadGenBenchStepAdapter().score(art)


# ---------------------------------------------------------------------------
# CadGenBenchStepAdapter — optional_local STEP path (degrades honestly)
# ---------------------------------------------------------------------------

def test_cadgenbench_step_path_degrades_when_extractor_absent(monkeypatch):
    from makerbench import brep_profile

    monkeypatch.setattr(brep_profile, "step_topology_summary", lambda path: {"available": False})
    art = ExternalArtifact(kind="step", path="missing.step")
    score = CadGenBenchStepAdapter().score(art)
    # Absent build123d/OCCT => skipped, never a misleading zero.
    assert score.available is False
    assert score.fraction is None
    assert "build123d" in score.reason


def test_cadgenbench_step_path_extracts_and_scores_when_available(monkeypatch):
    from makerbench import brep_profile

    monkeypatch.setattr(
        brep_profile,
        "step_topology_summary",
        lambda path: {"available": True, "watertight": True, "solid_count": 1, "face_count": 6, "bbox_mm": [10, 20, 30]},
    )
    art = ExternalArtifact(kind="step", path="part.step")
    score = CadGenBenchStepAdapter().score(art)
    assert score.available is True
    # watertight + solid_count are scorable; bbox is carried as min_bbox_mm metric.
    assert {c.name for c in score.checks} == {"watertight", "solid_count"}
    assert score.metrics["min_bbox_mm"] == 10.0


def test_cadgenbench_step_path_without_file_raises(monkeypatch):
    from makerbench import brep_profile

    # Force the extraction branch (no feature_metrics) but give no path.
    monkeypatch.setattr(brep_profile, "step_topology_summary", lambda path: {"available": True})
    art = ExternalArtifact(kind="step", metadata={"note": "no path"})
    with pytest.raises(ValueError, match="needs a path"):
        CadGenBenchStepAdapter().score(art)


# ---------------------------------------------------------------------------
# validate_adapter_descriptor — manifest honesty
# ---------------------------------------------------------------------------

def test_validate_flags_core_adapter_with_dependencies():
    bad = AdapterDescriptor(
        adapter_id="x", version="1", summary="s", consumes=("step",),
        produces="dfm_component", runtime_class="core", dependencies=("build123d",),
    )
    problems = validate_adapter_descriptor(bad)
    assert any("stdlib-only" in p for p in problems)


def test_validate_flags_optional_local_without_dependencies():
    bad = AdapterDescriptor(
        adapter_id="x", version="1", summary="s", consumes=("step",),
        produces="dfm_component", runtime_class="optional_local",
    )
    assert any("must declare the" in p for p in validate_adapter_descriptor(bad))


def test_validate_flags_unknown_runtime_class_and_missing_fields():
    bad = AdapterDescriptor(
        adapter_id="", version="", summary="s", consumes=(),
        produces="dfm_component", runtime_class="wizard",  # type: ignore[arg-type]
    )
    problems = validate_adapter_descriptor(bad)
    assert any("adapter_id" in p for p in problems)
    assert any("version is required" in p for p in problems)
    assert any("at least one consumed kind" in p for p in problems)
    assert any("runtime_class" in p for p in problems)


def test_validate_flags_non_deterministic_core_adapter():
    bad = AdapterDescriptor(
        adapter_id="x", version="1", summary="s", consumes=("step",),
        produces="dfm_component", runtime_class="core", deterministic=False,
    )
    assert any("must be deterministic" in p for p in validate_adapter_descriptor(bad))


# ---------------------------------------------------------------------------
# Registry + manifest
# ---------------------------------------------------------------------------

def test_builtin_adapters_have_valid_descriptors():
    descs = [a.descriptor() for a in builtin_adapters()]
    assert {d.adapter_id for d in descs} == {"bendfm-taxonomy", "cadgenbench-step-dfm"}
    for d in descs:
        assert validate_adapter_descriptor(d) == []


def test_adapter_manifest_is_json_serializable():
    manifest = adapter_manifest()
    # round-trips through JSON unchanged -> safe to publish in a manifest.
    assert json.loads(json.dumps(manifest)) == manifest


# ---------------------------------------------------------------------------
# Determinism + serialization (the core promise)
# ---------------------------------------------------------------------------

def test_component_score_is_deterministic():
    a = BenDfmTaxonomyAdapter().score(BENDFM_INPUT).as_dict()
    b = BenDfmTaxonomyAdapter().score(BENDFM_INPUT).as_dict()
    assert a == b
    assert json.loads(json.dumps(a)) == a


def test_component_score_never_claims_to_be_a_grade_result():
    for adapter in builtin_adapters():
        desc = adapter.descriptor()
        assert desc.deterministic is True
    score = CadGenBenchStepAdapter().score(
        ExternalArtifact(kind="feature_metrics", feature_metrics={"watertight": 1})
    )
    d = score.as_dict()
    assert d["component_score_only"] is True
    # The boundary contract: no L1-L4 / GradeResult fields leak in.
    assert "compute_score" not in d and "level" not in d


# ---------------------------------------------------------------------------
# The committed example JSON stays honest
# ---------------------------------------------------------------------------

def test_example_json_matches_regenerated_output():
    example = json.loads(EXAMPLE_PATH.read_text())

    assert example["adapter_manifest"] == adapter_manifest()

    bendfm = BenDfmTaxonomyAdapter().score(BENDFM_INPUT).as_dict()
    assert example["examples"]["bendfm_taxonomy_mapping"] == bendfm

    step_input = ExternalArtifact(
        kind="feature_metrics",
        feature_metrics={"watertight": 1, "solid_count": 2, "min_wall_mm": 1.4, "mass_g": 31.2},
    )
    step = CadGenBenchStepAdapter().score(step_input).as_dict()
    assert example["examples"]["cadgenbench_step_dfm_component"] == step
