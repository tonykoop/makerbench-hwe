"""Controlled matchup tests: public preview/queue/CLI, with no entrant calls."""

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from makerbench import nightly_cad
from makerbench.arena_studio import create_studio_app, doe
from makerbench.cli import app


@pytest.mark.parametrize(
    "axis,values,extra",
    [
        ("backend", ["solidworks", "fusion"], {}),
        ("model", ["claude-code-opus-5.5", "codex-gpt-5.6"], {}),
        # Public round-4 docs identify Luthier Bridge as Fusion live.
        ("bridge", ["fusion-live", "solidworks-live"], {"driver_models": ["gpt-5.6-sol"]}),
    ],
)
def test_canonical_matchups_vary_one_axis(axis, values, extra):
    result = doe.build_matchup(
        axis, values, instruments=["ocarina"], models=["codex-gpt-5.6"], **extra
    )
    canonical = "models" if axis == "model" else "backends"
    assert result["varied_axis"] == canonical
    assert result["varied_axes"] == [canonical]
    assert result["values"] == values
    assert len(result["cells"]) == 2
    assert result["held"]["instruments"] == "ocarina"
    assert result["held"]["levels"] == "L1"
    assert result["held"]["seeds"] == 0
    assert canonical not in result["held"]


@pytest.mark.parametrize("live_backend", ["fusion-live", "solidworks-live"])
def test_mixed_backend_matchup_requires_the_same_effective_model(live_backend):
    dimensions = {"instruments": ["ocarina"], "models": ["claude-opus-5-5"]}
    with pytest.raises(doe.DoeValidationError, match="effective model"):
        doe.build_matchup(
            "backends", ["openscad", live_backend],
            driver_models=["gpt-5.6-sol"], **dimensions,
        )
    result = doe.build_matchup(
        "backends", ["openscad", live_backend],
        driver_models=["claude-opus-5-5"], **dimensions,
    )
    assert {cell["model_id"] for cell in result["cells"]} == {"claude-opus-5-5"}
    assert result["varied_axes"] == ["backends"]


def test_confounded_matchup_needs_factorial():
    dimensions = dict(instruments=["ocarina"], models=["claude-code-opus-5.5", "codex-gpt-5.6"])
    with pytest.raises(doe.DoeValidationError, match="factorial"):
        doe.build_matchup("backend", ["solidworks", "fusion"], **dimensions)
    result = doe.build_matchup("backend", ["solidworks", "fusion"], factorial=True, **dimensions)
    assert len(result["cells"]) == 4
    assert result["varied_axes"] == ["models", "backends"]
    assert "models" not in result["held"] and "backends" not in result["held"]


@pytest.mark.parametrize("values", [[], ["openscad"], ["openscad", "openscad"]])
def test_requires_two_distinct_values(values):
    with pytest.raises(doe.DoeValidationError, match="two distinct"):
        doe.build_matchup("backend", values, instruments=["ocarina"], models=["model-a"])


def test_live_model_axis_must_not_silently_collapse():
    with pytest.raises(doe.DoeValidationError, match="driver_models"):
        doe.build_matchup(
            "model", ["a", "b"], instruments=["ocarina"], models=["a"], backends=["fusion-live"]
        )
    result = doe.build_matchup(
        "driver_model",
        ["gpt-5.6-sol", "gpt-6-sol"],
        instruments=["ocarina"],
        models=["a"],
        backends=["fusion-live"],
    )
    assert len(result["cells"]) == 2
    assert {c["model_id"] for c in result["cells"]} == {"gpt-5.6-sol", "gpt-6-sol"}


def test_inapplicable_driver_axis_and_conflicting_values_refused():
    with pytest.raises(doe.DoeValidationError, match="live backends"):
        doe.build_matchup("driver_model", ["a", "b"], instruments=["ocarina"], models=["m"])
    with pytest.raises(doe.DoeValidationError, match="conflict"):
        doe.build_matchup(
            "backend",
            ["solidworks", "fusion"],
            instruments=["ocarina"],
            models=["m"],
            backends=["openscad", "build123d"],
        )


@pytest.fixture
def studio(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"instruments": [{"id": "ocarina", "family": "woodwind"}]}))
    ref = tmp_path / "tasks" / "ocarina" / "reference.png"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b"fake-png")
    client = TestClient(
        create_studio_app(registry_path=registry, repo_root=tmp_path),
        base_url="http://127.0.0.1",
        headers={"origin": "http://127.0.0.1"},
    )
    return client, tmp_path


