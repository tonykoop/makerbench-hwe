"""HF Docker Space app (#98) — render/build contract tests.

Loads spaces/hf_dashboard/app.py via importlib (repo convention) and proves it:
  - builds Tab A (dual-league) from the real run-nav fixture manifest, keeping the
    two leagues strictly separate and headlining workflow rows with the stack;
  - renders Tab B run-detail emitting ONLY whitelisted, golden-seed-safe content
    (no oracle/seed/gold leakage);
  - degrades gracefully for the 3D preview when geometry libs OR the artifact are
    absent — never crashing.

These tests are stdlib/pytest only. app.py defers all heavy imports (gradio,
trimesh, OCC) into build_demo()/build_geometry_preview(), so importing the module
and calling its render helpers needs none of them installed.
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


app = _load(ROOT / "spaces" / "hf_dashboard" / "app.py", "hf_dashboard_app")
library = _load(ROOT / "scripts" / "generate_run_library.py", "generate_run_library")


@pytest.fixture(scope="module")
def manifest() -> dict:
    """The real runs-manifest the app consumes, built from the run-nav fixtures."""
    views = library.scan_runs(FIXTURES)
    return library.build_manifest(views, FIXTURES)


# --- Tab A: dual-league render ---------------------------------------------

def test_render_dual_league_two_separate_tables(manifest):
    out = app.render_dual_league(manifest)
    assert out["schema"] == app.dd.DUAL_LEAGUE_SCHEMA
    assert out["total_runs"] == 2
    # Two distinct league bundles; beta autonomous, alpha workflow.
    assert out["autonomous"]["n_runs"] == 1
    assert out["workflow"]["n_runs"] == 1
    assert out["autonomous"]["headers"] == app.LEAGUE_HEADERS
    assert out["workflow"]["headers"] == app.LEAGUE_HEADERS


def test_workflow_table_headlines_the_stack(manifest):
    out = app.render_dual_league(manifest)
    row = out["workflow"]["rows"][0]
    # row = [rank, headline, mean, best, runs, verified, hii, domains]
    headline = row[1]
    assert headline != "claude-code-opus-4.8"
    assert "claude-code-opus-4.8" in headline
    # Workflow league surfaces the artifact-verified cap note.
    assert "artifact-verified" in out["workflow"]["cap_note"]
    # Verified column is expressed as an explicit "N/M verified" cap.
    assert "verified" in str(row[5])


def test_autonomous_table_headlines_the_model(manifest):
    out = app.render_dual_league(manifest)
    row = out["autonomous"]["rows"][0]
    assert "codex-gpt-5.5" in row[1]


def test_empty_manifest_renders_empty_leagues():
    out = app.render_dual_league({"schema": "makerbench-runs-manifest-v1", "runs": []})
    assert out["autonomous"]["rows"] == []
    assert out["workflow"]["rows"] == []


# --- Tab B: inspect index / picker -----------------------------------------

def test_list_runs_one_choice_per_run(manifest):
    choices = app.list_runs(manifest)
    ids = {rid for _label, rid in choices}
    assert ids == {"run_alpha_vented_plate", "run_beta_bracket"}
    labels = " ".join(label for label, _ in choices)
    assert "workflow" in labels and "autonomous" in labels


# --- Tab B: run detail render (whitelist / no leakage) ---------------------

_FORBIDDEN_SUBSTRINGS = ("oracle", "private/oracles", "gold", "secret", "held_out", "held-out")


def test_render_run_detail_emits_only_whitelisted_content(manifest):
    view = app.render_run_detail(ALPHA)
    # The render bundle is derived solely from the whitelisted build_run_detail
    # payload — assert no oracle/seed/gold markers leak into anything rendered.
    blob = json.dumps(
        {k: v for k, v in view.items() if k != "model3d_path"}
    ).lower()
    for needle in _FORBIDDEN_SUBSTRINGS:
        assert needle not in blob, f"{needle!r} leaked into a rendered view"
    # Verdict + trace panels carry the expected public facts.
    assert "vented_plate" in view["verdict_md"]
    assert "L1" in view["trace_md"]  # HII headline
    assert "blender-mcp.new_scene" in view["trace_md"]  # tool-call log
    assert view["league"] == "workflow"


def test_render_run_detail_underlying_payload_is_whitelisted():
    """The app must not bypass the data layer's fixed key set."""
    detail = app.dd.build_run_detail(ALPHA)
    allowed = {
        "schema", "run_id", "league", "model_identifier", "agent_identifier",
        "reasoning_level", "benchmark_profile", "harness_class", "harness_subclass",
        "domain", "stack", "score", "quality", "cost_usd", "iterations", "verdict",
        "workflow", "packet", "certificate", "artifact_3d", "video",
    }
    assert set(detail) == allowed


# --- Tab B: 3D preview graceful degradation --------------------------------

def test_geometry_preview_no_artifact_degrades(manifest):
    """Beta has no 3D artifact -> (None, clear message), never a crash."""
    detail = app.dd.build_run_detail(BETA)
    model_path, status = app.build_geometry_preview(BETA, detail)
    assert model_path is None
    assert "No 3D artifact" in status


def test_geometry_preview_missing_geom_libs_degrades(monkeypatch):
    """When geometry libs are absent, a STEP/B-Rep artifact degrades with a message.

    Simulate trimesh being unavailable by blocking its import, then point at a
    synthetic STEP artifact pointer.
    """
    import builtins

    real_import = builtins.__import__

    def _block(name, *args, **kwargs):
        if name == "trimesh" or name.startswith("trimesh."):
            raise ImportError("trimesh blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block)

    detail = {
        "artifact_3d": {"file": "packet/model.step", "kind": "step"},
        "packet": {"files": ["packet/model.step"]},
    }
    model_path, status = app.build_geometry_preview(ALPHA, detail)
    # ALPHA has packet/model.step on disk; without trimesh a STEP solid cannot be
    # tessellated -> graceful (None, message), not an exception.
    assert model_path is None
    assert "geometry" in status.lower() or "step" in status.lower()


def test_geometry_preview_direct_mesh_without_trimesh(monkeypatch):
    """A GLB/STL artifact can be shown directly even without trimesh installed."""
    import builtins

    real_import = builtins.__import__

    def _block(name, *args, **kwargs):
        if name == "trimesh" or name.startswith("trimesh."):
            raise ImportError("trimesh blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block)

    # ALPHA ships model.glb in the run-dir root.
    detail = app.dd.build_run_detail(ALPHA)
    model_path, status = app.build_geometry_preview(ALPHA, detail)
    assert model_path is not None
    assert model_path.endswith(".glb")
    assert "directly" in status
