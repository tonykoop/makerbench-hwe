"""Blindness, source preservation and local-only vote persistence through real HTTP."""
import importlib.util
import json
from pathlib import Path
import urllib.error
import urllib.request

from PIL import Image, PngImagePlugin
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("frontier_vote", ROOT / "scripts/frontier_vote.py")
vote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vote)


def fixture_runs(tmp_path):
    source = tmp_path / "source"
    for n in range(1, 11):
        for model in ("SECRET_MODEL_A", "SECRET_MODEL_B"):
            folder = source / f"round{n}" / model
            folder.mkdir(parents=True)
            png = folder / "preview.png"
            meta = PngImagePlugin.PngInfo()
            meta.add_text("model", model)
            Image.new("RGB", (10, 10), "red").save(png, pnginfo=meta)
            trial = {"instrument_id": "synthetic", "seed": 0, "rep": 0,
                     "trial_id": "synthetic-" + model, "model_id": model, "status": "scored",
                     "result": {"status": "scored", "render_ok": True, "artifacts": {"png_path": str(png)}}}
            (folder / "run_log.json").write_text(json.dumps({
                "schema": "makerbench-code-cad-orchestration-v1", "trials": [trial]}))
    return source


def test_real_http_blindness_vote_resume_and_source_preservation(tmp_path):
    source = fixture_runs(tmp_path)
    before = {p: vote.digest(p) for p in source.rglob("*") if p.is_file()}
    out = tmp_path / "runs/votes"
    queue = vote.prepare(source, out, voter="synthetic-voter", workspace=tmp_path)
    assert len(queue.items) == 10
    assert {i.meta["round"] for i in queue.items} == set(range(1, 11))
    server = vote.serve(queue)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        html = urllib.request.urlopen(base + "/queue").read().decode()
        assert "SECRET_MODEL" not in html
        pair = queue.items[0].pair
        for candidate in (pair.left, pair.right):
            assert "SECRET_MODEL" not in candidate.candidate_id + candidate.trial_id + candidate.render_path
            assert urllib.request.urlopen(base + "/" + candidate.render_path).status == 200
            with Image.open(out / candidate.render_path) as image:
                assert not image.info
        for path in ("/package.local.json", "/votes.revealed.jsonl", "/", "/blind/"):
            if path == "/":
                continue
            for method in ("GET", "HEAD"):
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(base + path, method=method))
                assert error.value.code == 404
        request = urllib.request.Request(base + "/vote", data=json.dumps({
            "pair_id": pair.pair_id, "winner": "left", "flags": {"left": ["wrong_proportions"]}
        }).encode(), headers={"Content-Type": "application/json", "Origin": base})
        assert json.load(urllib.request.urlopen(request)) == {"ok": True}
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 409
    finally:
        server.shutdown()
        server.server_close()
    revealed = json.loads((out / "votes.revealed.jsonl").read_text())
    assert revealed["round"] == 1 and revealed["preview_only"] is True
    assert revealed["reveal"]["left"]["model_id"].startswith("SECRET_MODEL")
    resumed = vote.prepare(source, out, voter="synthetic-voter", workspace=tmp_path)
    assert resumed.progress() == (1, 10)
    assert before == {p: vote.digest(p) for p in source.rglob("*") if p.is_file()}
    assert not (tmp_path / "site").exists()
    log = next(source.rglob("run_log.json"))
    log.write_text(log.read_text() + "\n")
    with pytest.raises(ValueError, match="differs"):
        vote.prepare(source, out, voter="synthetic-voter", workspace=tmp_path)


def test_failed_candidate_with_stale_png_is_excluded(tmp_path):
    source = fixture_runs(tmp_path)
    log = source / "round1/SECRET_MODEL_A/run_log.json"
    data = json.loads(log.read_text())
    data["trials"][0]["status"] = "auto_fail"
    log.write_text(json.dumps(data))
    out = tmp_path / "runs/votes"
    queue = vote.prepare(source, out, workspace=tmp_path)
    assert len(queue.items) == 9
    manifest = json.loads((out / "package.local.json").read_text())
    assert manifest["excluded"] == {"auto_fail": 1}
    assert manifest["candidates"] == 19 and manifest["paired_candidates"] == 18


def test_output_and_preview_containment_fail_before_staging(tmp_path):
    source = fixture_runs(tmp_path)
    with pytest.raises(ValueError, match="ignored runs"):
        vote.prepare(source, tmp_path / "site/votes", workspace=tmp_path)
    assert not (tmp_path / "site").exists()
    outside = tmp_path / "private.png"
    Image.new("RGB", (1, 1)).save(outside)
    log = next(source.rglob("run_log.json"))
    data = json.loads(log.read_text())
    data["trials"][0]["result"]["artifacts"]["png_path"] = str(outside)
    log.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="contained"):
        vote.prepare(source, tmp_path / "runs/votes", workspace=tmp_path)
    assert not (tmp_path / "runs").exists()


