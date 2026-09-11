"""Studio service layer integrating MakerBench arena modules (Issue #696)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from makerbench import code_cad_arena_runner as arena_runner
from makerbench.cli_arena import (
    DEFAULT_REGISTRY,
    _elo_payload_for_run,
    _judge_payload_for_run,
    _load_run_log,
    _pairing_plan,
    _stage_blind_assets,
    _voted_pair_keys,
)
from makerbench.code_cad_agreement import build_agreement_summary
from makerbench.code_cad_vote_surface import (
    BlindPair,
    VoteCandidate,
    build_blind_pair,
)
from makerbench.code_cad_vote_web import QueueItem, VoteQueue


class ArenaStudioService:
    """Business logic and data provider for MakerBench Arena Studio."""

    def __init__(
        self,
        default_run_dir: Optional[Path] = None,
        registry_path: Path = Path(DEFAULT_REGISTRY),
        repo_root: Optional[Path] = None,
    ):
        self.default_run_dir = default_run_dir.resolve() if default_run_dir else None
        self.registry_path = registry_path.resolve()
        self.repo_root = repo_root.resolve() if repo_root else Path.cwd().resolve()
        self._queues: dict[tuple[str, str], VoteQueue] = {}

    def get_default_run_dir(self) -> Optional[Path]:
        if self.default_run_dir and self.default_run_dir.exists():
            return self.default_run_dir
        # Auto-discover first candidate
        runs = self.discover_runs()
        if runs:
            return Path(runs[0]["path"])
        return None

    def discover_runs(self) -> list[dict[str, Any]]:
        """Search workspace directories for arena runs with a run_log.json."""
        discovered: list[dict[str, Any]] = []
        search_roots = [
            self.repo_root / "runs" / "code_cad_arena",
            self.repo_root / "arena_gen",
            Path("/home/tony/bench-wt/arena_gen"),
        ]
        if self.default_run_dir:
            search_roots.insert(0, self.default_run_dir.parent)

        visited_paths: set[str] = set()

        for root in search_roots:
            if not root.exists():
                continue
            for log_file in root.rglob("run_log.json"):
                run_path = log_file.parent.resolve()
                path_str = str(run_path)
                if path_str in visited_paths:
                    continue
                visited_paths.add(path_str)

                summary = self._summarize_run_dir(run_path)
                discovered.append(summary)

        discovered.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return discovered

    def _summarize_run_dir(self, run_path: Path) -> dict[str, Any]:
        """Produce lightweight metadata for a single run directory."""
        log_path = run_path / "run_log.json"
        created_at = None
        models: list[str] = []
        instruments: list[str] = []
        trials_count = 0

        if log_path.exists():
            try:
                data = json.loads(log_path.read_text(encoding="utf-8"))
                created_at = data.get("started_at") or data.get("created_at")
                cfg = data.get("config") or {}
                models = cfg.get("model_ids") or []
                instruments = cfg.get("instruments") or []
                trials_count = len(data.get("trials") or [])
            except Exception:
                pass

        # Check votes
        blind_votes = 0
        blind_file = run_path / "votes.blind.jsonl"
        if blind_file.exists():
            try:
                blind_votes = sum(
                    1 for line in blind_file.read_text(encoding="utf-8").splitlines() if line.strip()
                )
            except Exception:
                pass

        return {
            "run_id": run_path.name,
            "path": str(run_path),
            "created_at": created_at,
            "models": models,
            "instruments": instruments,
            "trials_count": trials_count,
            "votes_count": blind_votes,
            "has_votes": blind_votes > 0,
        }

    def get_run_summary(self, run_dir: Path) -> dict[str, Any]:
        """Detailed run statistics."""
        run_log = _load_run_log(run_dir)
        cfg = run_log.get("config") or {}
        trials = run_log.get("trials") or []

        # Count passes
        compiled = sum(1 for t in trials if (t.get("grade") or {}).get("compiled"))
        manifold = sum(1 for t in trials if (t.get("grade") or {}).get("manifold"))

        summary = self._summarize_run_dir(run_dir)
        summary.update({
            "config": cfg,
            "compiled_count": compiled,
            "manifold_count": manifold,
            "trials": trials,
        })
        return summary

    def get_run_leaderboard(self, run_dir: Path) -> dict[str, Any]:
        """Build the live Elo leaderboard for the run."""
        run_log = _load_run_log(run_dir)
        return _elo_payload_for_run(run_dir, run_log)

    def get_run_agreement(self, run_dir: Path) -> dict[str, Any]:
        """Calculate subjective Elo x objective pass-rate agreement."""
        run_log = _load_run_log(run_dir)
        elo_payload = _elo_payload_for_run(run_dir, run_log)
        scoreline = arena_runner.collect_objective_scoreline(run_log)
        judge_path = run_dir / "votes.judge.jsonl"
        judge_payload = _judge_payload_for_run(run_dir, run_log) if judge_path.exists() else None

        rows = arena_runner.build_agreement_rows(elo_payload, scoreline, judge_payload)
        summary = build_agreement_summary(rows)
        return summary

    def get_registry_tasks(self, family: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieve instrument definitions from registry."""
        if not self.registry_path.exists():
            return []
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            tasks = data.get("instruments") or []
            if family:
                tasks = [t for t in tasks if t.get("family") == family]
            return tasks
        except Exception:
            return []

    def get_or_create_queue(
        self,
        run_dir: Path,
        voter: str = "tony",
        rounds: tuple[int, ...] = (0, 1),
    ) -> VoteQueue:
        """Get or build the Swiss pairing queue for a voter."""
        key = (str(run_dir.resolve()), voter)
        if key in self._queues:
            return self._queues[key]

        run_path = run_dir.resolve()
        run_log = _load_run_log(run_path)
        vote_pages = run_path / "vote_pages"
        vote_pages.mkdir(parents=True, exist_ok=True)
        already = _voted_pair_keys(run_path, voter)
        queue = VoteQueue(run_dir=run_path, voter=voter)

        for round_index in rounds:
            for item in _pairing_plan(run_path, run_log, round_index):
                cand_a, cand_b = item["candidates"]
                pair_seed = (
                    f"{item['instrument_id']}:seed{item['seed']}:rep{item['rep']}:round{item['round']}"
                )
                shuffled = build_blind_pair(cand_a, cand_b, pair_seed=pair_seed)
                if (shuffled.pair_id, voter) in already:
                    continue

                def _root_relative(candidate: VoteCandidate) -> VoteCandidate:
                    return VoteCandidate(
                        candidate_id=candidate.candidate_id,
                        model_id=candidate.model_id,
                        trial_id=candidate.trial_id,
                        render_path=f"/runs/{run_path.name}/vote_pages/{candidate.render_path}",
                        provenance=candidate.provenance,
                        model3d_path=(
                            f"/runs/{run_path.name}/vote_pages/{candidate.model3d_path}"
                            if candidate.model3d_path
                            else None
                        ),
                        frames=(
                            tuple(
                                f"/runs/{run_path.name}/vote_pages/{p}" for p in candidate.frames
                            )
                            if candidate.frames
                            else None
                        ),
                    )

                pair = BlindPair(
                    pair_id=shuffled.pair_id,
                    left=_root_relative(
                        _stage_blind_assets(shuffled.left, shuffled.pair_id, "left", vote_pages)
                    ),
                    right=_root_relative(
                        _stage_blind_assets(shuffled.right, shuffled.pair_id, "right", vote_pages)
                    ),
                )
                queue.items.append(
                    QueueItem(
                        pair=pair,
                        meta={
                            "instrument_id": item["instrument_id"],
                            "seed": item["seed"],
                            "rep": item["rep"],
                            "round": item["round"],
                        },
                    )
                )

        self._queues[key] = queue
        return queue

    def cast_vote(
        self,
        run_dir: Path,
        pair_id: str,
        winner: str,
        voter: str = "tony",
        flags: Optional[dict] = None,
    ) -> bool:
        """Submit a vote on a specific pair."""
        queue = self.get_or_create_queue(run_dir, voter=voter)
        success = queue.cast(pair_id=pair_id, winner=winner, flags=flags)
        return success
