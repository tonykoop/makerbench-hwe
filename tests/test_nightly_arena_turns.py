"""Grader-driven revisions through the production nightly arena path, with no model calls."""

import json
from pathlib import Path

import pytest
import trimesh
from PIL import Image

from makerbench import code_cad_arena_runner as runner
from makerbench import nightly_cad, render
from makerbench.code_cad_objective import RenderArtifacts
from makerbench.code_cad_orchestrator import ArenaTrial
from makerbench.run_log_io import find_trial, trial_rows


@pytest.fixture
def arena(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (32, 32)).save(reference)
    registry = {"instruments": [{"id": "box", "task_brief": "A box instrument.",
                                 "envelope_mm": [100, 100, 100], "min_bodies": 1}]}
    calls = []

    def provider(request):
        calls.append(request)
        provider.usage_observations[(request.model_id, request.instrument_id, request.seed, request.context_tier)] = {
            "cost_usd": 0.1,
        }
        return "broken" if len(calls) == 1 else "fixed"

    provider.usage_observations = {}

    def compiler(source, out_dir):
        if source.read_text() == "broken":
            raise render.CompileError("'Part.Arc' object has no attribute 'Center'")
        mesh = out_dir / "output.stl"
        trimesh.creation.box(extents=[30, 30, 30]).export(mesh)
        preview = out_dir / "preview.png"
        Image.new("RGB", (32, 32)).save(preview)
        return RenderArtifacts(stl_path=mesh, png_path=preview)

    monkeypatch.setattr(nightly_cad.providers, "resolve_generator", lambda *a, **kw: provider)
    monkeypatch.setattr(runner, "compiler_for_backend", lambda backend: compiler)
    executor = nightly_cad.NightlyExecutor(queue_path=tmp_path / "queue.json",
                                         registry_path=tmp_path / "registry.json", output_root=tmp_path / "runs")

    def run(*, turns=3, stop_early=True, backend="freecad", max_cost_usd=0.0):
        entrant = nightly_cad.NightlyEntrant("entrant", "arena", "stub-a", backend=backend, context_tier="image",
                                            max_cost_usd=max_cost_usd)
        job = nightly_cad.NightlyJob("night", "box", str(reference), [entrant, entrant],
                                     turns=turns, stop_early=stop_early)
        cost = executor._run_arena(job, entrant, tmp_path / "run", registry)
        log = json.loads((tmp_path / "run" / "run_log.json").read_text())
        return log, find_trial(log, "box__seed0__rep0__entrant"), cost

    return run, calls, compiler, provider, registry


@pytest.mark.parametrize("backend", ["openscad", "freecad", "blender"])
def test_compile_failure_revises_same_entrant_and_scores_only_final(arena, backend):
    run, calls, _, _, _ = arena
    log, row, cost = run(backend=backend)
    assert len(calls) == 2 and calls[0].model_id == calls[1].model_id
    assert "'Part.Arc' object has no attribute 'Center'" in calls[1].prompt
    assert "broken" in calls[1].prompt and f"```{backend}" in calls[1].prompt
    assert row["status"] == "scored" and row["result"]["objective"]["passed"]
    turns = row["result"]["turns"]
    assert [t["turn"] for t in turns] == [1, 2]
    assert [t["revision_of"] for t in turns] == [None, 1]
    assert turns[0]["trigger"] is None and "Part.Arc" in turns[1]["trigger"]
    assert [t["cost_usd"] for t in turns] == [0.1, 0.1] and cost == pytest.approx(0.2)
    assert Path(turns[0]["source_path"]).parent.name == "turn-1"
    assert Path(turns[0]["source_path"]).read_text() == "broken"
    assert Path(row["result"]["gen"]["scad_path"]).read_text() == "fixed"
    run_dir = Path(turns[0]["source_path"]).parents[3]
    job = nightly_cad.NightlyJob("night", "box", "", [])
    entrant = nightly_cad.NightlyEntrant("entrant", "arena", "stub-a")
    assert nightly_cad._trial_cost(run_dir / "run_log.json", job, entrant) == pytest.approx(cost)
    if backend == "blender":
        assert trial_rows(log) == []  # revisions preserve the existing mesh-track boundary
    else:
        assert len(trial_rows(log)) == 1
        assert runner.collect_objective_scoreline(log)[0]["n_objective_trials"] == 1
        assert runner.collect_objective_scoreline(log)[0]["objective_pass_rate"] == 1.0


