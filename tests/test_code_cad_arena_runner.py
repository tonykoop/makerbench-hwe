"""Tests for the Code-CAD Arena end-to-end runner glue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench.code_cad_arena import build_elo_leaderboard
from makerbench.code_cad_agreement import build_agreement_summary
from makerbench.code_cad_generator import instrument_spec_from_registry
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts
from makerbench.code_cad_orchestrator import OrchestrationConfig, run_orchestration
from makerbench.code_cad_providers import make_stub_generator
from makerbench.code_cad_vote_surface import (
    build_blind_pair,
    record_vote,
    reveal_vote,
    append_vote_record,
    VoteCandidate,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
ARENA_REGISTRY = REPO_ROOT / "tasks" / "code_cad_arena" / "registry.json"


class TestArenaRegistry:
    def test_registry_loads_and_all_ids_resolve(self):
        registry = runner.load_arena_registry(ARENA_REGISTRY)
        for instrument_id in ("ocarina", "kena", "tongue-drum", "kora"):
            spec = instrument_spec_from_registry(registry, instrument_id)
            assert spec["id"] == instrument_id
            assert spec["task_brief"].strip()
            assert len(spec["envelope_mm"]) == 3

    def test_kora_is_the_assembly_task(self):
        registry = runner.load_arena_registry(ARENA_REGISTRY)
        kora = instrument_spec_from_registry(registry, "kora")
        assert kora["assembly"] is True
        assert kora["min_bodies"] >= 4

    def test_bad_registry_shape_raises(self, tmp_path):
        bad = tmp_path / "registry.json"
        bad.write_text(json.dumps({"instruments": [{"name_only": "x"}]}), encoding="utf-8")
        with pytest.raises(ValueError, match="needs an 'id'"):
            runner.load_arena_registry(bad)


def _export_stl(mesh: trimesh.Trimesh, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path.as_posix())
    return path


def _context(tmp_path: Path, mesh: trimesh.Trimesh) -> ObjectiveContext:
    stl_path = _export_stl(mesh, tmp_path / "output.stl")
    png_path = tmp_path / "preview.png"
    png_path.write_bytes(b"\x89PNG\r\n")
    return ObjectiveContext(
        trial_id="t",
        model_id="m",
        instrument_id="i",
        seed=0,
        scad_path=tmp_path / "input.scad",
        artifacts=RenderArtifacts(stl_path=stl_path, png_path=png_path),
    )


class TestMeshObjectiveGate:
    def test_solid_box_passes_single_body_spec(self, tmp_path):
        gate = runner.mesh_objective_gate(
            {"id": "x", "envelope_mm": [100, 100, 100], "min_bodies": 1}
        )
        result = gate(_context(tmp_path, trimesh.creation.box(extents=[30, 30, 30])))
        assert result["sub_scores"]["watertight"] == 1.0
        assert result["sub_scores"]["nonzero_volume"] == 1.0
        assert result["sub_scores"]["fits_envelope"] == 1.0
        assert result["sub_scores"]["min_wall"] == 1.0
        assert result["sub_scores"]["body_count"] == 1.0
        assert result["passed"] is True
        assert result["metrics"]["body_count"] == 1

    def test_single_body_fails_assembly_min_bodies(self, tmp_path):
        gate = runner.mesh_objective_gate(
            {"id": "kora", "envelope_mm": [1500, 700, 700], "min_bodies": 4}
        )
        result = gate(_context(tmp_path, trimesh.creation.box(extents=[50, 50, 50])))
        assert result["sub_scores"]["body_count"] == 0.0
        assert result["passed"] is False

    def test_multi_body_counts_disjoint_parts(self, tmp_path):
        a = trimesh.creation.box(extents=[20, 20, 20])
        b = trimesh.creation.box(extents=[20, 20, 20])
        b.apply_translation([100, 0, 0])
        combined = trimesh.util.concatenate([a, b])
        gate = runner.mesh_objective_gate(
            {"id": "x", "envelope_mm": [300, 100, 100], "min_bodies": 2}
        )
        result = gate(_context(tmp_path, combined))
        assert result["metrics"]["body_count"] == 2
        assert result["sub_scores"]["body_count"] == 1.0

    def test_oversized_part_fails_envelope(self, tmp_path):
        gate = runner.mesh_objective_gate(
            {"id": "x", "envelope_mm": [10, 10, 10], "min_bodies": 1}
        )
        result = gate(_context(tmp_path, trimesh.creation.box(extents=[100, 100, 100])))
        assert result["sub_scores"]["fits_envelope"] == 0.0


def _fake_compiler(tmp_root: Path):
    """Compiler stand-in: exports a real STL box, fake PNG — no OpenSCAD needed."""

    def compiler(scad_path: Path, out_dir: Path) -> RenderArtifacts:
        out_dir.mkdir(parents=True, exist_ok=True)
        stl = _export_stl(trimesh.creation.box(extents=[30, 20, 10]), out_dir / "output.stl")
        png = out_dir / "preview.png"
        png.write_bytes(b"\x89PNG\r\n")
        return RenderArtifacts(stl_path=stl, png_path=png)

    return compiler


TINY_REGISTRY = {
    "instruments": [
        {
            "id": "boxolin",
            "task_brief": "a box instrument",
            "envelope_mm": [100, 100, 100],
            "min_bodies": 1,
        }
    ]
}


class TestExecuteTrialEndToEnd:
    def test_stub_run_orchestration_scores_all_trials(self, tmp_path):
        config = OrchestrationConfig(
            instrument_ids=("boxolin",),
            model_ids=("stub-a", "stub-b"),
            seeds=(0,),
            reps=1,
        )
        generators = {mid: make_stub_generator() for mid in config.model_ids}
        execute = runner.make_execute_trial(
            registry=TINY_REGISTRY,
            run_dir=tmp_path,
            generators=generators,
            compiler=_fake_compiler(tmp_path),
        )
        log = run_orchestration(
            config=config,
            run_log_path=tmp_path / "run_log.json",
            execute_trial=execute,
        )
        assert log["summary"]["counts"] == {"scored": 2}
        rows = runner.collect_objective_scoreline(log)
        assert [row["entrant"] for row in rows] == ["stub-a", "stub-b"]
        assert all(row["n_objective_trials"] == 1 for row in rows)
        # generated artifacts landed under per-trial dirs
        gen_dirs = list((tmp_path / "gen").iterdir())
        assert len(gen_dirs) == 2

    def test_generation_failure_becomes_resumable_error_row(self, tmp_path):
        def broken_generator(request):
            raise RuntimeError("CLI melted")

        config = OrchestrationConfig(
            instrument_ids=("boxolin",),
            model_ids=("stub-a",),
            seeds=(0,),
            max_attempts=2,
        )
        execute = runner.make_execute_trial(
            registry=TINY_REGISTRY,
            run_dir=tmp_path,
            generators={"stub-a": broken_generator},
            compiler=_fake_compiler(tmp_path),
        )
        log = run_orchestration(
            config=config,
            run_log_path=tmp_path / "run_log.json",
            execute_trial=execute,
        )
        entry = log["trials"][0]
        assert entry["status"] == "error"
        assert entry["attempts"] == 1
        # Error rows retry on resume until max_attempts is exhausted.
        log = run_orchestration(
            config=config,
            run_log_path=tmp_path / "run_log.json",
            execute_trial=execute,
        )
        entry = log["trials"][0]
        assert entry["status"] == "error"
        assert entry["attempts"] == 2
        rows = runner.collect_objective_scoreline(log)
        assert rows[0]["objective_pass_rate"] == 0.0

    def test_missing_generator_raises_in_executor(self, tmp_path):
        execute = runner.make_execute_trial(
            registry=TINY_REGISTRY, run_dir=tmp_path, generators={}
        )
        from makerbench.code_cad_orchestrator import ArenaTrial

        trial = ArenaTrial(
            trial_id="t", instrument_id="boxolin", model_id="ghost", seed=0, rep=0,
            provider="ghost",
        )
        with pytest.raises(RuntimeError, match="no generator"):
            execute(trial)


class TestVoteJoinAndAgreement:
    def _revealed_votes_file(self, tmp_path: Path) -> Path:
        left = VoteCandidate(
            candidate_id="c1", model_id="stub-a", trial_id="t1", render_path="a.png"
        )
        right = VoteCandidate(
            candidate_id="c2", model_id="stub-b", trial_id="t2", render_path="b.png"
        )
        pair = build_blind_pair(left, right, pair_seed="cell:0")
        vote = record_vote(pair, winner="left", voter_id="tony")
        revealed = reveal_vote(pair, vote)
        revealed["instrument_id"] = "boxolin"
        revealed["seed"] = 0
        path = tmp_path / "votes.revealed.jsonl"
        append_vote_record(path, revealed)
        return path

    def test_votes_to_elo_votes_joins_model_ids(self, tmp_path):
        path = self._revealed_votes_file(tmp_path)
        votes = runner.votes_to_elo_votes(path)
        assert len(votes) == 1
        assert {votes[0].left, votes[0].right} == {"stub-a", "stub-b"}
        assert votes[0].winner in {"left", "right", "draw"}
        assert votes[0].instrument_id == "boxolin"
        assert votes[0].voter_id == "tony"

    def test_missing_votes_file_returns_empty(self, tmp_path):
        assert runner.votes_to_elo_votes(tmp_path / "nope.jsonl") == []

    def test_elo_and_agreement_pipeline(self, tmp_path):
        votes = runner.votes_to_elo_votes(self._revealed_votes_file(tmp_path))
        elo = build_elo_leaderboard(votes, entrants=["stub-a", "stub-b"])
        assert elo["votes"] == 1
        scoreline = [
            {"entrant": "stub-a", "objective_pass_rate": 1.0, "n_objective_trials": 2},
            {"entrant": "stub-b", "objective_pass_rate": 0.5, "n_objective_trials": 2},
        ]
        rows = runner.build_agreement_rows(elo, scoreline)
        assert {row["entrant"] for row in rows} == {"stub-a", "stub-b"}
        summary = build_agreement_summary(rows)
        assert summary["agreement"]["n"] == 2

    def test_build_vote_candidates_skips_unrendered(self, tmp_path):
        png = tmp_path / "p.png"
        png.write_bytes(b"\x89PNG\r\n")
        log = {
            "trials": [
                {
                    "trial_id": "t1", "model_id": "stub-a", "instrument_id": "boxolin",
                    "seed": 0, "rep": 0, "status": "scored",
                    "result": {"render_ok": True, "artifacts": {"png_path": png.as_posix()}},
                },
                {
                    "trial_id": "t2", "model_id": "stub-b", "instrument_id": "boxolin",
                    "seed": 0, "rep": 0, "status": "auto_fail",
                    "result": {"render_ok": False, "artifacts": {"png_path": None}},
                },
            ]
        }
        cells = runner.build_vote_candidates(log)
        assert list(cells) == [("boxolin", 0, 0)]
        assert [c.model_id for c in cells[("boxolin", 0, 0)]] == ["stub-a"]


class TestPerInstrumentMinWall:
    def test_min_wall_mm_override_is_honored(self, tmp_path):
        # A solid 30mm cube has no thin features; a 50mm floor must fail it
        # while a 0.5mm floor passes — proving the spec override reaches the
        # gate (#595).
        strict = runner.mesh_objective_gate(
            {"id": "x", "envelope_mm": [100, 100, 100], "min_bodies": 1, "min_wall_mm": 50.0}
        )
        lenient = runner.mesh_objective_gate(
            {"id": "x", "envelope_mm": [100, 100, 100], "min_bodies": 1, "min_wall_mm": 0.5}
        )
        box = trimesh.creation.box(extents=[30, 30, 30])
        strict_result = strict(_context(tmp_path, box))
        lenient_result = lenient(_context(tmp_path / "b", box))
        assert strict_result["sub_scores"]["min_wall"] == 0.0
        assert lenient_result["sub_scores"]["min_wall"] == 1.0
        assert strict_result["metrics"]["min_wall_floor_mm"] == 50.0

    def test_missing_min_wall_mm_falls_back_to_default(self, tmp_path):
        gate = runner.mesh_objective_gate(
            {"id": "x", "envelope_mm": [100, 100, 100], "min_bodies": 1}
        )
        result = gate(_context(tmp_path, trimesh.creation.box(extents=[30, 30, 30])))
        assert result["metrics"]["min_wall_floor_mm"] == runner.MIN_WALL_FLOOR_MM


class TestAssemblyModuleFallback:
    ASSEMBLY_SPEC = {
        "id": "kora", "envelope_mm": [1500, 700, 700],
        "min_bodies": 4, "assembly": True,
    }

    def test_mated_assembly_passes_via_part_modules(self, tmp_path):
        # One fused connected component (a correctly mated assembly) must not
        # fail the assembly check when its part modules compile standalone
        # (#596 — Round 1 penalized the most accurate kora candidates).
        gate = runner.mesh_objective_gate(
            self.ASSEMBLY_SPEC, part_module_counter=lambda path: 5
        )
        result = gate(_context(tmp_path, trimesh.creation.box(extents=[50, 50, 50])))
        assert result["sub_scores"]["body_count"] == 1.0
        assert result["metrics"]["part_modules_compiled"] == 5

    def test_too_few_part_modules_still_fails(self, tmp_path):
        gate = runner.mesh_objective_gate(
            self.ASSEMBLY_SPEC, part_module_counter=lambda path: 2
        )
        result = gate(_context(tmp_path, trimesh.creation.box(extents=[50, 50, 50])))
        assert result["sub_scores"]["body_count"] == 0.0

    def test_disjoint_pass_skips_the_module_counter(self, tmp_path):
        boxes = [trimesh.creation.box(extents=[20, 20, 20]) for _ in range(4)]
        for index, box in enumerate(boxes):
            box.apply_translation([index * 100, 0, 0])
        combined = trimesh.util.concatenate(boxes)

        def exploding_counter(path):
            raise AssertionError("counter must not run when components pass")

        gate = runner.mesh_objective_gate(
            self.ASSEMBLY_SPEC, part_module_counter=exploding_counter
        )
        result = gate(_context(tmp_path, combined))
        assert result["sub_scores"]["body_count"] == 1.0
        assert result["metrics"]["part_modules_compiled"] is None

    def test_non_assembly_spec_never_uses_fallback(self, tmp_path):
        def exploding_counter(path):
            raise AssertionError("non-assembly specs must not consult modules")

        gate = runner.mesh_objective_gate(
            {"id": "x", "envelope_mm": [100, 100, 100], "min_bodies": 4},
            part_module_counter=exploding_counter,
        )
        result = gate(_context(tmp_path, trimesh.creation.box(extents=[30, 30, 30])))
        assert result["sub_scores"]["body_count"] == 0.0


class TestCallableModuleParsing:
    def test_zero_arg_and_defaulted_modules_qualify(self):
        source = """
module bowl() { sphere(258); }
module neck(length=1300, d=[40,40,60]) { cylinder(h=length, r=20); }
module bridge(h) { cube([10, 10, h]); }
module bowl() { sphere(1); }
"""
        names = runner._callable_module_names(source)
        assert names == ["bowl", "neck"]

    def test_count_uses_injected_face_counter(self, tmp_path):
        scad = tmp_path / "candidate.scad"
        scad.write_text(
            "module bowl() {}\nmodule neck() {}\nmodule empty() {}\n",
            encoding="utf-8",
        )
        faces = {"bowl": 12, "neck": 8, "empty": 0}
        count = runner.count_standalone_part_modules(
            scad, face_counter=lambda path, name: faces[name]
        )
        assert count == 2

    def test_missing_scad_counts_zero(self, tmp_path):
        assert runner.count_standalone_part_modules(tmp_path / "nope.scad") == 0
