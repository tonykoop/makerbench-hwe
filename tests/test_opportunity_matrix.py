"""Tests for the Opportunity Matrix cube + vacancy detector (#120)."""

import json
from pathlib import Path

import pytest

from makerbench import opportunity_matrix as om

ROOT = Path(__file__).resolve().parents[1]


def _write_result(tmp_path: Path, model: str, scores: dict[str, int], track: str = "blind") -> Path:
    """Write a minimal RunResults bundle under results/<model>/."""
    run_dir = tmp_path / "results" / model
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "benchmark_version": "0.1.0",
        "model_identifier": model,
        "results": [
            {"task_id": tid, "seed": 1, "track": track, "grade": {"score": s}}
            for tid, s in scores.items()
        ],
    }
    path = run_dir / f"{track}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _write_manifest(run_dir: Path, *, orchestrator, host, bridge, score=None, autonomy=None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "0.1",
        "stack": {
            "orchestrator": orchestrator,
            "host_application": host,
            "execution_bridge": bridge,
        },
    }
    if score is not None:
        manifest["dossier"] = {"verification": {"score": score}}
    if autonomy is not None:
        manifest["hii"] = {"autonomy_ratio": autonomy}
    path = run_dir / "workflow_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


# --- weights / catalog integrity ------------------------------------------------


def test_weights_sum_to_one():
    assert abs(sum(om.WEIGHTS.values()) - 1.0) < 1e-9


def test_catalog_ids_unique():
    cat = om.Catalog()
    for entries in (cat.models, cat.cad_hosts, cat.plugins, cat.domains):
        ids = [e["id"] for e in entries]
        assert len(ids) == len(set(ids))


def test_plugin_priors_are_normalized():
    cat = om.Catalog()
    for plugin in cat.plugins:
        for key in ("openness", "ease", "autonomy_affinity"):
            assert 0.0 <= plugin[key] <= 1.0
        assert plugin.get("hosts"), f"{plugin['id']} must declare hosts"


# --- axis matching --------------------------------------------------------------


def test_match_model_by_token():
    cat = om.Catalog()
    assert om._match_axis(cat.models, "claude-code-opus-4.8", by_match=True) == "claude-opus"
    assert om._match_axis(cat.models, "codex-gpt-5.4-high", by_match=True) == "gpt-5"
    assert om._match_axis(cat.models, "totally-unknown-model", by_match=True) is None


def test_component_name_handles_str_and_dict():
    assert om._component_name("Blender") == "blender"
    assert om._component_name({"name": "blender-mcp", "version": "1.2"}) == "blender-mcp"
    assert om._component_name(None) is None


# --- capability prior from results ---------------------------------------------


def test_model_capability_prior_from_results(tmp_path):
    _write_result(tmp_path, "claude-code-opus-4.8", {"vented_plate": 4, "enclosure": 2})
    priors = om.load_model_capability(tmp_path / "results")
    # mean(4, 2) / 4 == 0.75
    assert priors["claude-opus"] == pytest.approx(0.75)
    # unmatched model falls back to its catalog prior
    assert priors["qwen"] == pytest.approx(0.35)


def test_capability_prior_ignores_perception_and_agent_errors(tmp_path):
    _write_result(tmp_path, "claude-code-opus-4.8", {"a": 4}, track="blind")
    _write_result(tmp_path, "claude-code-opus-4.8", {"b": 1}, track="perception")
    priors = om.load_model_capability(tmp_path / "results")
    assert priors["claude-opus"] == pytest.approx(1.0)  # only blind 4 counts


# --- cube assembly --------------------------------------------------------------


def test_build_cube_no_evidence_is_all_vacancy():
    cube = build = om.build_cube()
    assert cube["schema"] == om.SCHEMA
    assert cube["counts"]["proven"] == 0
    assert cube["counts"]["vacancies"] == cube["counts"]["coordinates"]
    assert all(c["is_vacancy"] for c in cube["coordinates"])


def test_incompatible_plugins_pruned():
    cube = om.build_cube()
    # blender-mcp may only sit on the blender host
    bad = [c for c in cube["coordinates"]
           if c["plugin"] == "blender-mcp" and c["cad"] != "blender"]
    assert bad == []
    # code-first "none" applies to every host
    none_hosts = {c["cad"] for c in cube["coordinates"] if c["plugin"] == "none"}
    assert none_hosts == {c["id"] for c in om.Catalog().cad_hosts}


def test_open_codefirst_outranks_closed_gui_at_equal_capability():
    cube = om.build_cube()
    by_key = {(c["model"], c["cad"], c["plugin"]): c for c in cube["coordinates"]}
    openscad = by_key[("claude-opus", "openscad", "none")]
    solidworks = by_key[("claude-opus", "solidworks", "solidworks-api")]
    # same capability prior, but the open code-first stack must score higher
    assert openscad["potential_score"] > solidworks["potential_score"]


def test_evidence_creates_proven_coordinate(tmp_path):
    _write_result(tmp_path, "claude-code-opus-4.8", {"vented_plate": 4})
    _write_manifest(
        tmp_path / "runs" / "run1",
        orchestrator="claude-code-opus-4.8",
        host="blender",
        bridge={"name": "blender-mcp", "version": "1.0"},
        score=4,
        autonomy=1.0,
    )
    cube = om.build_cube(results_dir=tmp_path / "results", runs_root=tmp_path / "runs")
    assert cube["counts"]["proven"] == 1
    proven = cube["top_proven"][0]
    assert (proven["model"], proven["cad"], proven["plugin"]) == ("claude-opus", "blender", "blender-mcp")
    assert proven["has_evidence"] and not proven.get("is_vacancy")
    assert proven["capability"] == pytest.approx(1.0)
    assert proven["opportunity_score"] is not None


def test_vacancies_ranked_by_potential(tmp_path):
    cube = om.build_cube()
    vacancies = cube["top_vacancies"]
    pots = [v["potential_score"] for v in vacancies]
    assert pots == sorted(pots, reverse=True)
    # the single highest-potential empty cell should be a fully-open code-first stack
    top = vacancies[0]
    assert top["plugin"] == "none"


def test_with_domain_expands_cube_and_sets_domain():
    base = om.build_cube()
    dom = om.build_cube(with_domain=True)
    assert dom["counts"]["coordinates"] > base["counts"]["coordinates"]
    assert all(c["domain"] is not None for c in dom["coordinates"])
    assert dom["axes"]["domain"]


def test_coordinate_label():
    coord = {"model": "claude-opus", "cad": "blender", "plugin": "blender-mcp", "domain": None}
    label = om.coordinate_label(coord)
    assert "Claude Opus" in label and "Blender" in label and "MCP" in label


def test_cube_is_deterministic():
    a = om.build_cube()
    b = om.build_cube()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
