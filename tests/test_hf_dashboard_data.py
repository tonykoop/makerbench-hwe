"""HF Space dual-league dashboard data layer (#98).

Loads spaces/hf_dashboard/dashboard_data.py via importlib (repo convention) and
exercises it against the run-nav fixtures:
  - run_alpha_vented_plate: harness_class "agentic-cad" + full partner stubs
    (packet, WorkflowManifest/HII, cert, 3D, video) -> Workflow league.
  - run_beta_bracket: bare run.json, no harness_class -> Autonomous league.

The fixtures are also fed through the real run-library generator so the test
proves the dashboard consumes the *actual* runs-manifest contract (#104), not a
hand-rolled stand-in.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "run_nav"
ALPHA = FIXTURES / "run_alpha_vented_plate"
BETA = FIXTURES / "run_beta_bracket"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


dd = _load(ROOT / "spaces" / "hf_dashboard" / "dashboard_data.py", "dashboard_data")
library = _load(ROOT / "scripts" / "generate_run_library.py", "generate_run_library")


@pytest.fixture(scope="module")
def manifest() -> dict:
    """The real runs-manifest the dashboard consumes, built from the fixtures."""
    views = library.scan_runs(FIXTURES)
    return library.build_manifest(views, FIXTURES)


# --- league classification -------------------------------------------------

@pytest.mark.parametrize("harness_class,expected", [
    ("agentic-cad", "workflow"),
    ("assisted-workflow", "workflow"),
    ("gui-injected-copilot", "workflow"),
    ("autonomous", "autonomous"),
    ("unclassified", "autonomous"),
    ("", "autonomous"),
    (None, "autonomous"),
    ("AUTONOMOUS", "autonomous"),  # case-insensitive
])
def test_league_split_rule(harness_class, expected):
    assert dd.league_of(harness_class) == expected


# --- Tab A: dual league ----------------------------------------------------

def test_dual_league_separates_leagues(manifest):
    out = dd.build_dual_league(manifest)
    assert out["schema"] == dd.DUAL_LEAGUE_SCHEMA
    assert out["source_schema"] == "makerbench-runs-manifest-v1"
    assert out["total_runs"] == 2
    auto = out["leagues"]["autonomous"]
    flow = out["leagues"]["workflow"]
    # beta (no harness_class) is autonomous; alpha (agentic-cad) is a workflow.
    assert auto["n_runs"] == 1 and flow["n_runs"] == 1
    assert {m for r in auto["rows"] for m in r["members"]} == {"run_beta_bracket"}
    assert {m for r in flow["rows"] for m in r["members"]} == {"run_alpha_vented_plate"}


def test_workflow_row_headlines_the_stack(manifest):
    out = dd.build_dual_league(manifest)
    row = out["leagues"]["workflow"]["rows"][0]
    # The stack identity (model · agent · harness_class) headlines the row, not
    # the bare model — that is the whole point of the Workflows league.
    assert row["headline"] != "claude-code-opus-4.8"
    assert "claude-code-opus-4.8" in row["headline"]
    assert "agentic-cad" in row["headline"]
    assert row["hii_best"] == "L1"
    assert row["harness_classes"] == ["agentic-cad"]


def test_autonomous_row_headlines_the_model(manifest):
    out = dd.build_dual_league(manifest)
    row = out["leagues"]["autonomous"]["rows"][0]
    assert "codex-gpt-5.5" in row["headline"]


def test_rows_ranked_by_mean_score_desc():
    manifest = {"schema": "makerbench-runs-manifest-v1", "runs": [
        {"run_id": "a", "harness_class": "autonomous", "model_identifier": "m1", "score": 0.4},
        {"run_id": "b", "harness_class": "autonomous", "model_identifier": "m2", "score": 0.9},
        {"run_id": "c", "harness_class": "autonomous", "model_identifier": "m3", "score": None},
    ]}
    # These fixtures carry no domain; floor off to test the ordering rule on its own.
    rows = dd.build_dual_league(manifest, min_domains=0)["leagues"]["autonomous"]["rows"]
    assert [r["rank"] for r in rows] == [1, 2, 3]
    assert rows[0]["headline"].startswith("m2")  # 0.9
    assert rows[1]["headline"].startswith("m1")  # 0.4
    assert rows[2]["mean_score"] is None         # unscored trails


def test_runs_with_same_stack_aggregate_into_one_row():
    manifest = {"schema": "x", "runs": [
        {"run_id": "r1", "harness_class": "agentic-cad", "stack": "Claude · blender",
         "model_identifier": "claude", "score": 0.8, "task_id": "t1", "verification": "verified"},
        {"run_id": "r2", "harness_class": "agentic-cad", "stack": "Claude · blender",
         "model_identifier": "claude", "score": 0.6, "task_id": "t2", "verification": "pending"},
    ]}
    rows = dd.build_dual_league(manifest)["leagues"]["workflow"]["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["n_runs"] == 2
    assert row["mean_score"] == 0.7
    assert row["best_score"] == 0.8
    assert sorted(row["tasks"]) == ["t1", "t2"]
    assert row["verification"] == {"verified": 1, "unverified": 0, "pending": 1}
    assert row["members"] == ["r1", "r2"]


# --- Tab B: inspect index --------------------------------------------------

def test_inspect_index_one_card_per_run(manifest):
    out = dd.build_inspect_index(manifest)
    assert out["schema"] == dd.INSPECT_INDEX_SCHEMA
    assert out["count"] == 2
    by_id = {c["run_id"]: c for c in out["runs"]}
    assert by_id["run_alpha_vented_plate"]["league"] == "workflow"
    assert by_id["run_alpha_vented_plate"]["has_artifact_3d"] is True
    assert by_id["run_beta_bracket"]["league"] == "autonomous"


# --- Tab B: run detail -----------------------------------------------------

def test_run_detail_alpha_full(manifest):
    detail = dd.build_run_detail(ALPHA)
    assert detail["schema"] == dd.RUN_DETAIL_SCHEMA
    assert detail["league"] == "workflow"
    # grader verdict: 4 levels, L4 failed on the fixture.
    verdict = detail["verdict"][0]
    assert verdict["task_id"] == "vented_plate"
    assert verdict["n_levels"] == 4
    assert verdict["n_pass"] == 3
    assert verdict["levels"][3]["passed"] is False
    # workflow trace: HII + tool-call log from the WorkflowManifest stub.
    wf = detail["workflow"]
    assert wf["has_manifest"] is True
    assert wf["hii"] == "L1"
    assert wf["n_steps"] == 4
    assert wf["tool_call_log"][0]["tool"] == "blender-mcp.new_scene"
    # 3D artifact + certificate + packet pointers.
    assert detail["artifact_3d"]["kind"] == "glb"
    assert detail["certificate"]["has_certificate"] is True
    assert detail["certificate"]["verification"] == "verified"
    assert detail["packet"]["has_packet"] is True


def test_run_detail_beta_degrades(manifest):
    detail = dd.build_run_detail(BETA)
    assert detail["league"] == "autonomous"
    assert detail["workflow"]["has_manifest"] is False
    assert detail["workflow"]["tool_call_log"] == []
    assert detail["certificate"]["has_certificate"] is False
    assert detail["certificate"]["verification"] == "pending"
    assert detail["artifact_3d"] is None


def test_wall_clock_read_from_real_manifest_metrics():
    # The fixture stub has no metrics; a real WorkflowManifest carries
    # metrics.wall_clock_seconds. Prove we read it defensively from both shapes.
    assert dd._wall_clock_seconds({"metrics": {"wall_clock_seconds": 42.0}}) == 42.0
    assert dd._wall_clock_seconds({"wall_clock_seconds": 7}) == 7.0
    assert dd._wall_clock_seconds({}) is None


# --- golden-seed safety ----------------------------------------------------

_FORBIDDEN_SUBSTRINGS = ("oracle", "private/oracles", "gold", "secret", "held_out", "held-out")


def test_no_golden_seed_leak_in_payloads(manifest):
    """Every emitted payload must be free of oracle/seed-gold markers (acceptance)."""
    payloads = [
        dd.build_dual_league(manifest),
        dd.build_inspect_index(manifest),
        dd.build_run_detail(ALPHA),
        dd.build_run_detail(BETA),
    ]
    for payload in payloads:
        blob = json.dumps(payload).lower()
        for needle in _FORBIDDEN_SUBSTRINGS:
            assert needle not in blob, f"{needle!r} leaked into a dashboard payload"


def test_run_detail_keys_are_whitelisted():
    """build_run_detail must emit only a fixed key set — no passthrough of run.json."""
    detail = dd.build_run_detail(ALPHA)
    allowed = {
        "schema", "run_id", "league", "model_identifier", "agent_identifier",
        "reasoning_level", "benchmark_profile", "harness_class", "harness_subclass",
        "domain", "stack", "score", "quality", "cost_usd", "iterations", "verdict",
        "workflow", "packet", "certificate", "artifact_3d", "video",
    }
    assert set(detail) == allowed


# --- coverage floor (#877) --------------------------------------------------

def _run(model, domain, score, run_id):
    return {"run_id": run_id, "model_identifier": model, "domain": domain, "score": score,
            "harness_class": "autonomous", "verification": "verified"}


def test_thin_coverage_model_cannot_top_the_board():
    runs = [_run("one-family-wonder", "bracket", 4.0, "w1"), _run("one-family-wonder", "bracket", 4.0, "w2")]
    for i, dom in enumerate(("bracket", "enclosure", "hinge")):
        runs.append(_run("broad", dom, 2.0, f"b{i}"))
    rows = dd.build_dual_league({"runs": runs})["leagues"]["autonomous"]["rows"]
    assert [r["headline"] for r in rows] == ["broad", "one-family-wonder"]
    broad, thin = rows
    assert broad["rank"] == 1 and broad["rank_eligible"] is True
    assert thin["rank"] is None and thin["rank_eligible"] is False
    assert "unranked" in thin["coverage_note"] and thin["n_domains"] == 1
    assert thin["mean_score"] > broad["mean_score"]  # would have led without the floor


def test_floor_is_configurable_and_applies_to_both_leagues():
    runs = [_run("m", "bracket", 3.0, "a"),
            {**_run("stack", "bracket", 3.0, "w"), "harness_class": "agentic-cad", "stack": "Claude + Blender MCP"}]
    leagues = dd.build_dual_league({"runs": runs}, min_domains=1)["leagues"]
    assert leagues["autonomous"]["rows"][0]["rank"] == 1
    assert leagues["workflow"]["rows"][0]["rank"] == 1
    leagues = dd.build_dual_league({"runs": runs})["leagues"]
    assert leagues["autonomous"]["rows"][0]["rank"] is None
    assert leagues["workflow"]["rows"][0]["rank"] is None


def test_default_floor_constant():
    assert dd.MIN_DOMAINS_FOR_RANK == 3


def test_unscored_runs_do_not_count_toward_coverage():
    # #890 review: scores [4, None, None] over three domains is one scored family.
    runs = [_run("sparse", "bracket", 4.0, "s0"), _run("sparse", "enclosure", None, "s1"),
            _run("sparse", "hinge", None, "s2")]
    for i, dom in enumerate(("bracket", "enclosure", "hinge")):
        runs.append(_run("steady", dom, 2.0, f"t{i}"))
    rows = dd.build_dual_league({"runs": runs})["leagues"]["autonomous"]["rows"]
    assert [r["headline"] for r in rows] == ["steady", "sparse"]
    sparse = rows[1]
    assert sparse["rank"] is None and sparse["n_domains"] == 1 and sparse["rank_eligible"] is False
    assert sparse["domains"] == ["bracket", "enclosure", "hinge"]  # still listed, just not evidence


def test_all_unscored_row_is_never_ranked_even_alone():
    runs = [_run("ghost", dom, None, f"g{i}") for i, dom in enumerate(("a", "b", "c"))]
    row = dd.build_dual_league({"runs": runs})["leagues"]["autonomous"]["rows"][0]
    assert row["rank"] is None and row["n_domains"] == 0


def test_recorded_zero_scores_count_as_scored_evidence():
    runs = [_run("zeros", dom, 0.0, f"z{i}") for i, dom in enumerate(("a", "b", "c"))]
    row = dd.build_dual_league({"runs": runs})["leagues"]["autonomous"]["rows"][0]
    assert row["rank"] == 1 and row["n_domains"] == 3 and row["mean_score"] == 0.0


def test_workflow_league_uses_scored_coverage_too():
    def wf(dom, score, i):
        return {**_run("x", dom, score, f"w{i}"), "harness_class": "agentic-cad", "stack": "Claude + Blender MCP"}
    runs = [wf("a", 4.0, 0), wf("b", None, 1), wf("c", None, 2)]
    row = dd.build_dual_league({"runs": runs})["leagues"]["workflow"]["rows"][0]
    assert row["rank"] is None and row["n_domains"] == 1
