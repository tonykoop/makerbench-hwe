"""#1103: the side-by-side CADAM routes smoke is runnable with one command and safe by default (fakes only)."""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from makerbench import cadam_routes_smoke as smoke
from makerbench.nightly_cad import NightlyExecutor
from makerbench.nightly_revisions import RevisionQueueError
from makerbench.run_log_io import atomic_write_json, file_lock

from test_nightly_cad import _image, _registry  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadam_routes_smoke.py"
NOW = datetime(2026, 7, 19, 7, 30, tzinfo=timezone.utc)
SECRET = "SECRET-VALUE-0123456789"

spec = importlib.util.spec_from_file_location("cadam_routes_smoke_script", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def _fake_handler(job, entrant, run_dir, _registry):
    """A CADAM turn that writes the row the real one would (label meta included), or fails for codex."""
    if entrant.entrant_id == "cadam-cli-codex":
        raise RuntimeError("codex route unavailable <script>alert(1)</script>")
    trial_id = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
    render = run_dir / "render" / trial_id
    render.mkdir(parents=True, exist_ok=True)
    _image(render / "preview.png")
    gen = run_dir / "gen" / trial_id
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "candidate.scad").write_text(f"// {entrant.entrant_id}\ncube(1);\n")
    objective = {"objective_pass_rate": 0.8, "checks": {"renders": 1.0, "watertight": 1.0, "nonzero_volume": 1.0, "fits_envelope": 0.0, "body_count": 1.0}}
    row = {
        "trial_id": trial_id, "instrument_id": job.instrument_id, "model_id": entrant.entrant_id, "seed": 0, "rep": 0,
        "provider": "ingested", "status": "scored", "attempts": 1,
        "meta": {"entrant_label": entrant.route_label, "route": "CADAM via CLI · claude" if entrant.route == "cli-bridge" else "CADAM · OpenRouter"},
        "result": {"render_ok": True, "artifacts": {"png_path": (render / "preview.png").as_posix(), "stl_path": None},
                   "gen": {"scad_path": (gen / "candidate.scad").as_posix()}, "objective": objective},
    }
    path = run_dir / "run_log.json"
    with file_lock(path):
        log = json.loads(path.read_text())
        log["trials"].append(row)
        atomic_write_json(path, log)
    return 0.42 if entrant.route == "openrouter" else 0.0


@pytest.fixture
def secrets(tmp_path):
    path = tmp_path / "nightly.env"
    path.write_text(f"CADAM_USER_ID=u\nCADAM_ACCESS_TOKEN={SECRET}\nSUPABASE_SERVICE_ROLE_KEY={SECRET}\nOPENROUTER_API_KEY={SECRET}\n")
    return path


def _args(tmp_path, secrets, *extra):
    reference = _image(tmp_path / "sambuca.png")
    return ["--secrets", str(secrets), "--runs-root", str(tmp_path / "runs"), "--out", str(tmp_path / "out"),
            "--reference", str(reference), "--registry", str(_registry(tmp_path / "registry.json")), *extra]


def test_entrants_are_three_distinct_routes_with_the_requested_models():
    a, b, c = smoke.smoke_entrants()
    assert (a["route"], a["model_id"], a["max_cost_usd"]) == ("openrouter", "anthropic/claude-fable-5.1", 3.0)
    assert (b["route"], b["runtime"]) == ("cli-bridge", "claude") and (c["route"], c["runtime"], c["model_id"]) == ("cli-bridge", "codex", "openai/gpt-6.1-sol")
    assert len({a["entrant_id"], b["entrant_id"], c["entrant_id"]}) == 3
    lines = "\n".join(smoke.plan_lines([a, b, c], "ref.jpg", "out"))
    assert "NOT hard-capped" in lines and "only reserved" in lines and "capped at $3.00" not in lines
    assert "claude subscription quota" in lines and "codex subscription quota" in lines


def test_default_mode_is_plan_only_runs_nothing_and_prints_no_secret(tmp_path, secrets, capsys):
    def exploding(**_kwargs):
        raise AssertionError("plan mode must never construct an executor")

    code = script.main(_args(tmp_path, secrets), executor_factory=exploding, environ={})
    out = capsys.readouterr().out
    assert "nothing is run in plan mode" in out and "plan only" in out
    assert "[FAIL] route" in out or "[PASS] route" in out  # the per-route preflight ran
    assert SECRET not in out and code in {0, 1}
    assert not (tmp_path / "out").exists() and not (tmp_path / "runs").exists()


@pytest.mark.parametrize("flags", [["--run"], ["--run", "--tony-go"], ["--run", "--yes"]])
def test_run_without_both_go_flags_is_refused_before_anything_happens(tmp_path, secrets, capsys, flags):
    code = script.main(_args(tmp_path, secrets, *flags), executor_factory=lambda **k: pytest.fail("ran"), environ={})
    err = capsys.readouterr().err
    assert code == 2 and "--tony-go" in err and "--yes" in err and "NOT hard-capped" in err


def test_run_with_go_but_a_no_go_preflight_is_refused(tmp_path, secrets, capsys):
    code = script.main(_args(tmp_path, secrets, "--run", "--tony-go", "--yes"), executor_factory=lambda **k: pytest.fail("ran"), environ={})
    assert code == 1 and "preflight is NO-GO" in capsys.readouterr().err


