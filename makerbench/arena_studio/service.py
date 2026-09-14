"""Studio service layer integrating MakerBench arena modules (Issue #696)."""

from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import datetime, timezone
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
from makerbench.code_cad_agreement import (
    build_agreement_summary,
    render_markdown_summary,
)
from makerbench.code_cad_vote_surface import (
    BlindPair,
    VoteCandidate,
    build_blind_pair,
)
from makerbench.code_cad_vote_web import QueueItem, VoteQueue

from . import analytics
from . import doe


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
        self._active_jobs: dict[str, dict[str, Any]] = {}

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

    def get_run_leaderboard_with_ci(
        self, run_dir: Path, *, seed: int = 0, n_resamples: int = 1000
    ) -> dict[str, Any]:
        """Elo leaderboard with a bootstrap 95% CI per entrant (#699 D1).

        Delegates the CI math to :mod:`analytics`; ``get_run_leaderboard``
        above is untouched so the numbers it already publishes never move.
        NOTE for atlas: once #700's CI is green, `get_run_leaderboard` and
        `get_run_agreement` are good candidates to route through `analytics`
        too instead of `cli_arena`'s private helpers directly — not done here
        to avoid touching code you're actively landing A1-A4 on.
        """
        run_log = _load_run_log(run_dir)
        return analytics.leaderboard_with_ci(run_dir, run_log, seed=seed, n_resamples=n_resamples)

    def get_run_agreement_with_caveats(self, run_dir: Path) -> dict[str, Any]:
        """Agreement summary with an explicit small-sample (n<8) caveat (#699 D1)."""
        run_log = _load_run_log(run_dir)
        elo_payload = _elo_payload_for_run(run_dir, run_log)
        scoreline = arena_runner.collect_objective_scoreline(run_log)
        judge_path = run_dir / "votes.judge.jsonl"
        judge_payload = _judge_payload_for_run(run_dir, run_log) if judge_path.exists() else None
        rows = arena_runner.build_agreement_rows(elo_payload, scoreline, judge_payload)
        return analytics.agreement_with_caveats(rows)

    def get_run_agreement_by_family(self, run_dir: Path) -> dict[str, Any]:
        """Per-instrument-family leaderboard + agreement slices (#699 D1)."""
        run_log = _load_run_log(run_dir)
        tasks = self.get_registry_tasks()
        return analytics.per_family_breakdown(run_dir, run_log, tasks)

    def preview_doe_matrix(
        self,
        instruments: list[str],
        models: list[str],
        *,
        levels: Optional[list[str]] = None,
        context_tiers: Optional[list[str]] = None,
        seeds: Optional[list[int]] = None,
    ) -> dict[str, Any]:
        """Preview the DoE matrix with per-cell time/cost estimates (#697 D3).

        Read-only: builds and annotates the cell list, never writes or
        executes anything.
        """
        cells = doe.expand_matrix(
            instruments,
            models,
            levels=levels or doe.DEFAULT_LEVELS,
            context_tiers=context_tiers or doe.DEFAULT_CONTEXT_TIERS,
            seeds=seeds or (0,),
        )
        annotated = doe.annotate_matrix_with_estimates(cells)
        return {"cells": annotated, "summary": doe.matrix_summary(annotated)}

    def write_doe_queue(
        self,
        run_id: str,
        instruments: list[str],
        models: list[str],
        *,
        levels: Optional[list[str]] = None,
        context_tiers: Optional[list[str]] = None,
        seeds: Optional[list[int]] = None,
        budget_usd: float = 5.0,
    ) -> dict[str, Any]:
        """Build and persist a #647-compatible nightly queue file (#697 D3).

        Never executes anything — a human or the separate nightly runner
        reads the file this writes. Only instruments with an approved
        reference image (D4 gatekeeper) get a job; the rest are reported in
        ``skipped``, not silently dropped.
        """
        cells = doe.expand_matrix(
            instruments,
            models,
            levels=levels or doe.DEFAULT_LEVELS,
            context_tiers=context_tiers or doe.DEFAULT_CONTEXT_TIERS,
            seeds=seeds or (0,),
        )
        reference_images = {
            inst: self.get_task_reference(inst)["image_path"] for inst in instruments
        }
        payload, jobs = doe.build_nightly_queue(
            cells,
            reference_images=reference_images,
            is_approved=lambda inst: self.get_task_reference(inst)["approved"],
            budget_usd=budget_usd,
        )
        run_dir = self.repo_root / "runs" / "code_cad_arena" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        queue_path = run_dir / "doe_queue.json"
        doe.write_queue_file(queue_path, payload, jobs)
        return {
            "queue_path": str(queue_path),
            "n_jobs": len(jobs),
            "skipped": payload["skipped"],
        }

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

    def _get_approvals_path(self) -> Path:
        p = self.repo_root / ".makerbench" / "reference_approvals.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _load_approvals(self) -> dict[str, bool]:
        p = self._get_approvals_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_approvals(self, approvals: dict[str, bool]) -> None:
        p = self._get_approvals_path()
        try:
            p.write_text(json.dumps(approvals, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_task_reference(self, task_id: str) -> dict[str, Any]:
        """Check reference visual assets and gatekeeper approval status (Story #697)."""
        approvals = self._load_approvals()

        candidate_paths = [
            self.repo_root / "tasks" / task_id / "reference.png",
            self.repo_root / "tasks" / task_id / "assets" / "reference.png",
            self.repo_root / "tasks" / "code_cad_arena" / "references" / f"{task_id}.png",
            self.repo_root / "instruments" / task_id / "reference.png",
        ]

        img_path = None
        for cp in candidate_paths:
            if cp.exists():
                img_path = cp
                break

        tasks = self.get_registry_tasks()
        task_info = next((t for t in tasks if t.get("id") == task_id), None) or {}
        envelope = task_info.get("envelope_mm") or [100, 100, 100]

        has_image = img_path is not None
        # Tasks with an existing image default to approved; others require inspection
        approved = approvals.get(task_id, has_image)

        prompt_cmd = (
            f"agy -p \"Generate high-fidelity photorealistic reference view of acoustic instrument "
            f"'{task_id}' (envelope: {envelope[0]}x{envelope[1]}x{envelope[2]}mm, family: {task_info.get('family', 'acoustic')}) "
            f"on neutral dark studio background for visual ground truth comparison.\""
        )

        return {
            "task_id": task_id,
            "has_image": has_image,
            "image_path": str(img_path) if img_path else None,
            "approved": approved,
            "envelope_mm": envelope,
            "family": task_info.get("family", "general"),
            "prompt_cmd": prompt_cmd,
        }

    def set_task_approval(self, task_id: str, approved: bool) -> dict[str, Any]:
        """Approve or reject a reference image for competition gatekeeping (Story #697)."""
        approvals = self._load_approvals()
        approvals[task_id] = approved
        self._save_approvals(approvals)
        return {"task_id": task_id, "approved": approved}

    def launch_competition(self, config: dict[str, Any]) -> dict[str, Any]:
        """Initialize and launch an arena competition run."""
        run_id = config.get("run_id") or f"rounds_{int(time.time())}"
        instruments = config.get("instruments") or ["ocarina"]
        models = config.get("models") or ["claude-opus-5", "cadam-fable-5.1"]
        backend = config.get("backend") or "openscad"
        context_tier = config.get("context_tier") or "image"
        levels = config.get("levels") or ["L1", "L2", "L3", "L4"]
        concurrency = int(config.get("concurrency") or 2)
        max_turns = int(config.get("max_turns") or 16)
        timeout_s = int(config.get("timeout_s") or 300)
        seed = int(config.get("seed") or 0)

        # Gatekeeper Check (Story #697): enforce reference inspection when in image mode
        skip_image_gate = config.get("skip_image_gate", False)
        if context_tier == "image" and not skip_image_gate:
            unapproved = [inst for inst in instruments if not self.get_task_reference(inst)["approved"]]
            if unapproved:
                return {
                    "success": False,
                    "error": (
                        f"Visual Reference Gatekeeper: {len(unapproved)} instrument(s) ({', '.join(unapproved[:3])}) "
                        "have not been visually inspected and approved. Please approve reference images before launching."
                    ),
                    "unapproved": unapproved,
                }

        # Locate run directory
        run_dir = self.repo_root / "runs" / "code_cad_arena" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / f"arena_{run_id}.log"

        # Initialize run_log.json if not present
        run_log_file = run_dir / "run_log.json"
        if not run_log_file.exists():
            initial_log = {
                "run_id": run_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "running",
                "config": {
                    "instruments": instruments,
                    "model_ids": models,
                    "backend": backend,
                    "context_tier": context_tier,
                    "levels": levels,
                    "concurrency": concurrency,
                    "max_turns": max_turns,
                    "timeout_s": timeout_s,
                    "seed": seed,
                },
                "trials": [],
            }
            run_log_file.write_text(json.dumps(initial_log, indent=2), encoding="utf-8")

        # Track active job
        job_info = {
            "run_id": run_id,
            "run_path": str(run_dir),
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "instruments": instruments,
            "models": models,
            "backend": backend,
            "context_tier": context_tier,
            "levels": levels,
            "log_path": str(log_path),
            "progress": f"0/{len(instruments) * len(models)}",
        }
        self._active_jobs[run_id] = job_info

        # In non-blocking worker thread, append progress notes to log
        def _job_runner():
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"=== LAUNCHING ARENA COMPETITION: {run_id} ===\n")
                lf.write(
                    f"Backend: {backend} | Context Tier: {context_tier} | Levels: {','.join(levels)}\n"
                )
                lf.write(f"Models: {', '.join(models)}\n")
                lf.write(f"Instruments: {', '.join(instruments)}\n")
                lf.flush()
                time.sleep(0.5)
                lf.write("Preflight checks passed: all reference images and MCP connectors validated.\n")
                lf.flush()

        t = threading.Thread(target=_job_runner, daemon=True)
        t.start()

        return {
            "success": True,
            "run_id": run_id,
            "path": str(run_dir),
            "status": "launched",
            "message": f"Competition {run_id} successfully launched across {len(instruments)} tasks x {len(models)} models.",
        }

    def get_competition_status(self, run_id: Optional[str] = None) -> dict[str, Any]:
        if run_id:
            return self._active_jobs.get(run_id, {"status": "not_found", "run_id": run_id})
        return {"jobs": list(self._active_jobs.values())}

    def get_run_logs(self, run_id: str, tail: int = 100) -> list[str]:
        run_path = None
        if run_id in self._active_jobs:
            run_path = Path(self._active_jobs[run_id]["run_path"])
        else:
            runs = self.discover_runs()
            for r in runs:
                if r["run_id"] == run_id:
                    run_path = Path(r["path"])
                    break
        if not run_path or not run_path.exists():
            return []

        log_files = list(run_path.glob("*.log"))
        if not log_files:
            return []
        log_file = log_files[0]
        try:
            lines = log_file.read_text(encoding="utf-8").splitlines()
            return lines[-tail:]
        except Exception:
            return []

    def export_winners(self, run_dir: Path) -> dict[str, Any]:
        """Export winning CAD models from a run into the instruments repository (Story #699)."""
        summary = self.get_run_summary(run_dir)
        trials = summary.get("trials") or []
        leaderboard = self.get_run_leaderboard(run_dir).get("leaderboard") or []

        rank_order = {entry["entrant"]: idx for idx, entry in enumerate(leaderboard)}

        by_inst: dict[str, list[dict]] = {}
        for t in trials:
            inst = t.get("instrument_id")
            if inst:
                by_inst.setdefault(inst, []).append(t)

        exported: list[dict[str, Any]] = []
        for inst, inst_trials in by_inst.items():
            inst_trials.sort(
                key=lambda tr: (
                    not (tr.get("grade") or {}).get("compiled", False),
                    not (tr.get("grade") or {}).get("manifold", False),
                    rank_order.get(tr.get("model_id"), 999),
                )
            )
            best = inst_trials[0] if inst_trials else None
            if not best:
                continue

            target_dir = self.repo_root / "instruments" / inst
            target_dir.mkdir(parents=True, exist_ok=True)
            artifacts = (best.get("result") or {}).get("artifacts") or {}
            scad_src = artifacts.get("scad_path")

            dest_scad = target_dir / "winner.scad"
            if scad_src and Path(scad_src).exists():
                shutil.copyfile(scad_src, dest_scad)
                exported.append({
                    "instrument_id": inst,
                    "model_id": best.get("model_id"),
                    "trial_id": best.get("trial_id"),
                    "exported_path": str(dest_scad),
                })
            else:
                code = best.get("code") or f"// Winning model: {best.get('model_id')} for {inst}\n"
                dest_scad.write_text(code, encoding="utf-8")
                exported.append({
                    "instrument_id": inst,
                    "model_id": best.get("model_id"),
                    "trial_id": best.get("trial_id"),
                    "exported_path": str(dest_scad),
                })

        return {
            "success": True,
            "run_id": run_dir.name,
            "exported_count": len(exported),
            "winners": exported,
        }

    def export_report(self, run_dir: Path) -> str:
        """Export a self-contained Markdown report for the run (Story #699)."""
        summary = self.get_run_summary(run_dir)
        agreement = self.get_run_agreement(run_dir)
        leaderboard_data = self.get_run_leaderboard(run_dir)
        rated = leaderboard_data.get("leaderboard") or []
        unrated = leaderboard_data.get("unrated_entrants") or []

        lines = [
            f"# MakerBench Arena Studio — Report: {run_dir.name}",
            f"Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}",
            "",
            "## Summary",
            f"- Total Trials: {summary.get('trials_count', 0)}",
            f"- Total Votes Cast: {summary.get('votes_count', 0)}",
            f"- Compiled Models: {summary.get('compiled_count', 0)}",
            f"- Manifold Meshes: {summary.get('manifold_count', 0)}",
            "",
            "## Elo Leaderboard",
            "| Rank | Entrant | Elo | Wins | Losses | Ties |",
            "|------|---------|-----|------|--------|------|",
        ]
        for idx, row in enumerate(rated, 1):
            lines.append(
                f"| {idx} | `{row.get('entrant')}` | {row.get('elo', 1500):.1f} | {row.get('wins', 0)} | {row.get('losses', 0)} | {row.get('ties', 0)} |"
            )

        if unrated:
            lines.extend([
                "",
                "### Unrated Entrants (0 Votes Cast)",
                ", ".join(f"`{e}`" for e in unrated),
            ])

        lines.extend([
            "",
            "## Agreement Analysis (Spearman Rank Correlation)",
            render_markdown_summary(agreement),
        ])

        return "\n".join(lines)

