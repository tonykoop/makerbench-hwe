"""Measure overlay (#975): the gate metrics the Studio 3D viewer shows, the
min-wall location, the read-only workbench endpoint and the compile's GLB."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

trimesh = pytest.importorskip("trimesh")
pytest.importorskip("fastapi")

from makerbench import geometry, measure  # noqa: E402
from makerbench.arena_studio import workbench as wb  # noqa: E402

import tests.test_arena_studio_workbench as workbench_tests  # noqa: E402
from tests.test_arena_studio_workbench import (  # noqa: E402
    _assert_no_host_paths,
    _fake_launch,
    _origin_revision,
    _post,
    _snapshot,
)

# Reuse the workbench API fixtures.
fake_run = workbench_tests.fake_run
instruments_root = workbench_tests.instruments_root
registry = workbench_tests.registry
studio = workbench_tests.studio


def _hollow_box(path: Path, outer: float = 20.0, inner: float = 16.0) -> Path:
    shell = trimesh.creation.box(extents=(outer,) * 3).difference(trimesh.creation.box(extents=(inner,) * 3))
    shell.export(path)
    return path


def _metric(payload: dict, name: str) -> dict:
    return next(row for row in payload["measurements"] if row["metric"] == name)


def test_min_wall_sample_reproduces_the_estimate_and_sits_on_the_wall(tmp_path):
    mesh = trimesh.load(_hollow_box(tmp_path / "shell.stl"), force="mesh")
    sample = geometry.min_wall_sample(mesh, 4000, seed=0)
    assert sample["wall_mm"] == geometry.estimate_min_wall_mm(mesh, 4000, seed=0)
    start, hit = sample["from"], sample["to"]
    assert abs(((start[0] - hit[0]) ** 2 + (start[1] - hit[1]) ** 2 + (start[2] - hit[2]) ** 2) ** 0.5 - sample["wall_mm"]) < 1e-9
    # Both ends lie on the 2 mm shell: one coordinate at |10| (outer) and |8| (inner).
    assert any(abs(abs(v) - 10) < 2e-3 for v in start) and any(abs(abs(v) - 8) < 1e-6 for v in hit)
    assert geometry.min_wall_sample(trimesh.creation.box().slice_plane([0, 0, 0], [0, 0, 1]), 100, seed=0) is None


def test_measure_overlay_reports_the_gate_metrics_and_the_location(tmp_path):
    payload = measure.measure_overlay(_hollow_box(tmp_path / "shell.stl"))
    assert payload["ok"] is True and payload["error"] is None and payload["artifact"] == "shell.stl"
    assert _metric(payload, "bbox")["value"] == [20.0, 20.0, 20.0]
    assert _metric(payload, "bbox")["details"] == {"min": [-10.0, -10.0, -10.0], "max": [10.0, 10.0, 10.0]}
    assert _metric(payload, "volume")["value"] == pytest.approx(20**3 - 16**3)
    wall = _metric(payload, "min_wall_thickness")
    assert wall["value"] == pytest.approx(2.0, abs=0.01)
    assert set(payload["min_wall_location"]) == {"from", "to"}
    json.dumps(payload)  # JSON-safe


def test_measure_overlay_degrades_without_a_location(tmp_path):
    open_mesh = trimesh.creation.box(extents=(5, 5, 5)).slice_plane([0, 0, 0], [0, 0, 1])
    open_mesh.export(tmp_path / "open.stl")
    payload = measure.measure_overlay(tmp_path / "open.stl")
    assert payload["ok"] is False and payload["min_wall_location"] is None
    assert _metric(payload, "bbox")["ok"] is True  # bbox still shows
    (tmp_path / "junk.stl").write_text("not a mesh", encoding="utf-8")
    junk = measure.measure_overlay(tmp_path / "junk.stl")
    assert junk["ok"] is False and junk["min_wall_location"] is None
    assert not any(row["ok"] for row in junk["measurements"])


def test_compile_writes_a_glb_and_a_failed_conversion_leaves_none(tmp_path):
    out = tmp_path / "artifacts"
    out.mkdir()
    wb._write_model_glb(_hollow_box(out / "output.stl"), out)
    scene = trimesh.load(out / "model.glb")
    assert scene.bounds.tolist() == [[-10.0, -10.0, -10.0], [10.0, 10.0, 10.0]], "GLB keeps STL coordinates (mm)"
    (out / "model.glb").unlink()
    wb._write_model_glb(out / "missing.stl", out)
    assert not (out / "model.glb").exists()


def _real_stl(studio, did: str, kind: str, item: str, tmp_path: Path) -> None:
    folder = studio.workbench.store.revision_dir(did, item) if kind == "revisions" else studio.workbench.store.draft_dir(did, item)
    _hollow_box(Path(folder) / "artifacts" / "output.stl")


def test_measure_endpoint_is_read_only_and_path_free(studio, tmp_path):
    _fake_launch(studio.workbench)
    did, rev = _origin_revision(studio)
    _real_stl(studio, did, "revisions", rev, tmp_path)
    root = tmp_path / "runs" / "workbench"
    before = _snapshot(root)
    response = studio.get(f"/api/workbench/designs/{did}/revisions/{rev}/dimensions")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["units"] == "mm" and body["ok"] is True
    assert _metric(body, "bbox")["value"] == [20.0, 20.0, 20.0]
    assert body["min_wall_location"] is not None
    _assert_no_host_paths(body, tmp_path)
    assert _snapshot(root) == before, "measuring writes nothing"
    assert studio.get(f"/api/workbench/designs/{did}/revisions/{rev}/dimensions").json() == body


def test_measure_endpoint_serves_drafts_and_refuses_unknowns(studio, tmp_path):
    _fake_launch(studio.workbench)
    did, rev = _origin_revision(studio)
    r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": rev, "source": "cube(20);\n"})
    jid = r.json()["draft_id"]
    # The fake job's placeholder STL is not a solid: still a 200 with honest errors.
    broken = studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/dimensions").json()
    assert broken["ok"] is False and broken["min_wall_location"] is None
    _assert_no_host_paths(broken, tmp_path)
    _real_stl(studio, did, "drafts", jid, tmp_path)
    assert studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/dimensions").json()["ok"] is True
    assert studio.get(f"/api/workbench/designs/{did}/sources/{jid}/dimensions").status_code == 404
    assert studio.get(f"/api/workbench/designs/{did}/drafts/nope/dimensions").status_code == 404
    assert studio.get(f"/api/workbench/designs/{did}/revisions/..%2F..%2Fx/dimensions").status_code == 404
