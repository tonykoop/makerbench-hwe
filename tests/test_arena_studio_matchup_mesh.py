"""Matchup compare (#974): the read-only GLB and dimension routes per trial."""
import io
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


def test_empty_or_unreadable_meshes_are_404_on_both_routes(tmp_path):
    run, _payload, client = _with_meshes(tmp_path)
    (run / "mesh-0.stl").write_bytes(b"")  # empty
    (run / "mesh-1.stl").write_text("not a mesh", encoding="utf-8")  # unreadable
    for trial in ("fixture-0", "fixture-1"):
        assert client.get(f"/api/runs/matchup-fixture/matchup-mesh/{trial}").status_code == 404
        assert client.get(f"/api/runs/matchup-fixture/matchup-dimensions/{trial}").status_code == 404


def _stl_with_header(path, header: bytes, ascii_name: str = ""):
    mesh = trimesh.creation.icosphere(subdivisions=2)
    if ascii_name:
        text = trimesh.exchange.stl.export_stl_ascii(mesh).replace("solid", f"solid {ascii_name}", 1)
        path.write_text(text, encoding="utf-8")
    else:
        data = trimesh.exchange.stl.export_stl(mesh)
        path.write_bytes(header.ljust(80, b" ")[:80] + data[80:])
    return path


def test_the_stl_header_never_reaches_the_glb(tmp_path):
    from makerbench.code_cad_export import stl_to_glb_bytes

    secret = "/home/someone/private/run-7/output.stl"
    binary = _stl_with_header(tmp_path / "b.stl", secret.encode())
    ascii_ = _stl_with_header(tmp_path / "a.stl", b"", ascii_name=secret)
    for path in (binary, ascii_):
        data = stl_to_glb_bytes(path)
        assert b"private" not in data and b"header" not in data
    run, payload, client = _with_meshes(tmp_path / "repo")
    _stl_with_header(run / "mesh-0.stl", secret.encode())
    served = client.get("/api/runs/matchup-fixture/matchup-mesh/fixture-0")
    assert served.status_code == 200 and b"private" not in served.content


def test_conversion_budgets(tmp_path, monkeypatch):
    from makerbench.code_cad_export import GlbBudgetExceeded, stl_to_glb_bytes

    many = trimesh.util.concatenate([
        trimesh.creation.box(extents=(1, 1, 1)).apply_translation((3 * i, 0, 0)) for i in range(12)])
    many.export(tmp_path / "many.stl")
    split = trimesh.load(io.BytesIO(stl_to_glb_bytes(tmp_path / "many.stl")), file_type="glb")
    assert len(split.geometry) == 12, "one mesh per body within the budget"
    capped = trimesh.load(io.BytesIO(stl_to_glb_bytes(tmp_path / "many.stl", max_bodies=4)), file_type="glb")
    assert len(capped.geometry) == 1, "over the component budget: drawn as one mesh"
    with pytest.raises(GlbBudgetExceeded):
        stl_to_glb_bytes(tmp_path / "many.stl", max_faces=100)
    with pytest.raises(GlbBudgetExceeded):
        stl_to_glb_bytes(tmp_path / "many.stl", max_bytes=1000)
    # The route turns an over-budget mesh into a 404, never a 500.
    from makerbench.arena_studio import service as svc

    run, _payload, client = _with_meshes(tmp_path / "repo")
    monkeypatch.setattr(svc, "MATCHUP_GLB_MAX_FACES", 5)
    svc._MATCHUP_GLB_CACHE.clear()
    assert client.get("/api/runs/matchup-fixture/matchup-mesh/fixture-0").status_code == 404
    svc._MATCHUP_GLB_CACHE.clear()


def test_the_glb_cache_holds_a_byte_budget():
    from makerbench.arena_studio.service import _ByteBudgetCache

    cache = _ByteBudgetCache(max_entries=10, max_bytes=100)
    made = []

    def make(n):
        def build():
            made.append(n)
            return b"x" * n
        return build

    cache.get_or_make(("a",), make(40))
    cache.get_or_make(("b",), make(40))
    cache.get_or_make(("a",), make(40))  # hit; "a" is now most recent
    cache.get_or_make(("c",), make(40))  # 120 bytes > 100: evict the oldest ("b")
    assert made == [40, 40, 40]
    cache.get_or_make(("a",), make(40))
    assert made == [40, 40, 40], "'a' survived"
    cache.get_or_make(("b",), make(40))
    assert made == [40, 40, 40, 40], "'b' was evicted and rebuilt"
    big = cache.get_or_make(("huge",), make(500))
    assert len(big) == 500 and ("huge",) not in cache._items, "too big to hold, served once"
    assert cache._size() <= 100
    small = _ByteBudgetCache(max_entries=2, max_bytes=10_000)
    for key in "pqr":
        small.get_or_make((key,), make(1))
    assert list(small._items) == [("q",), ("r",)]
