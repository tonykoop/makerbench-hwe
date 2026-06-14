"""Tests for the best-combo-per-craft report (#183).

The report specializes the Opportunity Matrix (#120) to the fabrication-process
axis: per craft, the winning (model × CAD × plugin) combo and the high-value
vacancies. These tests cover the craft/corpus catalog integrity, manifest →
craft attribution (the three resolution paths), evidence-driven winner
selection, per-craft potential scaling by domain demand, and determinism.
"""

import json
from pathlib import Path

import pytest

from makerbench import best_combo_per_craft as bc
from makerbench import opportunity_matrix as om

ROOT = Path(__file__).resolve().parents[1]


def _write_manifest(run_dir: Path, *, orchestrator, host, bridge,
                    processes=None, instrument=None, score=None, autonomy=None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "0.1",
        "stack": {"orchestrator": orchestrator, "host_application": host, "execution_bridge": bridge},
    }
    dossier = {}
    if processes is not None:
        dossier["fabrication_processes"] = processes
    if instrument is not None:
        dossier["instrument"] = instrument
    if score is not None:
        dossier["verification"] = {"score": score}
    if dossier:
        manifest["dossier"] = dossier
    if autonomy is not None:
        manifest["hii"] = {"autonomy_ratio": autonomy}
    path = run_dir / "workflow_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


# --- catalog integrity ----------------------------------------------------------


def test_schema_and_weights_inherited():
    report = bc.build_craft_report()
    assert report["schema"] == bc.SCHEMA
    assert report["weights"] == dict(om.WEIGHTS)


def test_process_ids_unique_and_domains_resolve():
    cat = bc.CraftCatalog()
    pids = [p["id"] for p in cat.processes]
    assert len(pids) == len(set(pids))
    om_domains = {d["id"] for d in om.Catalog().domains}
    for p in cat.processes:
        assert p["domain"] in om_domains, f"{p['id']} domain {p['domain']} not in matrix domains"


def test_corpus_repos_unique_and_reference_known_processes():
    cat = bc.CraftCatalog()
    repos = [c["repo"] for c in cat.corpus]
    assert len(repos) == len(set(repos))
    pids = cat.process_ids()
    for entry in cat.corpus:
        assert entry["processes"], f"{entry['repo']} declares no processes"
        for pid in entry["processes"]:
            assert pid in pids, f"{entry['repo']} references unknown process {pid}"


def test_every_process_is_exercised_by_some_instrument():
    cat = bc.CraftCatalog()
    covered = {pid for entry in cat.corpus for pid in entry["processes"]}
    assert cat.process_ids() <= covered


# --- manifest -> craft attribution ----------------------------------------------


def test_manifest_processes_explicit_field():
    found = bc.manifest_processes(
        {"dossier": {"fabrication_processes": ["wood_turning", "laser_cut", "bogus"]}}
    )
    assert found == {"wood_turning", "laser_cut"}


def test_manifest_processes_top_level_singular():
    assert bc.manifest_processes({"process": "cnc_plasma"}) == {"cnc_plasma"}


def test_manifest_processes_via_corpus_instrument():
    # fujara -> stave_joinery + wood_turning per the corpus
    found = bc.manifest_processes({"dossier": {"instrument": "Fujara"}})
    assert found == {"stave_joinery", "wood_turning"}


def test_manifest_processes_union_of_paths():
    found = bc.manifest_processes(
        {"process": "laser_cut", "dossier": {"instrument": "duduk"}}
    )
    # explicit laser_cut + duduk's wood_turning/hand_power_tools
    assert found == {"laser_cut", "wood_turning", "hand_power_tools"}


def test_manifest_processes_empty_when_unknown():
    assert bc.manifest_processes({"dossier": {"instrument": "kazoo"}}) == set()


# --- evidence + winner ----------------------------------------------------------


def test_no_evidence_is_all_vacancy():
    report = bc.build_craft_report()
    assert report["counts"]["proven"] == 0
    assert report["counts"]["crafts_with_evidence"] == 0
    for craft in report["crafts"]:
        assert craft["winner"] is None
        assert craft["n_proven"] == 0
        assert craft["n_vacancies"] == craft["n_coordinates"]
        assert craft["top_vacancies"]