def test_failed_objective_feeds_checks_and_readable_render_back(arena, monkeypatch):
    run, calls, _, _, _ = arena

    def oversized_first(source, out_dir):
        mesh = out_dir / "output.stl"
        trimesh.creation.box(extents=[1000 if len(calls) == 1 else 30, 30, 30]).export(mesh)
        preview = out_dir / "preview.png"
        Image.new("RGB", (32, 32)).save(preview)
        return RenderArtifacts(stl_path=mesh, png_path=preview)

    monkeypatch.setattr(runner, "compiler_for_backend", lambda backend: oversized_first)
    _, row, _ = run()
    assert len(calls) == 2
    assert "fits_envelope" in calls[1].prompt and '"failures"' in calls[1].prompt
    staged = Path(calls[1].workspace_dir) / "revision-preview.png"
    assert staged.is_file() and str(staged) in calls[1].prompt
    assert staged.read_bytes() == Path(row["result"]["turns"][0]["render_path"]).read_bytes()


def test_turn_limit_and_stop_early_false(arena):
    run, calls, _, _, _ = arena
    _, row, _ = run(turns=3, stop_early=False)
    assert len(calls) == 3 and len(row["result"]["turns"]) == 3
    assert row["result"]["turns"][-1]["revision_of"] == 2


def test_one_turn_nightly_job_keeps_one_attempt_and_legacy_cost(arena):
    run, calls, _, _, _ = arena
    _, row, cost = run(turns=1)
    assert len(calls) == row["attempts"] == 1 and row["status"] == "auto_fail"
    assert "turns" not in row["result"] and type(cost) is float and cost == 0.0


def test_first_all_pass_stops_immediately(arena, monkeypatch):
    run, calls, _, _, _ = arena

    def already_fixed(request):
        calls.append(request)
        return "fixed"

    monkeypatch.setattr(nightly_cad.providers, "resolve_generator", lambda *a, **kw: already_fixed)
    _, row, _ = run()
    assert len(calls) == len(row["result"]["turns"]) == 1


def test_compiler_infrastructure_error_keeps_turn_cost_and_does_not_revise(arena, monkeypatch):
    run, calls, _, _, _ = arena

    def unavailable(source, out_dir):
        raise RuntimeError("sandbox unavailable")

    monkeypatch.setattr(runner, "compiler_for_backend", lambda backend: unavailable)
    _, row, cost = run()
    assert len(calls) == 1 and row["status"] == "error"
    assert row["meta"]["turns"][0]["error"] == "sandbox unavailable"
    assert cost == pytest.approx(0.1)


def test_exhausted_compile_failures_score_the_final_failure(arena, monkeypatch):
    run, calls, _, _, _ = arena

    def fails(source, out_dir):
        raise render.CompileError("still broken")

    monkeypatch.setattr(runner, "compiler_for_backend", lambda backend: fails)
    log, row, _ = run(turns=2)
    assert len(calls) == 2 and row["status"] == "auto_fail"
    assert len(row["result"]["turns"]) == 2
    assert runner.collect_objective_scoreline(log)[0]["objective_pass_rate"] == 0.0


def test_provider_failure_is_not_a_compile_revision(arena):
    run, calls, _, _, _ = arena
    # The second provider call fails after one genuine compile failure.
    class FailOnRevision:
        def __call__(self, request):
            if calls:
                raise TimeoutError("CLI unavailable")
            calls.append(request)
            return "broken"

    from unittest.mock import patch

    with patch.object(nightly_cad.providers, "resolve_generator", return_value=FailOnRevision()):
        _, row, _ = run()
    assert row["status"] == "error" and "CLI unavailable" in row["error"]
    assert len(row["meta"]["turns"]) == 2 and row["meta"]["turns"][-1]["status"] == "error"


def test_one_turn_preserves_legacy_payload_bytes(tmp_path):
    registry = {"instruments": [{"id": "box", "task_brief": "A box."}]}
    trial = ArenaTrial("trial", "box", "stub-a", 0, 0, "stub")
    calls = []

    def generator(request):
        calls.append(request)
        return "broken"

    def compiler(source, out_dir):
        raise render.CompileError("compile failed")

    kwargs = dict(registry=registry, run_dir=tmp_path, generators={"stub-a": generator},
                  compiler=compiler, backend="freecad")
    legacy = runner.make_execute_trial(**kwargs)(trial)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    explicit = runner.make_execute_trial(**kwargs, max_turns=1)(trial)
    assert json.dumps(legacy, sort_keys=True) == json.dumps(explicit, sort_keys=True)
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert calls[0] == calls[1] and "turns" not in explicit