def test_full_run_with_fakes_publishes_a_three_column_page(tmp_path, secrets, monkeypatch, capsys):
    from makerbench import nightly_preflight as pre

    monkeypatch.setattr(pre, "build_report", lambda **kw: type("R", (), {"ok": True})())
    monkeypatch.setattr(pre, "render_report_lines", lambda report: ["preflight: GO (faked)"])

    def factory(**kwargs):
        return NightlyExecutor(**kwargs, now=lambda: NOW, handlers={"cadam": _fake_handler})

    code = script.main(_args(tmp_path, secrets, "--run", "--tony-go", "--yes"), executor_factory=factory, environ={})
    out = capsys.readouterr().out
    assert code == 0 and "comparison page:" in out and SECRET not in out
    page_dir = next((tmp_path / "out").glob("*T*Z-cadam-routes-smoke-*"))
    page = (page_dir / "index.html").read_text()
    assert page.count("<section>") == 3
    for label in ("CADAM · OpenRouter · anthropic/claude-fable-5.1", "CADAM via CLI · claude ·", "CADAM via CLI · codex · openai/gpt-6.1-sol"):
        assert label in page
    assert "never merged" in page and "not apples-to-apples" in page
    assert "<script>" not in page and "&lt;script&gt;" in page  # the failed column's error is escaped
    assert "no render" in page  # the failed codex column has no image
    assert (page_dir / "cadam-openrouter-fable-5-1" / "preview.png").is_file() and (page_dir / "cadam-cli-claude" / "candidate.scad").is_file()
    data = json.loads((page_dir / "comparison.json").read_text())
    assert [c["entrant"] for c in data["columns"]] == ["cadam-openrouter-fable-5-1", "cadam-cli-claude", "cadam-cli-codex"]
    assert data["columns"][0]["cost"] == {"kind": "usd", "usd": 0.42} and data["columns"][2]["status"] == "error"
    assert data["columns"][1]["cost"] == {"kind": "subscription"}  # the claude CLI column: a $0 ledger charge is not a bill
    sections = page.split("<section>")[1:]
    assert "OpenRouter-billed" in sections[0] and "subscription quota" in sections[1]
    assert "OpenRouter-billed" not in sections[1] and "OpenRouter-billed" not in sections[2] and "$0.0000" not in page
    assert "url=" in (tmp_path / "out" / "index.html").read_text()  # top-level pointer to the latest run


def test_missing_entrant_gets_an_honest_column(tmp_path):
    run = tmp_path / "20260719T073000Z-x"
    run.mkdir()
    atomic_write_json(run / "run_log.json", {"config": {"instrument_ids": ["sambuca"]}, "trials": []})
    data = smoke.comparison(run, ROOT, order=["cadam-cli-claude"])
    (col,) = data["columns"]
    assert col["status"] == "missing" and "no design row" in col["failures"][0]
    assert "no render" in smoke.render_page(data, {})


def test_the_script_is_executable_and_documents_one_command():
    assert SCRIPT.stat().st_mode & 0o111
    doc = script.__doc__
    assert "--run --tony-go --yes" in doc and "runs nothing, spends nothing" in doc and "NOT hard-capped" in doc


# ---- publishing never follows a symlink out of the output folder ---------------------------------


def _finished_run(tmp_path):
    job = smoke.smoke_job("cadam-routes-smoke-x", _image(tmp_path / "ref.png").as_posix(), smoke.smoke_entrants())
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps(smoke.smoke_queue(job)))
    executor = NightlyExecutor(queue_path=queue, registry_path=_registry(tmp_path / "registry.json"),
                               output_root=tmp_path / "runs", now=lambda: NOW, handlers={"cadam": _fake_handler})
    return Path(executor.run()["run_dir"])


def _publish(run, out, tmp_path):
    entrants = smoke.smoke_entrants()
    return smoke.publish(run, out, ROOT, order=[e["entrant_id"] for e in entrants],
                         labels={e["entrant_id"]: e["entrant_id"] for e in entrants}, routes={e["entrant_id"]: e["route"] for e in entrants})


def test_publish_refuses_a_symlinked_page_and_leaves_the_target_untouched(tmp_path):
    run = _finished_run(tmp_path)
    out = tmp_path / "out"
    (out / run.name).mkdir(parents=True)
    victim = tmp_path / "unrelated.txt"
    victim.write_text("keep me")
    (out / run.name / "index.html").symlink_to(victim)
    with pytest.raises(RevisionQueueError, match="symlink"):
        _publish(run, out, tmp_path)
    assert victim.read_text() == "keep me"


@pytest.mark.parametrize("what", ["entrant_dir", "run_dir", "out_dir", "pointer", "json"])
def test_publish_refuses_other_symlinked_destinations(tmp_path, what):
    run = _finished_run(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    out = tmp_path / "out"
    if what == "out_dir":
        out.symlink_to(outside, target_is_directory=True)
    else:
        (out / run.name).mkdir(parents=True)
        if what == "entrant_dir":
            (out / run.name / "cadam-cli-claude").symlink_to(outside, target_is_directory=True)
        elif what == "run_dir":
            (out / run.name).rmdir()
            (out / run.name).symlink_to(outside, target_is_directory=True)
        elif what == "pointer":
            (out / "index.html").symlink_to(outside / "pointer-victim")
        else:
            (out / run.name / "comparison.json").symlink_to(outside / "json-victim")
    with pytest.raises(RevisionQueueError):
        _publish(run, out, tmp_path)
    assert list(outside.iterdir()) == []  # nothing was written through any link


def test_publish_into_a_clean_folder_still_works(tmp_path):
    run = _finished_run(tmp_path)
    page = _publish(run, tmp_path / "out", tmp_path)
    assert page.is_file() and (tmp_path / "out" / "index.html").is_file()
