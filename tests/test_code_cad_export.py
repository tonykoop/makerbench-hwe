"""Tests for arena 3D viewer assets and winner export (#602, #603)."""

from __future__ import annotations

import json
from pathlib import Path

import trimesh

from makerbench import code_cad_export as export
from makerbench.cli_arena import drop_unrated_entrants


def _write_stl(path: Path, meshes: list[trimesh.Trimesh]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    trimesh.util.concatenate(meshes).export(path.as_posix())
    return path


def _two_body_stl(path: Path) -> Path:
    a = trimesh.creation.box(extents=[20, 20, 20])
    b = trimesh.creation.box(extents=[20, 20, 20])
    b.apply_translation([100, 0, 0])
    return _write_stl(path, [a, b])


class TestStlToGlb:
    def test_bodies_become_distinctly_colored_scene_nodes(self, tmp_path):
        stl = _two_body_stl(tmp_path / "output.stl")
        glb = export.stl_to_glb(stl, tmp_path / "output.glb")
        assert glb.exists() and glb.stat().st_size > 0
        scene = trimesh.load(glb.as_posix(), force="scene")
        assert len(scene.geometry) == 2

    def test_ensure_glb_is_lazy_and_cached(self, tmp_path):
        stl = _two_body_stl(tmp_path / "output.stl")
        first = export.ensure_glb(stl)
        assert first is not None and first.suffix == ".glb"
        stamp = first.stat().st_mtime_ns
        second = export.ensure_glb(stl)
        assert second == first
        assert second.stat().st_mtime_ns == stamp  # no reconversion

    def test_ensure_glb_missing_stl_returns_none(self, tmp_path):
        assert export.ensure_glb(tmp_path / "nope.stl") is None


def _revealed(path: Path, instrument: str, left: str, right: str, winner: str) -> None:
    record = {
        "instrument_id": instrument,
        "winner": winner,
        "reveal": {"left": {"model_id": left}, "right": {"model_id": right}},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _run_log_with_trials(run_dir: Path, rates: dict[tuple[str, str, int], float]) -> dict:
    trials = []
    for (instrument, entrant, seed), rate in rates.items():
        trial_id = f"{instrument}__seed{seed}__rep0__{entrant}"
        gen_dir = run_dir / "gen" / trial_id
        render_dir = run_dir / "render" / trial_id
        gen_dir.mkdir(parents=True, exist_ok=True)
        scad = gen_dir / "candidate.scad"
        scad.write_text("cube([20,20,20]);\n", encoding="utf-8")
        stl = _two_body_stl(render_dir / "output.stl")
        png = render_dir / "preview.png"
        png.write_bytes(b"\x89PNG\r\n")
        trials.append(
            {
                "trial_id": trial_id,
                "instrument_id": instrument,
                "model_id": entrant,
                "seed": seed,
                "rep": 0,
                "status": "scored",
                "result": {
                    "objective": {"objective_pass_rate": rate},
                    "gen": {"scad_path": scad.as_posix()},
                    "artifacts": {
                        "stl_path": stl.as_posix(),
                        "png_path": png.as_posix(),
                    },
                },
            }
        )
    return {"trials": trials}


class TestWinnerSelection:
    def test_vote_wins_beat_objective_rate(self, tmp_path):
        votes = tmp_path / "votes.revealed.jsonl"
        _revealed(votes, "kora", "agy", "codex", "left")
        _revealed(votes, "kora", "agy", "opus", "left")
        run_log = _run_log_with_trials(
            tmp_path,
            {
                ("kora", "agy", 0): 0.5,
                ("kora", "codex", 0): 0.9,
                ("kora", "opus", 0): 0.9,
            },
        )
        winners = export.pick_winners(run_log, votes)
        assert winners["kora"]["entrant"] == "agy"
        assert winners["kora"]["vote_wins"] == 2.0

    def test_no_votes_falls_back_to_objective(self, tmp_path):
        run_log = _run_log_with_trials(
            tmp_path,
            {("kena", "agy", 0): 0.5, ("kena", "opus", 0): 0.9},
        )
        winners = export.pick_winners(run_log, tmp_path / "none.jsonl")
        assert winners["kena"]["entrant"] == "opus"

    def test_draws_count_half(self, tmp_path):
        votes = tmp_path / "votes.revealed.jsonl"
        _revealed(votes, "kora", "agy", "codex", "draw")
        tallies = export.instrument_vote_tallies(votes)
        assert tallies["kora"] == {"agy": 0.5, "codex": 0.5}


class TestExportWinners:
    REGISTRY = {
        "instruments": [
            {"id": "kora", "repo_path": "strings/kora"},
            {"id": "kena", "repo_path": ""},
        ]
    }

    def _exported(self, tmp_path, force=False):
        run_dir = tmp_path / "runs" / "round9"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_log = _run_log_with_trials(
            run_dir, {("kora", "agy", 0): 0.5, ("kora", "codex", 0): 0.9}
        )
        votes = run_dir / "votes.revealed.jsonl"
        _revealed(votes, "kora", "agy", "codex", "left")
        instruments_root = tmp_path / "instruments"
        (instruments_root / "strings" / "kora").mkdir(parents=True, exist_ok=True)
        summary = export.export_winners(
            run_log=run_log,
            run_dir=run_dir,
            registry=self.REGISTRY,
            instruments_root=instruments_root,
            force=force,
        )
        return summary, instruments_root

    def test_winner_lands_in_instrument_repo_with_provenance(self, tmp_path):
        summary, root = self._exported(tmp_path)
        row = next(r for r in summary if r["instrument_id"] == "kora")
        assert row["status"] == "exported"
        dest = root / "strings" / "kora" / "arena" / "round9"
        assert (dest / "kora-arena-winner.scad").exists()
        assert (dest / "kora-arena-winner.stl").exists()
        assert (dest / "kora-arena-winner.glb").exists()
        provenance = json.loads((dest / "provenance.json").read_text(encoding="utf-8"))
        assert provenance["entrant"] == "agy"
        assert provenance["generated"] is True
        readme = (dest / "README.md").read_text(encoding="utf-8")
        assert "NOT a measured master" in readme

    def test_existing_export_is_not_clobbered_without_force(self, tmp_path):
        self._exported(tmp_path)
        run_dir = tmp_path / "runs" / "round9"
        run_log = _run_log_with_trials(
            run_dir, {("kora", "agy", 0): 0.5, ("kora", "codex", 0): 0.9}
        )
        summary = export.export_winners(
            run_log=run_log,
            run_dir=run_dir,
            registry=self.REGISTRY,
            instruments_root=tmp_path / "instruments",
            force=False,
        )
        row = next(r for r in summary if r["instrument_id"] == "kora")
        assert row["status"].startswith("exists")

    def test_missing_repo_path_is_reported_not_fatal(self, tmp_path):
        run_dir = tmp_path / "runs" / "round9"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_log = _run_log_with_trials(run_dir, {("kena", "agy", 0): 0.5})
        summary = export.export_winners(
            run_log=run_log,
            run_dir=run_dir,
            registry=self.REGISTRY,
            instruments_root=tmp_path / "instruments",
        )
        row = next(r for r in summary if r["instrument_id"] == "kena")
        assert "repo_path" in row["status"]


class TestDropUnratedEntrants:
    def test_ghost_rows_move_to_unrated_and_ranks_stay_contiguous(self):
        payload = {
            "leaderboard": [
                {"rank": 1, "entrant": "agy", "rating": 1560.0, "games": 8},
                {"rank": 2, "entrant": "gemini-cli", "rating": 1500.0, "games": 0},
                {"rank": 3, "entrant": "haiku", "rating": 1440.0, "games": 8},
            ]
        }
        result = drop_unrated_entrants(payload)
        assert [row["entrant"] for row in result["leaderboard"]] == ["agy", "haiku"]
        assert [row["rank"] for row in result["leaderboard"]] == [1, 2]
        assert result["unrated_entrants"] == ["gemini-cli"]


class TestVoteWebQueue:
    def _queue(self, tmp_path):
        from makerbench.code_cad_vote_web import QueueItem, VoteQueue, render_queue_page
        from makerbench.code_cad_vote_surface import VoteCandidate, build_blind_pair

        left = VoteCandidate(candidate_id="a", model_id="m1", trial_id="t-a",
                             render_path="blind/p-left.png")
        right = VoteCandidate(candidate_id="b", model_id="m2", trial_id="t-b",
                              render_path="blind/p-right.png")
        pair = build_blind_pair(left, right, pair_seed="cell")
        queue = VoteQueue(run_dir=tmp_path, voter="tony")
        queue.items.append(QueueItem(pair=pair, meta={
            "instrument_id": "boxolin", "seed": 0, "rep": 0, "round": 0}))
        return queue, pair, render_queue_page

    def test_cast_appends_blind_and_revealed_records(self, tmp_path):
        import json
        queue, pair, _ = self._queue(tmp_path)
        assert queue.cast(pair.pair_id, "left") is True
        blind = json.loads((tmp_path / "votes.blind.jsonl").read_text().strip())
        revealed = json.loads((tmp_path / "votes.revealed.jsonl").read_text().strip())
        assert blind["winner"] == "left" and blind["voter_id"] == "tony"
        assert "model_id" not in json.dumps(blind.get("left"))
        assert revealed["reveal"]["left"]["model_id"] in {"m1", "m2"}
        assert revealed["instrument_id"] == "boxolin"

    def test_duplicate_and_unknown_votes_rejected(self, tmp_path):
        queue, pair, _ = self._queue(tmp_path)
        assert queue.cast(pair.pair_id, "draw") is True
        assert queue.cast(pair.pair_id, "left") is False
        assert queue.cast("pair-nope", "left") is False

    def test_queue_page_serves_pair_then_summary(self, tmp_path):
        queue, pair, render_queue_page = self._queue(tmp_path)
        page = render_queue_page(queue)
        assert "pair 1 of 1" in page
        assert "fetch('/vote'" in page
        assert "m1" not in page and "m2" not in page  # blindness holds
        queue.cast(pair.pair_id, "right")
        assert "All 1 pairs voted" in render_queue_page(queue)


class TestVoteFlags:
    """Per-candidate defect/disposition flags on votes (Round 4 feedback)."""

    def _queue(self, tmp_path):
        from makerbench.code_cad_vote_web import QueueItem, VoteQueue
        from makerbench.code_cad_vote_surface import VoteCandidate, build_blind_pair

        left = VoteCandidate(candidate_id="a", model_id="m1", trial_id="t-a",
                             render_path="blind/p-left.png")
        right = VoteCandidate(candidate_id="b", model_id="m2", trial_id="t-b",
                              render_path="blind/p-right.png")
        pair = build_blind_pair(left, right, pair_seed="cell")
        queue = VoteQueue(run_dir=tmp_path, voter="tony")
        queue.items.append(QueueItem(pair=pair, meta={
            "instrument_id": "boxolin", "seed": 0, "rep": 0, "round": 0}))
        return queue, pair

    def test_flags_recorded_in_blind_and_revealed(self, tmp_path):
        import json
        queue, pair = self._queue(tmp_path)
        ok = queue.cast(pair.pair_id, "left", flags={
            "left": ["insufficient_detail"],
            "right": ["missing_critical_components", "delete_immediately"],
        })
        assert ok
        blind = json.loads((tmp_path / "votes.blind.jsonl").read_text().strip())
        revealed = json.loads((tmp_path / "votes.revealed.jsonl").read_text().strip())
        for record in (blind, revealed):
            assert record["flags"]["left"] == ["insufficient_detail"]
            assert record["flags"]["right"] == [
                "missing_critical_components", "delete_immediately"]

    def test_unknown_flags_dropped_and_empty_flags_omitted(self, tmp_path):
        import json
        queue, pair = self._queue(tmp_path)
        ok = queue.cast(pair.pair_id, "draw", flags={
            "left": ["not_a_real_flag"], "right": [], "up": ["save_for_later"]})
        assert ok
        blind = json.loads((tmp_path / "votes.blind.jsonl").read_text().strip())
        assert "flags" not in blind

    def test_queue_page_renders_flag_checkboxes_per_side(self, tmp_path):
        from makerbench.code_cad_vote_web import render_queue_page, VOTE_FLAGS
        queue, pair = self._queue(tmp_path)
        page = render_queue_page(queue)
        assert 'data-flag-side="left"' in page
        assert 'data-flag-side="right"' in page
        for flag_id, label in VOTE_FLAGS:
            assert page.count(f'value="{flag_id}"') == 2
            assert label in page