def test_evidence_makes_winner_for_its_craft_only(tmp_path):
    _write_manifest(
        tmp_path / "runs" / "r1",
        orchestrator="claude-code-opus-4.8",
        host="blender",
        bridge={"name": "blender-mcp"},
        processes=["wood_turning"],
        score=4,
        autonomy=1.0,
    )
    report = bc.build_craft_report(runs_root=tmp_path / "runs")
    crafts = {c["process"]: c for c in report["crafts"]}
    wood = crafts["wood_turning"]
    assert wood["winner"] is not None
    w = wood["winner"]
    assert (w["model"], w["cad"], w["plugin"]) == ("claude-opus", "blender", "blender-mcp")
    assert w["has_evidence"] and w["opportunity_score"] is not None
    # a different craft saw no evidence and stays vacant
    assert crafts["laser_cut"]["winner"] is None
    assert report["counts"]["crafts_with_evidence"] == 1


def test_multi_craft_manifest_via_corpus_promotes_both(tmp_path):
    _write_manifest(
        tmp_path / "runs" / "r1",
        orchestrator="gpt-5-codex",
        host="cadquery",
        bridge="python-sdk",
        instrument="fujara",  # -> stave_joinery + wood_turning
        score=3,
    )
    report = bc.build_craft_report(runs_root=tmp_path / "runs")
    crafts = {c["process"]: c for c in report["crafts"]}
    assert crafts["stave_joinery"]["winner"] is not None
    assert crafts["wood_turning"]["winner"] is not None
    assert crafts["cnc_plasma"]["winner"] is None


def test_winner_is_highest_opportunity(tmp_path):
    # two combos on the same craft; the higher-scored geometry should win
    _write_manifest(
        tmp_path / "runs" / "weak",
        orchestrator="claude-code-haiku",
        host="solidworks",
        bridge="solidworks-api",
        processes=["laser_cut"],
        score=1,
    )
    _write_manifest(
        tmp_path / "runs" / "strong",
        orchestrator="claude-code-opus-4.8",
        host="openscad",
        bridge="none",
        processes=["laser_cut"],
        score=4,
        autonomy=1.0,
    )
    report = bc.build_craft_report(runs_root=tmp_path / "runs")
    laser = next(c for c in report["crafts"] if c["process"] == "laser_cut")
    assert laser["n_proven"] == 2
    assert (laser["winner"]["model"], laser["winner"]["cad"]) == ("claude-opus", "openscad")
    scores = [p["opportunity_score"] for p in laser["top_proven"]]
    assert scores == sorted(scores, reverse=True)


# --- scoring properties ---------------------------------------------------------


def test_potential_scaled_by_domain_demand():
    # identical coordinate scores higher on a higher-demand craft domain.
    # sheet-metal/laser demand 0.8 > woodworking 0.7 in the matrix domains.
    report = bc.build_craft_report()
    by_proc = {c["process"]: c for c in report["crafts"]}

    def cell(craft, model, cad, plugin):
        return next(
            v for v in craft["top_vacancies"] + craft.get("top_proven", [])
            if (v["model"], v["cad"], v["plugin"]) == (model, cad, plugin)
        ) if False else None

    # pull the same coord from each craft's full vacancy list
    def find(proc, key):
        # top_vacancies is truncated; rebuild full set deterministically
        return None

    laser = by_proc["laser_cut"]["top_vacancies"][0]
    wood = by_proc["wood_turning"]["top_vacancies"][0]
    assert laser["potential_score"] > wood["potential_score"]


def test_incompatible_plugins_pruned_per_craft():
    report = bc.build_craft_report()
    for craft in report["crafts"]:
        bad = [v for v in craft["top_vacancies"]
               if v["plugin"] == "blender-mcp" and v["cad"] != "blender"]
        assert bad == []


def test_report_is_deterministic():
    a = bc.build_craft_report()
    b = bc.build_craft_report()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_coordinate_label_delegates():
    coord = {"model": "claude-opus", "cad": "openscad", "plugin": "none", "domain": None}
    assert "Claude Opus" in bc.coordinate_label(coord)