@pytest.mark.parametrize("turns", [0, 9, True, "3", 1.5, None])
def test_job_turn_validation_and_legacy_default(arena, turns):
    _, calls, _, _, _ = arena
    job = nightly_cad.NightlyJob.from_dict({"job_id": "j", "instrument_id": "box", "turns": turns})
    # Use a revision to isolate turn validation from reference/two-entrant validation.
    job.revision = {"test": True}
    job.entrants = [nightly_cad.NightlyEntrant("e", "arena", "stub-a")]
    with pytest.raises(ValueError, match="turns must"):
        job.validate()
    assert nightly_cad.NightlyJob.from_dict({}).turns == 1
    assert nightly_cad.NightlyJob("j", "box", "", []).turns == 3
    assert not calls


@pytest.mark.parametrize('stop_early', [True, False])
def test_retired_check_does_not_block_stop_or_enter_feedback(arena, monkeypatch, stop_early):
    run, calls, compiler, _, _ = arena
    original = runner.evaluate_objective_trial

    def with_retired_check(**kwargs):
        payload = original(**kwargs)
        if payload['status'] == 'scored':
            payload['objective']['sub_scores']['min_wall'] = 0.0
        return payload

    def successful(source, out_dir):
        source.write_text('fixed')
        return compiler(source, out_dir)

    monkeypatch.setattr(runner, 'evaluate_objective_trial', with_retired_check)
    monkeypatch.setattr(runner, 'compiler_for_backend', lambda _: successful)
    _, row, _ = run(stop_early=stop_early, turns=2)
    assert len(calls) == (1 if stop_early else 2)
    if not stop_early:
        assert 'All objective checks passed' in calls[1].prompt
    assert row['result']['objective']['passed']


def test_failed_check_feedback_filters_retired_check(arena, monkeypatch):
    run, calls, _, _, _ = arena

    def objective(**kwargs):
        return {'status': 'scored', 'error': None, 'artifacts': {'png_path': None},
                'objective': {'passed': False, 'objective_pass_rate': 0.3,
                              'sub_scores': {'fits_envelope': 0.0, 'renders': 1.0, 'min_wall': 0.0}}}

    monkeypatch.setattr(runner, 'evaluate_objective_trial', objective)
    run(turns=2)
    assert '"fits_envelope": 0.0' in calls[1].prompt
    assert '"objective_pass_rate": 0.5' in calls[1].prompt
    assert 'min_wall' not in calls[1].prompt


def test_compile_failure_removes_prior_workspace_preview(arena, monkeypatch):
    run, calls, compiler, provider, _ = arena
    preview_at_dispatch = []

    def observe(request):
        preview_at_dispatch.append((Path(request.workspace_dir) / 'revision-preview.png').exists())
        return provider(request)

    observe.usage_observations = provider.usage_observations

    def first_render_then_failure(source, out_dir):
        if len(calls) == 2:
            raise render.CompileError('second turn failed')
        source.write_text('fixed')
        return compiler(source, out_dir)

    monkeypatch.setattr(nightly_cad.providers, 'resolve_generator', lambda *a, **kw: observe)
    monkeypatch.setattr(runner, 'compiler_for_backend', lambda _: first_render_then_failure)
    run(turns=3, stop_early=False)
    assert preview_at_dispatch == [False, True, False]
    assert 'revision-preview.png' not in calls[2].prompt
    assert 'second turn failed' in calls[2].prompt


@pytest.mark.parametrize('ceiling,expected_turns', [(0.1, 1), (0.15, 2), (0.2, 2), (0.0, 3)])
def test_cost_ceiling_stops_further_turns(arena, ceiling, expected_turns):
    run, calls, _, _, _ = arena
    _, row, cost = run(turns=3, stop_early=False, max_cost_usd=ceiling)
    assert len(calls) == expected_turns
    assert cost == pytest.approx(0.1 * expected_turns)
    assert len(row['result']['turns']) == expected_turns
    if ceiling:
        assert row['result']['revision_stop_reason'] == 'entrant cost ceiling reached'