def test_query_page_routes_never_dispatch_to_private_files(tmp_path):
    source = fixture_runs(tmp_path)
    out = tmp_path / "runs/votes"
    queue = vote.prepare(source, out, workspace=tmp_path)
    (out / "queue").write_text("PRIVATE_SENTINEL")
    server = vote.serve(queue)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for path in ("/queue?cache=1", "/?cache=1"):
            response = urllib.request.urlopen(base + path)
            html = response.read().decode()
            assert "PRIVATE_SENTINEL" not in html and "package.local.json" not in html
            assert 'data-pair-id="' in html and response.headers.get_content_type() == "text/html"
            head = urllib.request.urlopen(urllib.request.Request(base + path, method="HEAD"))
            assert head.status == 200 and head.read() == b""
            assert head.headers.get_content_type() == "text/html"
        alias = queue.items[0].pair.left.render_path
        assert urllib.request.urlopen(base + "/" + alias + "?cache=1").status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_foreign_host_origin_and_non_json_leave_votes_unchanged(tmp_path):
    source = fixture_runs(tmp_path)
    out = tmp_path / "runs/votes"
    queue = vote.prepare(source, out, workspace=tmp_path)
    server = vote.serve(queue)
    base = f"http://127.0.0.1:{server.server_port}"
    payload = json.dumps({"pair_id": queue.items[0].pair.pair_id, "winner": "left"}).encode()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + "/queue", headers={"Host": "untrusted.example"}))
        assert error.value.code == 403
        for headers in (
            {"Host": "untrusted.example", "Origin": "https://untrusted.example", "Content-Type": "text/plain"},
            {"Origin": "https://untrusted.example", "Content-Type": "application/json"},
            {"Content-Type": "application/json"},
            {"Origin": base, "Content-Type": "text/plain"},
        ):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base + "/vote", data=payload, headers=headers))
            assert error.value.code in {403, 415}
            assert queue.progress() == (0, 10)
            assert not (out / "votes.blind.jsonl").exists()
            assert not (out / "votes.revealed.jsonl").exists()
        response = urllib.request.urlopen(urllib.request.Request(
            base + "/vote", data=payload, headers={"Origin": base, "Content-Type": "application/json"}))
        assert json.load(response) == {"ok": True}
        assert queue.progress() == (1, 10)
    finally:
        server.shutdown()
        server.server_close()


def extra_root(tmp_path, name, model, layout):
    """One entrant in the other run layouts: ``round<N>/run_log.json`` or ``r<N>-<model>/``."""
    root = tmp_path / name / "runs" / name
    for n in range(1, 11):
        folder = root / (f"round{n}" if layout == "flat" else f"r{n}-{model}")
        folder.mkdir(parents=True)
        Image.new("RGB", (10, 10), "blue").save(folder / "preview.png")
        rel = folder.relative_to(tmp_path / name).as_posix() + "/preview.png"  # checkout-relative
        trial = {"instrument_id": "synthetic", "seed": 0, "rep": 0, "trial_id": "t-" + model,
                 "model_id": model, "status": "scored",
                 "result": {"status": "scored", "render_ok": True, "artifacts": {"png_path": rel}}}
        (folder / "run_log.json").write_text(json.dumps({
            "schema": "makerbench-code-cad-orchestration-v1", "trials": [trial]}))
    return root


def test_multiple_source_roots_pair_across_roots_within_a_cell(tmp_path):
    source = fixture_runs(tmp_path)  # A, B per round
    flat = extra_root(tmp_path, "gem", "SECRET_MODEL_C", "flat")
    prefixed = extra_root(tmp_path, "or", "SECRET_MODEL_D", "prefixed")
    out = tmp_path / "runs/votes"
    queue = vote.prepare([source, flat, prefixed], out, workspace=tmp_path)
    assert len(queue.items) == 60  # four entrants -> six pairs per round
    manifest = json.loads((out / "package.local.json").read_text())
    assert manifest["candidates"] == 40 and manifest["paired_candidates"] == 40
    assert any(k.startswith("root2/") for k in manifest["sources"])
    models = {c.model_id for i in queue.items for c in (i.pair.left, i.pair.right)}
    assert models == {"SECRET_MODEL_" + x for x in "ABCD"}
    assert vote.prepare([source, flat, prefixed], out, workspace=tmp_path).progress() == (0, 60)
    with pytest.raises(ValueError, match="differs"):
        vote.prepare([source, flat], out, workspace=tmp_path)
    with pytest.raises(ValueError, match="Duplicate source root"):
        vote.prepare([source, source], tmp_path / "runs/other", workspace=tmp_path)


def test_same_model_in_two_roots_is_rejected_and_one_root_path_still_works(tmp_path):
    source = fixture_runs(tmp_path)
    clash = extra_root(tmp_path, "dup", "SECRET_MODEL_A", "flat")
    with pytest.raises(ValueError, match="Duplicate entrant"):
        vote.prepare([source, clash], tmp_path / "runs/votes", workspace=tmp_path)
    assert len(vote.prepare(source, tmp_path / "runs/ok", workspace=tmp_path).items) == 10


def test_baseline_root_entrants_are_opponents_only(tmp_path):
    source = fixture_runs(tmp_path)  # A, B: would pair together, already voted in an earlier round
    flat = extra_root(tmp_path, "gem", "SECRET_MODEL_C", "flat")
    out = tmp_path / "runs/votes"
    queue = vote.prepare([source, flat], out, workspace=tmp_path, baseline_roots=[source])
    assert len(queue.items) == 20  # C-A and C-B per round; no A-B
    for item in queue.items:
        assert "SECRET_MODEL_C" in {c.model_id for c in (item.pair.left, item.pair.right)}
    manifest = json.loads((out / "package.local.json").read_text())
    assert manifest["candidates"] == 30 and manifest["baseline_roots"] == [str(source.resolve())]
    with pytest.raises(ValueError, match="differs"):
        vote.prepare([source, flat], out, workspace=tmp_path)
    with pytest.raises(ValueError, match="baseline root"):
        vote.prepare([source], tmp_path / "runs/x", workspace=tmp_path, baseline_roots=[flat])
