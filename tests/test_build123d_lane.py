"""build123d entrant lane (#799): prompt, sandboxed compile, STEP/STL, per-backend gates.

Stub entrants only. Real-compile tests need the optional CadQuery/build123d
extra plus Bubblewrap; they skip without them, except that
``MAKERBENCH_REQUIRE_SANDBOX=1`` turns a missing sandbox into a failure.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import trimesh

from makerbench import build123d_backend, cadquery_backend, render
from makerbench import code_cad_arena_runner as runner
from makerbench import code_cad_providers as providers
from makerbench.code_cad_generator import GenerationRequest

REQUIRE_SANDBOX = os.environ.get("MAKERBENCH_REQUIRE_SANDBOX") == "1"
_BWRAP = cadquery_backend._bubblewrap_available(cadquery_backend._scrub_environment(os.environ))
if REQUIRE_SANDBOX and not _BWRAP:  # pragma: no cover - CI guard
    pytest.fail("MAKERBENCH_REQUIRE_SANDBOX=1 but the CadQuery/build123d sandbox cannot start",
                pytrace=False)
needs_real_build123d = pytest.mark.skipif(
    not (_BWRAP and build123d_backend.build123d_available() and render.openscad_available()),
    reason="build123d extra, bwrap or openscad unavailable here",
)

TUBE = (
    "from build123d import Cylinder, Pos\n"
    "outer = Cylinder(radius=10, height=30)\n"
    "bore = Cylinder(radius=8, height=40)\n"
    "result = outer - bore\n"
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- registration and prompt ---------------------------------------------------------------


def test_build123d_is_a_registered_sandboxed_backend():
    assert runner.compiler_for_backend("build123d") is build123d_backend.compile_build123d_to_artifacts
    assert (runner.compiler_for_backend("build123d", sandboxed=True)
            is build123d_backend.compile_build123d_to_artifacts)


def test_prompt_template_is_build123d_specific():
    request = GenerationRequest(model_id="stub-a", instrument_id="lyre", seed=0, spec={},
                                prompt="Generate one parametric OpenSCAD program. Emit OpenSCAD only.",
                                prompt_sha256="x")
    prompt = providers.arena_prompt(request, "build123d")
    assert "build123d" in prompt and "Do not use CadQuery" in prompt
    assert "OpenSCAD" not in prompt
    assert "```build123d" in prompt


def test_fence_extraction_accepts_build123d_blocks():
    text = "sure\n```build123d\nfrom build123d import Box\nresult = Box(1, 1, 1)\n```\n"
    assert providers.extract_candidate(text, "build123d").startswith("from build123d import Box")


def test_stub_entrant_emits_a_build123d_part_script():
    generator = providers.resolve_generator("stub-a", stub=True, backend="build123d")
    request = GenerationRequest(model_id="stub-a", instrument_id="lyre", seed=3, spec={},
                                prompt="p", prompt_sha256="x")
    script = generator(request)
    assert build123d_backend.imports_build123d(script)
    assert "cadquery" not in script


# --- lane check (host-side parse only) ------------------------------------------------------


def test_cadquery_script_is_refused_by_the_build123d_lane(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_backend, "compile_cadquery_to_artifacts",
                        lambda *a: pytest.fail("a non-build123d script must never reach the worker"))
    script = _write(tmp_path, "cq.py", "import cadquery as cq\nresult = cq.Workplane().box(1, 1, 1)\n")
    with pytest.raises(render.CompileError, match="requires a script that imports build123d"):
        build123d_backend.compile_build123d_to_artifacts(script, tmp_path / "out")


def test_syntax_error_is_a_candidate_defect_and_is_never_executed(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_backend, "compile_cadquery_to_artifacts",
                        lambda *a: pytest.fail("unparseable source must never reach the worker"))
    script = _write(tmp_path, "bad.py", "import build123d\nresult = (\n")
    with pytest.raises(render.CompileError, match="syntax error"):
        build123d_backend.compile_build123d_to_artifacts(script, tmp_path / "out")


def test_build123d_script_is_compiled_only_by_the_bubblewrap_worker(tmp_path, monkeypatch):
    calls = []

    def fake_worker(script_path, out_dir):
        calls.append(Path(script_path))
        raise render.CompileError("worker ran")

    monkeypatch.setattr(cadquery_backend, "compile_cadquery_to_artifacts", fake_worker)
    script = _write(tmp_path, "tube.py", TUBE)
    with pytest.raises(render.CompileError, match="worker ran"):
        build123d_backend.compile_build123d_to_artifacts(script, tmp_path / "out")
    assert calls == [script.resolve()]


# --- real sandbox ---------------------------------------------------------------------------


@needs_real_build123d
def test_fixture_script_compiles_in_the_sandbox_to_step_and_stl(tmp_path):
    script = _write(tmp_path, "tube.py", TUBE)

    artifacts = build123d_backend.compile_build123d_to_artifacts(script, tmp_path / "out")

    step = tmp_path / "out" / "output.step"
    assert step.is_file() and step.read_bytes().startswith(b"ISO-10303-21")
    mesh = trimesh.load(artifacts.stl_path, force="mesh")
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(3.14159265 * (100 - 64) * 30, rel=0.02)
    assert any(w.startswith("brep_mesh_volume:") for w in artifacts.warnings)


@needs_real_build123d
def test_malicious_build123d_script_cannot_read_host_files(tmp_path):
    secret = tmp_path / "host-secret.txt"
    secret.write_text("HOST-SECRET-b123d", encoding="utf-8")
    script = _write(tmp_path, "evil.py", (
        "from build123d import Box\n"
        f"data = open({secret.as_posix()!r}).read()\n"
        "result = Box(1, 1, len(data))\n"
    ))

    with pytest.raises(render.CompileError) as excinfo:
        build123d_backend.compile_build123d_to_artifacts(script, tmp_path / "out")

    assert "FileNotFoundError" in str(excinfo.value) or "No such file" in str(excinfo.value)
    assert "HOST-SECRET-b123d" not in str(excinfo.value)
    assert not (tmp_path / "out" / "output.stl").exists()


# --- gates and scorelines -------------------------------------------------------------------


def test_scoreline_rows_carry_the_backend_and_split_mixed_runs():
    log = {
        "config": {"backend": "build123d"},
        "trials": [
            {"model_id": "m", "status": "scored",
             "result": {"objective": {"objective_pass_rate": 1.0}}},
            {"model_id": "m", "status": "scored",
             "result": {"backend": "openscad", "objective": {"objective_pass_rate": 0.5}}},
        ],
    }
    rows = runner.collect_objective_scoreline(log)
    assert [(r["entrant"], r["backend"], r["objective_pass_rate"]) for r in rows] == [
        ("m", "build123d", 1.0), ("m", "openscad", 0.5)]


def test_legacy_logs_without_a_backend_report_openscad():
    log = {"trials": [{"model_id": "m", "status": "scored",
                       "result": {"objective": {"objective_pass_rate": 0.25}}}]}
    (row,) = runner.collect_objective_scoreline(log)
    assert row == {"entrant": "m", "backend": "openscad", "objective_pass_rate": 0.25,
                   "n_objective_trials": 1}


def test_trial_payload_and_gate_run_record_the_build123d_backend(tmp_path, monkeypatch):
    from makerbench.code_cad_objective import RenderArtifacts
    from makerbench.code_cad_orchestrator import ArenaTrial

    def fake_compile(script_path, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        stl = out_dir / "output.stl"
        trimesh.creation.box(extents=[10, 10, 10]).export(stl)
        png = out_dir / "preview.png"
        png.write_bytes(b"png")
        return RenderArtifacts(stl_path=stl, png_path=png)

    registry = {"instruments": [{"id": "lyre", "envelope_mm": [20, 20, 20], "min_bodies": 1}]}
    generator = providers.resolve_generator("stub-a", stub=True, backend="build123d")
    execute = runner.make_execute_trial(registry=registry, run_dir=tmp_path,
                                        generators={"stub-a": generator},
                                        compiler=fake_compile, backend="build123d")
    trial = ArenaTrial(trial_id="lyre__seed0__rep0__stub-a", instrument_id="lyre",
                       model_id="stub-a", seed=0, rep=0, provider="stub")

    payload = execute(trial)

    assert payload["backend"] == "build123d"
    assert "objective_pass_rate" in payload["objective"]
    (row,) = runner.collect_objective_scoreline({"trials": [{"model_id": "stub-a",
                                                             "status": "scored",
                                                             "result": payload}]})
    assert row["backend"] == "build123d"
