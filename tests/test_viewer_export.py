"""Anti-cheat + pipeline tests for the interactive 3D artifact viewer (#11).

The load-bearing guarantee: the viewer's mesh-export path reads only *submitted*
artifacts and never oracle geometry. The guard tests below need no OpenSCAD; the
full compile test is skipped when the binary is absent.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from makerbench import render, viewer_export

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "mb_build_data_viewer", ROOT / "site" / "build_data.py"
)
build_data = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_data)

CUBE = "cube([10, 10, 10]);"


def _make_submission(results_dir, model="m1", task="vented_plate", seed=0,
                     track="blind", score=4):
    """Write a minimal results bundle with one submitted SCAD artifact."""
    art_dir = results_dir / model / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    scad = art_dir / f"{task}_seed{seed}_{track}.scad"
    scad.write_text(CUBE, encoding="utf-8")
    run = {
        "model_identifier": model,
        "reasoning_level": None,
        "result_provenance": "community",
        "agent_identifier": "legacy_unknown",
        "results": [{
            "task_id": task, "seed": seed, "track": track,
            "grade": {"score": score,
                      "quality": {"mass_g": 1.0, "min_wall_mm": 2.0, "bbox_mm": 10.0}},
            "dossier": {"artifacts": [{
                "role": "source", "format": "scad",
                "path": str(scad), "sha256": "abc123def456",
            }]},
        }],
    }
    (results_dir / model / f"r_{task}_{track}.json").write_text(
        json.dumps(run), encoding="utf-8"
    )
    return scad


# ---- anti-cheat guard (AC#3) — no OpenSCAD required -------------------------

def test_guard_accepts_results_submission(tmp_path):
    sub = tmp_path / "results" / "m" / "artifacts" / "vented_plate_seed0_blind.scad"
    sub.parent.mkdir(parents=True)
    sub.write_text(CUBE, encoding="utf-8")
    assert viewer_export.assert_submission_source(
        sub, submission_root=tmp_path / "results"
    )


def test_guard_rejects_oracle_named_file_even_inside_root(tmp_path):
    """A file literally named oracle.scad is refused even under results/."""
    root = tmp_path / "results"
    root.mkdir()
    oracle = root / "oracle.scad"
    oracle.write_text(CUBE, encoding="utf-8")
    with pytest.raises(viewer_export.OracleGeometryError):
        viewer_export.assert_submission_source(oracle, submission_root=root)


def test_guard_rejects_private_oracles_path(tmp_path):
    p = tmp_path / "private" / "oracles" / "vented_plate" / "oracle.scad"
    p.parent.mkdir(parents=True)
    p.write_text(CUBE, encoding="utf-8")
    with pytest.raises(viewer_export.OracleGeometryError):
        viewer_export.assert_submission_source(p, submission_root=tmp_path / "results")


def test_guard_rejects_path_outside_submission_root(tmp_path):
    outside = tmp_path / "elsewhere" / "a.scad"
    outside.parent.mkdir(parents=True)
    outside.write_text(CUBE, encoding="utf-8")
    with pytest.raises(viewer_export.OracleGeometryError):
        viewer_export.assert_submission_source(
            outside, submission_root=tmp_path / "results"
        )


def test_guard_rejects_missing_file(tmp_path):
    root = tmp_path / "results"
    root.mkdir()
    with pytest.raises(viewer_export.OracleGeometryError):
        viewer_export.assert_submission_source(root / "nope.scad", submission_root=root)


def test_export_refuses_oracle_before_compiling(tmp_path):
    """The guard fires before any read/compile — oracle is never even opened."""
    p = tmp_path / "private" / "oracles" / "x" / "oracle.scad"
    p.parent.mkdir(parents=True)
    p.write_text(CUBE, encoding="utf-8")
    with pytest.raises(viewer_export.OracleGeometryError):
        viewer_export.export_submission_mesh(str(p), tmp_path / "out.stl")


# ---- selection --------------------------------------------------------------

def test_select_one_representative_per_family_highest_score(tmp_path):
    results = tmp_path / "results"
    _make_submission(results, model="lo", score=1)
    _make_submission(results, model="hi", score=4)
    reps = viewer_export.select_representatives(
        viewer_export.iter_submissions(results)
    )
    assert len(reps) == 1
    assert reps[0]["model_identifier"] == "hi"


def test_iter_submissions_skips_rows_without_source(tmp_path):
    results = tmp_path / "results" / "m"
    results.mkdir(parents=True)
    run = {"model_identifier": "m", "results": [
        {"task_id": "vented_plate", "seed": 0, "track": "blind",
         "grade": {"score": 4}, "dossier": {"artifacts": []}}
    ]}
    (results / "r.json").write_text(json.dumps(run), encoding="utf-8")
    assert list(viewer_export.iter_submissions(tmp_path / "results")) == []


# ---- full pipeline (needs OpenSCAD) ----------------------------------------

@pytest.mark.skipif(
    not render.openscad_available(), reason="needs the openscad binary"
)
def test_build_writes_stl_and_manifest_from_submission(tmp_path):
    results = tmp_path / "results"
    _make_submission(results)
    site = tmp_path / "site"
    manifest = viewer_export.build(results, site)

    assert len(manifest["meshes"]) == 1
    entry = manifest["meshes"][0]
    assert entry["task_id"] == "vented_plate"
    assert entry["mesh"] == "assets/meshes/vented_plate.stl"
    assert entry["source_sha256"] == "abc123def456"
    assert entry["quality"]["mass_g"] == 1.0

    stl = site / entry["mesh"]
    assert stl.exists() and stl.stat().st_size > 0
    ondisk = json.loads((site / "data" / "meshes.json").read_text(encoding="utf-8"))
    assert ondisk["meshes"] == manifest["meshes"]
    # The export never wrote anything outside the site tree.
    assert "oracle" not in stl.read_bytes()[:200].lower().decode("latin-1")


# ---- build_data embedding ---------------------------------------------------

def _viewer_payload():
    task = {"id": "vented_plate", "title": "Vented Plate"}
    payload = {"tracks": ["blind"], "task_families": [task], "models": []}
    manifest = {"by_task": {"vented_plate": {
        "task_id": "vented_plate", "mesh": "assets/meshes/vented_plate.stl",
        "quality": {"mass_g": 1.0, "min_wall_mm": 2.0, "bbox_mm": 10.0},
        "face_count": 12, "model_identifier": "m", "track": "blind",
        "seed": 0, "score": 4, "source_sha256": "deadbeefcafe",
    }}, "by_row": {}}
    return task, payload, manifest


def test_task_detail_embeds_viewer_with_manifest():
    task, payload, manifest = _viewer_payload()
    html = build_data.task_detail_html(task, payload, "https://x", manifest)
    assert 'class="mb-viewer"' in html
    assert 'data-mesh="../../assets/meshes/vented_plate.stl"' in html
    assert "importmap" in html
    assert "assets/viewer.js" in html
    assert "deadbeefcafe"[:12] in html


def test_task_detail_byte_stable_without_manifest():
    task, payload, _ = _viewer_payload()
    with_none = build_data.task_detail_html(task, payload, "https://x", None)
    default = build_data.task_detail_html(task, payload, "https://x")
    assert with_none == default
    assert "mb-viewer" not in with_none
    assert "importmap" not in with_none
