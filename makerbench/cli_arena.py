"""``makerbench arena`` — CLI wiring for the Code-CAD Arena (Epic #421).

Runs the 4D DoE matrix (instruments x seeds x reps x models), collects the
objective scoreline, drives blind voting rounds, and emits the Elo leaderboard
plus the dual-scoreline agreement report. All artifacts live under a
gitignored run directory (``runs/code_cad_arena/<run_id>/`` by convention).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import code_cad_providers as providers
from . import code_cad_arena_runner as arena_runner
from . import render
from .code_cad_agreement import build_agreement_summary, render_markdown_summary
from .code_cad_arena import build_elo_leaderboard, sample_swiss_pairs
from .code_cad_orchestrator import OrchestrationConfig, run_orchestration
from .code_cad_vote_surface import (
    VoteCandidate,
    append_vote_record,
    build_blind_pair,
    record_vote,
    render_vote_surface,
    reveal_vote,
)


arena_app = typer.Typer(
    add_completion=False,
    help="Code-CAD A/B Arena: DoE runs, blind votes, Elo, agreement (Epic #421).",
)
console = Console(width=140)

DEFAULT_REGISTRY = "tasks/code_cad_arena/registry.json"


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _load_model_map(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_run_log(run_dir: Path) -> dict:
    log_path = run_dir / "run_log.json"
    if not log_path.exists():
        raise typer.BadParameter(f"no run log at {log_path}; run `arena run` first")
    return json.loads(log_path.read_text(encoding="utf-8"))


def _windows_link(path: Path) -> str:
    """A clickable link for WSL paths under /mnt/c (Tony views them from Windows)."""

    posix = path.resolve().as_posix()
    if posix.startswith("/mnt/") and len(posix) > 6:
        drive = posix[5].upper()
        return f"file:///{drive}:{posix[6:]}"
    return f"file://{posix}"


def _elo_payload_for_run(run_dir: Path, run_log: dict) -> dict:
    votes = arena_runner.votes_to_elo_votes(run_dir / "votes.revealed.jsonl")
    entrants = (run_log.get("config") or {}).get("model_ids") or []
    return build_elo_leaderboard(votes, entrants=entrants)


def _pairing_plan(run_dir: Path, run_log: dict, round_index: int) -> list[dict]:
    """One Swiss pairing per arena cell, mapped back to concrete candidates."""

    cells = arena_runner.build_vote_candidates(run_log)
    ratings = arena_runner.entrant_ratings(_elo_payload_for_run(run_dir, run_log))
    plan: list[dict] = []
    for (instrument_id, seed, rep), candidates in sorted(cells.items()):
        by_model: dict[str, VoteCandidate] = {}
        for candidate in candidates:
            by_model.setdefault(candidate.model_id, candidate)
        pairs = sample_swiss_pairs(
            by_model.keys(),
            ratings=ratings,
            round_index=round_index,
            seed=f"{instrument_id}:seed{seed}:rep{rep}",
        )
        for left_model, right_model in pairs:
            plan.append(
                {
                    "instrument_id": instrument_id,
                    "seed": seed,
                    "rep": rep,
                    "round": round_index,
                    "candidates": (by_model[left_model], by_model[right_model]),
                }
            )
    return plan


def _voted_pair_keys(run_dir: Path, voter_id: str) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    blind_path = run_dir / "votes.blind.jsonl"
    if not blind_path.exists():
        return keys
    for line in blind_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        keys.add((str(record.get("pair_id")), str(record.get("voter_id"))))
    return keys


@arena_app.command("run")
def arena_run(
        run_dir: str = typer.Option(..., "--run-dir", help="Run directory (use runs/code_cad_arena/<run_id>)."),
        instruments: str = typer.Option(..., help="Comma-separated instrument ids from the arena registry."),
        models: str = typer.Option(..., help="Comma-separated entrant model ids (results/-style names)."),
        registry: str = typer.Option(DEFAULT_REGISTRY, help="Arena registry JSON path."),
        seeds: str = typer.Option("0", help="Comma-separated integer seeds."),
        reps: int = typer.Option(1, help="Repetitions per (instrument, seed, model)."),
        max_attempts: int = typer.Option(2, help="Attempts per trial across resumes."),
        rate_limit_s: float = typer.Option(5.0, "--rate-limit-s", help="Seconds between calls to the same provider."),
        timeout_s: Optional[int] = typer.Option(None, help="Override per-call CLI timeout in seconds."),
        model_map: Optional[str] = typer.Option(None, "--model-map", help="JSON file mapping model_id -> {provider, model, effort}."),
        stub: bool = typer.Option(False, "--stub", help="Swap every entrant for the zero-token stub generator (smoke runs).")):
    """Run (or resume) the 4D arena matrix and write the objective scoreline."""

    if not render.openscad_available():
        console.print("[red]openscad binary not found — objective scoring needs it.[/red]")
        raise typer.Exit(code=1)

    model_ids = _split_csv(models)
    instrument_ids = _split_csv(instruments)
    seed_values = tuple(int(s) for s in _split_csv(seeds))
    mapping = _load_model_map(model_map)

    missing = providers.preflight_binaries(list(model_ids), model_map=mapping, stub=stub)
    if missing:
        console.print("[red]missing entrant CLIs:[/red] " + ", ".join(missing))
        raise typer.Exit(code=1)

    registry_payload = arena_runner.load_arena_registry(Path(registry))
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    model_providers = {}
    for model_id in model_ids:
        overrides = dict((mapping or {}).get(model_id) or {})
        model_providers[model_id] = (
            "stub" if stub
            else str(overrides.get("provider") or providers.provider_for_model_id(model_id))
        )
    generators = {
        model_id: providers.resolve_generator(
            model_id, model_map=mapping, stub=stub, timeout_s=timeout_s
        )
        for model_id in model_ids
    }

    config = OrchestrationConfig(
        instrument_ids=instrument_ids,
        model_ids=model_ids,
        seeds=seed_values,
        reps=reps,
        max_attempts=max_attempts,
        model_providers=model_providers,
        provider_rate_limits_s=(
            {provider: rate_limit_s for provider in set(model_providers.values())}
            if rate_limit_s > 0 and not stub
            else {}
        ),
    )
    execute = arena_runner.make_execute_trial(
        registry=registry_payload, run_dir=run_path, generators=generators
    )
    total = len(instrument_ids) * len(seed_values) * reps * len(model_ids)
    console.print(
        f"arena matrix: {len(instrument_ids)} instruments x {len(seed_values)} seeds "
        f"x {reps} reps x {len(model_ids)} models = {total} trials"
    )
    log = run_orchestration(
        config=config,
        run_log_path=run_path / "run_log.json",
        execute_trial=execute,
    )
    scoreline = arena_runner.collect_objective_scoreline(log)
    arena_runner.write_json(
        run_path / "objective_scoreline.json",
        {"schema": "makerbench-code-cad-objective-scoreline-v1", "rows": scoreline},
    )
    console.print(f"summary: {json.dumps(log['summary']['counts'])}")
    table = Table(title="Objective scoreline (mean pass-rate)")
    table.add_column("entrant")
    table.add_column("pass-rate", justify="right")
    table.add_column("trials", justify="right")
    for row in scoreline:
        table.add_row(row["entrant"], f"{row['objective_pass_rate']:.3f}", str(row["n_objective_trials"]))
    console.print(table)


@arena_app.command("pairs")
def arena_pairs(
        run_dir: str = typer.Option(..., "--run-dir"),
        round_index: int = typer.Option(0, "--round", help="Swiss voting round index.")):
    """Print the blind-pairing plan for one voting round (dry run)."""

    run_path = Path(run_dir)
    run_log = _load_run_log(run_path)
    plan = _pairing_plan(run_path, run_log, round_index)
    payload = [
        {
            "instrument_id": item["instrument_id"],
            "seed": item["seed"],
            "rep": item["rep"],
            "round": item["round"],
            "entrants": sorted(c.model_id for c in item["candidates"]),
        }
        for item in plan
    ]
    console.print_json(json.dumps(payload))


@arena_app.command("vote")
def arena_vote(
        run_dir: str = typer.Option(..., "--run-dir"),
        voter: str = typer.Option(..., "--voter", help="Voter id recorded on every vote."),
        round_index: int = typer.Option(0, "--round", help="Swiss voting round index."),
        max_pairs: Optional[int] = typer.Option(None, "--max-pairs", help="Stop after N pairs this session.")):
    """Interactive blind voting: open each pair page, record l/r/d votes."""

    run_path = Path(run_dir)
    run_log = _load_run_log(run_path)
    plan = _pairing_plan(run_path, run_log, round_index)
    if not plan:
        console.print("no votable pairs (need >=2 rendered candidates in a cell)")
        raise typer.Exit()

    vote_pages = run_path / "vote_pages"
    vote_pages.mkdir(parents=True, exist_ok=True)
    already = _voted_pair_keys(run_path, voter)
    asked = 0
    for item in plan:
        if max_pairs is not None and asked >= max_pairs:
            break
        cand_a, cand_b = item["candidates"]
        pair_seed = (
            f"{item['instrument_id']}:seed{item['seed']}:rep{item['rep']}:round{item['round']}"
        )
        # Rebase image paths relative to vote_pages/ so the HTML opens in a browser.
        rel_a = VoteCandidate(
            candidate_id=cand_a.candidate_id, model_id=cand_a.model_id,
            trial_id=cand_a.trial_id,
            render_path=os.path.relpath(cand_a.render_path, vote_pages),
            provenance=cand_a.provenance,
        )
        rel_b = VoteCandidate(
            candidate_id=cand_b.candidate_id, model_id=cand_b.model_id,
            trial_id=cand_b.trial_id,
            render_path=os.path.relpath(cand_b.render_path, vote_pages),
            provenance=cand_b.provenance,
        )
        pair = build_blind_pair(rel_a, rel_b, pair_seed=pair_seed)
        if (pair.pair_id, voter) in already:
            continue
        page_path = vote_pages / f"{item['instrument_id']}_seed{item['seed']}_rep{item['rep']}_round{item['round']}_{pair.pair_id}.html"
        page_path.write_text(render_vote_surface(pair), encoding="utf-8")

        console.print(
            f"\n[bold]{item['instrument_id']}[/bold] seed={item['seed']} rep={item['rep']} "
            f"round={item['round']}  ({pair.pair_id})"
        )
        console.print(f"  open: {_windows_link(page_path)}")
        choice = typer.prompt("  vote [l]eft / [r]ight / [d]raw / [s]kip / [q]uit").strip().lower()
        if choice.startswith("q"):
            break
        if choice.startswith("s"):
            continue
        winner = {"l": "left", "r": "right", "d": "draw"}.get(choice[:1])
        if winner is None:
            console.print("  unrecognized choice, skipping")
            continue
        vote = record_vote(pair, winner=winner, voter_id=voter)
        append_vote_record(run_path / "votes.blind.jsonl", vote)
        revealed = reveal_vote(pair, vote)
        revealed["instrument_id"] = item["instrument_id"]
        revealed["seed"] = item["seed"]
        revealed["rep"] = item["rep"]
        revealed["round"] = item["round"]
        append_vote_record(run_path / "votes.revealed.jsonl", revealed)
        asked += 1
    console.print(f"\nrecorded {asked} vote(s); run `arena leaderboard --run-dir {run_dir}`")


@arena_app.command("leaderboard")
def arena_leaderboard(
        run_dir: str = typer.Option(..., "--run-dir")):
    """Aggregate revealed votes into the Elo leaderboard."""

    run_path = Path(run_dir)
    run_log = _load_run_log(run_path)
    payload = _elo_payload_for_run(run_path, run_log)
    arena_runner.write_json(run_path / "elo_leaderboard.json", payload)
    table = Table(title=f"Arena Elo ({payload['votes']} votes, {payload['voters']} voter(s))")
    table.add_column("rank", justify="right")
    table.add_column("entrant")
    table.add_column("rating", justify="right")
    table.add_column("W-L-D", justify="right")
    for row in payload["leaderboard"]:
        table.add_row(
            str(row["rank"]), row["entrant"], f"{row['rating']:.1f}",
            f"{row['wins']}-{row['losses']}-{row['draws']}",
        )
    console.print(table)
    if payload["voters"] <= 1:
        console.print("[yellow]single-voter Elo is directional, not a population claim[/yellow]")


@arena_app.command("agreement")
def arena_agreement(
        run_dir: str = typer.Option(..., "--run-dir")):
    """Dual-scoreline agreement: subjective Elo vs objective pass-rate (#427)."""

    run_path = Path(run_dir)
    run_log = _load_run_log(run_path)
    elo_payload = _elo_payload_for_run(run_path, run_log)
    scoreline = arena_runner.collect_objective_scoreline(run_log)
    rows = arena_runner.build_agreement_rows(elo_payload, scoreline)
    summary = build_agreement_summary(rows)
    arena_runner.write_json(run_path / "agreement.json", summary)
    markdown = render_markdown_summary(summary)
    (run_path / "agreement.md").write_text(markdown + "\n", encoding="utf-8")
    console.print(markdown)
