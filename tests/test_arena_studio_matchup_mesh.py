"""Matchup compare (#974): the read-only GLB and dimension routes per trial."""
import json

import pytest
from fastapi.testclient import TestClient

from makerbench.arena_studio import create_studio_app

from tests.test_arena_studio_matchup_view import make_matchup_repo

trimesh = pytest.importorskip("trimesh")


def _with_meshes(tmp_path):
    run = make_matchup_repo(tmp_path)
    payload = json.loads((run / "run_log.json").read_text())
    for n, trial in enumerate(payload["trials"]):
        stl = run / f"mesh-{n}.stl"
        trimesh.creation.box(extents=(10 + n * 10, 10, 10)).export(stl)
        trial["result"]["artifacts"]["stl_path"] = str(stl)
    (run / "run_log.json").write_text(json.dumps(payload))
    client = TestClient(create_studio_app(registry_path=tmp_path / "registry.json", repo_root=tmp_path),
                        base_url="http://127.0.0.1")
    return run, payload, client


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_summary_links_each_trial_mesh_and_the_glb_is_converted_in_memory(tmp_path):
    run, _payload, client = _with_meshes(tmp_path)
    before = _snapshot(tmp_path)
    a, b = client.get("/api/runs/matchup-fixture/summary").json()["matchup_trials"]
    assert a["mesh_url"] == "/api/runs/matchup-fixture/matchup-mesh/fixture-0"
    assert a["dimensions_url"] == "/api/runs/matchup-fixture/matchup-dimensions/fixture-0"
    response = client.get(a["mesh_url"])
    assert response.status_code == 200 and response.content[:4] == b"glTF"
    assert response.headers["content-type"] == "model/gltf-binary"
    assert response.headers["x-content-type-options"] == "nosniff"
    import io

    scene = trimesh.load(io.BytesIO(client.get(b["mesh_url"]).content), file_type="glb")
    assert scene.extents.tolist() == pytest.approx([20, 10, 10]), "GLB keeps the STL frame (mm)"
    dims = client.get(b["dimensions_url"]).json()
    assert dims["units"] == "mm" and dims["ok"] is True
    bbox = next(row for row in dims["measurements"] if row["metric"] == "bbox")
    assert bbox["value"] == [20.0, 10.0, 10.0]
    assert str(tmp_path) not in json.dumps(dims)
    assert _snapshot(tmp_path) == before, "never writes (no .glb beside the STL)"


def test_mesh_routes_refuse_escapes_missing_meshes_and_non_matchup_runs(tmp_path):
    run, payload, client = _with_meshes(tmp_path)
    outside = tmp_path / "outside.stl"
    trimesh.creation.box().export(outside)
    payload["trials"][0]["result"]["artifacts"]["stl_path"] = str(outside)
    (run / "run_log.json").write_text(json.dumps(payload))
    for route in ("matchup-mesh", "matchup-dimensions"):
        assert client.get(f"/api/runs/matchup-fixture/{route}/fixture-0").status_code == 404
        assert client.get(f"/api/runs/matchup-fixture/{route}/missing").status_code == 404
    # A symlink inside the run that points out of it is refused too.
    payload["trials"][0]["result"]["artifacts"]["stl_path"] = str(run / "link.stl")
    (run / "link.stl").symlink_to(outside)
    (run / "run_log.json").write_text(json.dumps(payload))
    assert client.get("/api/runs/matchup-fixture/matchup-mesh/fixture-0").status_code == 404
    # A recorded path that is not an STL, or no mesh at all.
    payload["trials"][0]["result"]["artifacts"]["stl_path"] = str(run / "preview-0.png")
    del payload["trials"][1]["result"]["artifacts"]["stl_path"]
    (run / "run_log.json").write_text(json.dumps(payload))
    assert client.get("/api/runs/matchup-fixture/matchup-mesh/fixture-0").status_code == 404
    assert client.get("/api/runs/matchup-fixture/matchup-mesh/fixture-1").status_code == 404
    rows = client.get("/api/runs/matchup-fixture/summary").json()["matchup_trials"]
    assert [row["mesh_url"] for row in rows] == [None, None]
    assert [row["dimensions_url"] for row in rows] == [None, None]
    # Not a matchup run: no mesh route at all.
    payload["trials"][1]["result"]["artifacts"]["stl_path"] = str(run / "mesh-1.stl")
    del payload["config"]["matchup"]
    (run / "run_log.json").write_text(json.dumps(payload))
    assert client.get("/api/runs/matchup-fixture/matchup-mesh/fixture-1").status_code == 404
    assert client.get("/api/runs/matchup-fixture/matchup-dimensions/fixture-1").status_code == 404


def test_an_unreadable_mesh_is_a_404_not_a_500(tmp_path):
    run, _payload, client = _with_meshes(tmp_path)
    (run / "mesh-0.stl").write_bytes(b"\x00" * 10)
    assert client.get("/api/runs/matchup-fixture/matchup-mesh/fixture-0").status_code == 404


def test_the_demo_app_serves_no_meshes():
    from makerbench.arena_studio.demo import create_demo_app

    client = TestClient(create_demo_app(), base_url="http://127.0.0.1")
    assert client.get("/api/runs/x/matchup-mesh/y").status_code in (403, 404)
    assert client.get("/api/runs/x/matchup-dimensions/y").status_code in (403, 404)