def test_studio_preview_and_queue_record_matchup(studio):
    client, root = studio
    params = {
        "instruments": "ocarina",
        "models": "codex-gpt-5.6",
        "varied_axis": "backends",
        "values": "solidworks,fusion",
    }
    response = client.get("/api/doe/preview", params=params)
    assert response.status_code == 200
    data = response.json()
    assert data["varied_axis"] == "backends" and data["held"]["models"] == "codex-gpt-5.6"
    assert data["summary"]["n_cells"] == 2
    client.post("/api/tasks/ocarina/approve?approved=true")
    response = client.post(
        "/api/doe/queue",
        json={
            "run_id": "matchup",
            "instruments": ["ocarina"],
            "models": ["codex-gpt-5.6"],
            "varied_axis": "backends",
            "values": ["solidworks", "fusion"],
        },
    )
    assert response.status_code == 200
    payload, jobs = nightly_cad.load_queue(root / response.json()["queue_path"])
    assert payload["varied_axis"] == "backends" and payload["held"] == data["held"]
    assert {e.backend for e in jobs[0].entrants} == {"solidworks", "fusion"}


def test_studio_rejects_multiple_axes_before_writing(studio):
    client, root = studio
    body = {
        "run_id": "confounded",
        "instruments": ["ocarina"],
        "models": ["codex-gpt-5.6"],
        "levels": ["L1", "L2"],
        "varied_axis": "backend",
        "values": ["solidworks", "fusion"],
    }
    response = client.post("/api/doe/queue", json=body)
    assert response.status_code == 400 and "factorial" in response.json()["detail"]
    assert not (root / "runs").exists()


def test_cli_matchup_json_and_factorial_gate(tmp_path):
    runner = CliRunner()
    args = [
        "arena",
        "matchup",
        "--vary",
        "model",
        "--values",
        "claude-code-opus-5.5,codex-gpt-5.6",
        "--instruments",
        "ocarina",
        "--models",
        "codex-gpt-5.6",
    ]
    out = tmp_path / "preview.json"
    result = runner.invoke(app, [*args, "--out", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == json.loads(out.read_text())
    assert payload["varied_axis"] == "models" and payload["held"]["backends"] == "openscad"
    assert runner.invoke(app, [*args, "--levels", "L1,L2"]).exit_code != 0
    factorial = runner.invoke(app, [*args, "--levels", "L1,L2", "--factorial"])
    assert factorial.exit_code == 0
    assert json.loads(factorial.output)["varied_axes"] == ["models", "levels"]


def test_seed_values_are_converted_and_unknown_axis_refused():
    result = doe.build_matchup("seed", ["0", "1"], instruments=["ocarina"], models=["m"])
    assert {c["seed"] for c in result["cells"]} == {0, 1}
    with pytest.raises(doe.DoeValidationError, match="seeds must be integers"):
        doe.build_matchup("seed", ["zero", "1"], instruments=["ocarina"], models=["m"])
    with pytest.raises(doe.DoeValidationError, match="varied_axis"):
        doe.build_matchup("unknown", ["a", "b"], instruments=["ocarina"], models=["m"])


def test_live_bridge_matchup_requires_an_explicit_driver():
    with pytest.raises(doe.DoeValidationError, match="explicit driver_models"):
        doe.build_matchup(
            "bridge", ["fusion-live", "solidworks-live"], instruments=["ocarina"], models=["m"]
        )


def test_matchup_metadata_survives_execution_and_objective_output(tmp_path):
    ref = tmp_path / "reference.png"
    ref.write_bytes(b"fake-png")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"instruments": [{"id": "ocarina", "family": "woodwind"}]}))
    result = doe.build_matchup(
        "model", ["stub-a", "stub-b"], instruments=["ocarina"], models=["stub-a"]
    )
    metadata = {
        key: result[key] for key in ("varied_axis", "values", "varied_axes", "factorial", "held")
    }
    payload, jobs = doe.build_nightly_queue(result["cells"], reference_images={"ocarina": str(ref)})
    queue = tmp_path / "queue.json"
    doe.write_queue_file(queue, {**payload, **metadata}, jobs)
    # Injecting a deterministic handler isolates orchestration and metadata
    # persistence from CAD and provider calls. Empty geometry stays non-votable.
    executor = nightly_cad.NightlyExecutor(
        queue_path=queue,
        registry_path=registry,
        output_root=tmp_path / "output",
        handlers={"arena": lambda *_: 0.0},
    )
    outcome = executor.run()
    assert outcome["matchup"] == metadata
    run_dir = next((tmp_path / "output").glob("*/run_log.json")).parent
    assert json.loads((run_dir / "run_log.json").read_text())["config"]["matchup"] == metadata
    assert json.loads((run_dir / "objective_scoreline.json").read_text())["matchup"] == metadata
