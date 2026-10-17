"""No provider calls: exercise prompts, real native sandbox and Arena isolation."""
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess

import pytest

from makerbench import code_cad_providers as providers, wood_assembly as wood
from makerbench import wood_assembly_harness as harness
from makerbench.code_cad_arena_runner import collect_objective_scoreline
from makerbench.run_log_io import trial_rows


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "RUNS_ROOT", tmp_path)
    return tmp_path


def handwritten_source(seed=0):
    source = (Path(__file__).parent / "fixtures/wood_assembly_fixture.py").read_text()
    source = source.replace('spec = json.loads(Path("/work/entrant.py").read_text())',
                            f"spec = {wood.public_spec(seed)!r}")
    return source.replace('variant = sys.argv[1]', 'variant = "good"')


def test_prepare_is_public_image_only_and_prompt_is_complete(runs):
    request, manifest = harness.prepare_trial(runs / "prepared", "codex-test", 1)
    prompt = (runs / "prepared/prompt.txt").read_text()
    provenance = json.loads((runs / "prepared/request.json").read_text())
    assert provenance["prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert request.context_tier == "image"
    assert request.spec["size_mm"] == [320,220,130]
    files = list((runs / "prepared/workspace").iterdir())
    assert all(p.suffix in {".png", ".json"} for p in files)
    image = next(p for p in files if p.suffix == ".png")
    assert image.read_bytes() == (wood.TASK_PATH.parent / "reference.png").read_bytes()
    assert manifest
    for name in [*(f"parts/{part}.FCStd" for part in wood.PARTS),
                 "assembly.FCStd", "cut-list.csv", "joints.json"]:
        assert name in prompt
    for phrase in ["fully constrained", "relative", "twelve", "butt_glue", "120-second",
                   "4 GiB", "<=128 objects", "<=256 archive", "0.05 mm", "1e-7 mm³",
                   "zero live DoF", "+0.1 mm", "App::PropertyLength", "Tongue01Length",
                   "Length (local X)", "Width (local Y)", "must also equal its cut-list row",
                   "projected shared area"]:
        assert phrase in prompt
    assert "assign the finished shape" not in prompt
    assert "Do not export files yourself" not in prompt
    assert "A fresh document `doc` is provided" not in prompt
    assert not (runs / "prepared/run_log.json").exists()
    with pytest.raises(ValueError, match="never overwritten"):
        harness.prepare_trial(runs / "prepared", "codex-test", 1)


@pytest.mark.parametrize("seed", [-1,3,True])
def test_public_seeds_only(runs, seed):
    with pytest.raises(ValueError, match="public dev"):
        harness.run_trials(runs / "bad", ["stub-control"], [seed], generators={})


def test_outputs_cannot_escape_runs(runs):
    with pytest.raises(ValueError, match="runs/"):
        harness.prepare_trial(runs.parent / "escaped", "codex-test", 0)
    (runs / "escape").symlink_to(runs.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="runs/"):
        harness.prepare_trial(runs / "escape/nested", "codex-test", 0)


def test_cli_requires_separate_s4_approval(runs, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["harness", "run", str(runs / "blocked"), "--models", "codex-test"])
    monkeypatch.setattr(providers, "resolve_generator", lambda *a, **kw: pytest.fail("provider invoked"))
    with pytest.raises(SystemExit) as error:
        harness.main()
    assert error.value.code == 2
    assert not (runs / "blocked").exists()


@pytest.mark.parametrize("failure", ["provider", "compile", "fence", "invalid-grade", "zip"])
def test_failure_classification_resume_and_isolation(runs, monkeypatch, failure):
    calls = []

    def generate(request):
        calls.append(request)
        if failure == "provider":
            raise RuntimeError("synthetic infrastructure control")
        return "" if failure == "fence" else "```python\npass\n```"

    def compile_source(*args):
        if failure in {"compile", "zip"}:
            raise wood.AssemblyExecutionError("synthetic entrant execution failure")

    monkeypatch.setattr(harness, "execute_source", compile_source)
    monkeypatch.setattr(wood, "grade_assembly", lambda *a, **kw: {"passed": False, "checks": []})
    run = runs / "run"
    log = harness.run_trials(run, ["stub-control"], [0], generators={"stub-control": generate})
    row = log["trials"][0]
    assert row["attempts"] == 1
    if failure == "provider":
        assert row["status"] == "error" and row["result"] is None
        assert row["meta"]["failure_class"] == "infrastructure"
        assert row["meta"]["passed"] is None
    else:
        assert row["result"]["failure_class"] == "entrant"
        assert row["result"]["passed"] is False
    assert trial_rows(log) == []
    assert collect_objective_scoreline(log) == []
    assert log["config"]["track"] == "wood_assembly"
    harness.run_trials(run, ["stub-control"], [0], generators={"stub-control": generate})
    assert len(calls) == 1  # no hidden retries, including failed rows
    with pytest.raises(ValueError, match="identical"):
        harness.run_trials(run, ["stub-control"], [0,1], generators={"stub-control": generate})


@pytest.mark.parametrize("tag", [{"track": "wood_assembly"}, {"backend": "freecad_assembly"}])
def test_mixed_log_native_rows_cannot_become_mesh_scores(tag):
    normal = {"trial_id": "ordinary", "status": "error"}
    for location in ("row", "result", "meta", "config"):
        native = {"trial_id": "native", "status": "error"}
        config = {}
        if location == "config":
            config = tag
        elif location == "row":
            native.update(tag)
        else:
            native[location] = tag
        log = {"config": config, "trials": [normal, native]}
        assert trial_rows(log) == ([] if location == "config" else [normal])


def test_real_confined_native_generation_grade_and_editable_outputs(runs, monkeypatch):
    if not os.environ.get("MAKERBENCH_FREECAD_APPDIR"):
        pytest.skip("pinned FreeCAD required for native generation integration")
    # Real script runs in bwrap: host path and inherited env cannot be read;
    # enforce the actual address-space limit inside the untrusted process.
    secret = runs / "host-only.txt"
    secret.write_text("synthetic isolation marker")
    monkeypatch.setenv("WOOD_HARNESS_HOST_MARKER", "synthetic")
    probe = (
        "import os, resource\nfrom pathlib import Path\n"
        f"assert not Path({str(secret)!r}).exists()\n"
        "assert 'WOOD_HARNESS_HOST_MARKER' not in os.environ\n"
        f"assert resource.getrlimit(resource.RLIMIT_AS) == ({harness.MEMORY_LIMIT_BYTES},)*2\n"
        "assert not Path('/inputs').exists()\n"
        f"assert os.readlink('/proc/self/ns/net') != {os.readlink('/proc/self/ns/net')!r}\n"
    )
    def generator(request):
        return probe + handwritten_source()  # real adapters already strip fences
    log = harness.run_trials(runs / "native", ["stub-control"], [0], generators={"stub-control": generator})
    result = log["trials"][0]["result"]
    assert result and result["passed"], log
    assert len(result["grade"]["artifact_sha256"]) == 9
    bundle = runs / "native" / result["bundle_path"]
    assert wood.grade_assembly(bundle)["passed"]  # relocated documents survive generation worker exit
    assert trial_rows(log) == []


def test_real_script_failure_is_entrant_not_infrastructure(runs):
    if not os.environ.get("MAKERBENCH_FREECAD_APPDIR"):
        pytest.skip("pinned FreeCAD required for native generation integration")
    log = harness.run_trials(runs / "bad-native", ["stub-control"], [0], generators={
        "stub-control": lambda request: "```python\nraise ValueError('synthetic failure')\n```"})
    result = log["trials"][0]["result"]
    assert result["status"] == "generation_failed"
    assert result["failure_class"] == "entrant" and result["passed"] is False


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_provider_transport_uses_native_prompt_image_and_no_retry(runs, monkeypatch, provider):
    model = "codex-test" if provider == "codex" else "claude-code-test"
    request, _ = harness.prepare_trial(runs / provider, model, 0)
    calls = []

    def fake_cli(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess([], 1, stdout="", stderr="synthetic failure")

    monkeypatch.setattr(providers, "_run_entrant_cli" if provider == "codex" else "_run_cli", fake_cli)
    generator = providers.resolve_generator(model, backend="freecad", retry_attempts=0)
    with pytest.raises(RuntimeError, match="failed"):
        generator(request)
    assert len(calls) == 1
    command = calls[0][0][1 if provider == "codex" else 0]
    prompt = command[-1]
    assert "exactly these deliverables" in prompt and "assembly.FCStd" in prompt
    image = providers._staged_image_path(request)
    assert image and image in prompt
    if provider == "codex":
        assert f"--image={image}" in command


def test_native_config_records_provider_and_exact_model_identifier():
    config = harness.AssemblyConfig(("tongue_drum_wood_assembly",),
                                    ("codex-test-model", "claude-code-test-model"), (0,))
    assert [t.provider for t in harness.build_trial_matrix(config)] == ["codex", "claude"]
    metadata = config.as_dict()["model_metadata"]
    assert metadata["codex-test-model"]["model_identifier"] == "test-model"
    assert metadata["claude-code-test-model"]["model_identifier"] == "test-model"
    assert metadata["codex-test-model"]["provider"] == "codex"
