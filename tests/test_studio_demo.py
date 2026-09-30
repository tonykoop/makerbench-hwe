"""Public demo capabilities fail closed and never discover a local workspace."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from makerbench.arena_studio import create_studio_app
from makerbench.arena_studio.demo import PACKAGE, create_demo_app
from makerbench.cli import app

ROOT = Path(__file__).resolve().parents[1]


def test_post3_aggregated_seed_claim_matches_trial_counts():
    data = json.loads((PACKAGE / "data/showcase.json").read_text())
    for case in data["cases"][:2]:
        assert case["held"]["seeds"] == [0, 1, 2]
        assert all(row["n_objective_trials"] == len(case["held"]["seeds"]) for row in case["rows"])


def test_builder_rejects_misstated_seed_scope_and_keys_images_by_entrant(monkeypatch):
    spec = importlib.util.spec_from_file_location("demo_builder", ROOT / "scripts/build_studio_demo_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.build(ROOT)
    data["cases"][0]["held"]["seeds"] = 0
    with pytest.raises(ValueError, match="trial counts"):
        module.validate_aggregate_seeds(data)
    data = module.build(ROOT)
    data["cases"][0]["rows"][0]["n_objective_trials"] = 1
    with pytest.raises(ValueError, match="trial counts"):
        module.validate_aggregate_seeds(data)
    original = Path.read_text
    scoreline = ROOT / "docs/showcase/post3/matchup-model/objective_scoreline.json"
    def reversed_rows(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        if path == scoreline:
            content = json.loads(text)
            content["rows"].reverse()
            return json.dumps(content)
        return text
    monkeypatch.setattr(Path, "read_text", reversed_rows)
    content = module.build(ROOT)
    for row in content["cases"][0]["rows"]:
        relative = content["assets"][row["image"].rsplit("/", 1)[1]]
        assert row["entrant"].removeprefix("claude-code-") + "-seed0.png" in relative


@pytest.fixture
def client(tmp_path, monkeypatch):
    private_run = tmp_path / "runs/code_cad_arena/private-sentinel"
    private_run.mkdir(parents=True)
    (private_run / "run_log.json").write_text('{"private": "PRIVATE_SENTINEL"}')
    monkeypatch.chdir(tmp_path)
    return TestClient(create_demo_app(), base_url="http://127.0.0.1")


def test_snapshot_reproduces_and_has_no_preference_or_source_artifact_fields():
    spec = importlib.util.spec_from_file_location("demo_builder", ROOT / "scripts/build_studio_demo_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = json.loads((PACKAGE / "data/showcase.json").read_text())
    assert data == module.build(ROOT)
    assert len(data["cases"]) == 4
    assert sum(len(case["rows"]) for case in data["cases"]) == 29
    assert len(data["assets"]) == 28

    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                assert key not in {"elo", "elos", "votes", "voter", "voter_id", "rating", "source_code"}
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, str):
            assert "PRIVATE_SENTINEL" not in value
            assert not value.startswith(("/home/", "/tmp/", "/mnt/"))
    check(data)
    failed = [r for c in data["cases"] for r in c["rows"] if r["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["objective_pass_rate"] is None
    assert failed[0]["gates"] == {}


def test_demo_reads_only_committed_showcases_and_assets(client):
    assert client.get("/api/health").json()["demo"] is True
    runs = client.get("/api/runs").json()["runs"]
    assert {r["run_id"] for r in runs} == {"post3-models", "post3-backends", "strings-gallery", "kora"}
    for run in runs:
        response = client.get(f'/api/runs/{run["run_id"]}/summary')
        assert response.status_code == 200
        assert "PRIVATE_SENTINEL" not in response.text
        for row in response.json()["rows"]:
            if row["image"]:
                image = client.get(row["image"])
                assert image.status_code == 200
                assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get("/api/runs/private-sentinel/summary").status_code == 404
    assert client.get("/api/demo/assets/unknown.png").status_code == 404
    assert 'data-demo="true"' in client.get("/").text


def test_every_real_studio_mutation_is_refused_before_writes(client, tmp_path, monkeypatch):
    normal = create_studio_app(repo_root=tmp_path)
    paths = [r.path for r in normal.routes if "POST" in getattr(r, "methods", ())]
    assert paths
    def forbidden(*args, **kwargs):
        raise AssertionError("demo attempted a write or process launch")
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    for path in paths:
        for segment in ("run_id", "job_id", "task_id", "design_id", "revision_id", "rev_id"):
            path = path.replace("{" + segment + "}", "sentinel")
        assert client.post(path, json={}, headers={"origin": "http://127.0.0.1"}).status_code == 403, path
    for method in ("PUT", "PATCH", "DELETE"):
        assert client.request(method, "/api/runs/kora/vote", json={}).status_code == 403
    for path in ("/api/runs/kora/queue", "/api/runs/kora/leaderboard",
                 "/api/runs/kora/agreement", "/api/workbench/designs", "/api/nightly/queue"):
        assert client.get(path).status_code == 403
    assert client.get("/api/runs/kora/summary").status_code == 200


@pytest.mark.parametrize("flags", [["--allow-live"], ["--run-dir", "."], ["--registry", "custom.json"]])
def test_demo_cannot_open_private_or_live_workspaces(flags):
    result = CliRunner().invoke(app, ["studio", "--demo", *flags])
    assert result.exit_code == 2
    assert "--demo cannot use" in result.stdout
