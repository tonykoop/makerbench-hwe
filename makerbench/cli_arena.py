"""``makerbench arena`` — CLI wiring for the Code-CAD Arena (Epic #421).

Runs the 4D DoE matrix (instruments x seeds x reps x models), collects the
objective scoreline, drives blind voting rounds, and emits the Elo leaderboard
plus the dual-scoreline agreement report. All artifacts live under a
gitignored run directory (``runs/code_cad_arena/<run_id>/`` by convention).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import code_cad_export as arena_export
from . import code_cad_providers as providers
from . import code_cad_arena_runner as arena_runner
from . import render
from .code_cad_agreement import build_agreement_summary, render_markdown_summary
from .code_cad_arena import build_elo_leaderboard, sample_swiss_pairs
from .code_cad_orchestrator import OrchestrationConfig, run_orchestration
from .code_cad_vote_surface import (
    BlindPair,
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
    payload = build_elo_leaderboard(votes, entrants=entrants)
    return drop_unrated_entrants(payload)


def drop_unrated_entrants(payload: dict) -> dict:
    """Remove zero-vote ghost entrants from the leaderboard (#597).

    An entrant with no recorded games (e.g. a dead CLI swapped out mid-run but
    still present in the run-log config) would otherwise sit mid-table at the
    initial rating. Rated rows are re-ranked contiguously; the ghosts move to
    an ``unrated_entrants`` list so they stay visible without distorting ranks.
    """

    rated = [row for row in payload.get("leaderboard") or [] if int(row.get("games") or 0) > 0]
    unrated = [
        str(row["entrant"])
        for row in payload.get("leaderboard") or []
        if int(row.get("games") or 0) == 0
    ]
    for rank, row in enumerate(rated, start=1):
        row["rank"] = rank
    payload["leaderboard"] = rated
    payload["unrated_entrants"] = sorted(unrated)
    return payload


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


def _serve_run_dir(run_dir: Path, port: int) -> tuple[object, int]:
    """Serve the run dir on 127.0.0.1 so vote pages get real http URLs.

    Pages opened via file:// cannot load module scripts or fetch GLBs, so the
    3D viewer only works over http. Loopback-only on purpose — run artifacts
    must never be exposed to the LAN.
    """

    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    handler = partial(QuietHandler, directory=run_dir.resolve().as_posix())
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _stage_blind_assets(
    candidate: VoteCandidate, pair_hint: str, side: str, vote_pages: Path
) -> VoteCandidate:
    """Copy a candidate's viewer assets under anonymized names (blindness).

    Raw artifact paths embed entrant names (``...__claude-code-opus/...``), so
    the page must reference per-pair aliases instead. Also converts the STL to
    a colored GLB lazily, which retrofits 3D viewing onto existing runs.
    """

    import shutil

    assets = vote_pages / "blind"
    assets.mkdir(parents=True, exist_ok=True)
    png_alias = assets / f"{pair_hint}-{side}.png"
    shutil.copyfile(candidate.render_path, png_alias)

    model3d_rel = None
    stl_path = (candidate.provenance or {}).get("stl_path")
    if stl_path:
        glb = arena_export.ensure_glb(Path(str(stl_path)))
        if glb is not None:
            glb_alias = assets / f"{pair_hint}-{side}.glb"
            shutil.copyfile(glb, glb_alias)
            model3d_rel = f"blind/{glb_alias.name}"

    return VoteCandidate(
        candidate_id=candidate.candidate_id,
        model_id=candidate.model_id,
        trial_id=candidate.trial_id,
        render_path=f"blind/{png_alias.name}",
        provenance=candidate.provenance,
        model3d_path=model3d_rel,
    )


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
        max_pairs: Optional[int] = typer.Option(None, "--max-pairs", help="Stop after N pairs this session."),
        serve: bool = typer.Option(True, "--serve/--no-serve", help="Serve pages on 127.0.0.1 so the 3D viewer works (file:// blocks it)."),
        port: int = typer.Option(0, "--port", help="Local server port (0 = pick a free one).")):
    """Interactive blind voting: open each pair page, record l/r/d votes."""

    run_path = Path(run_dir)
    run_log = _load_run_log(run_path)
    plan = _pairing_plan(run_path, run_log, round_index)
    if not plan:
        console.print("no votable pairs (need >=2 rendered candidates in a cell)")
        raise typer.Exit()

    vote_pages = run_path / "vote_pages"
    vote_pages.mkdir(parents=True, exist_ok=True)
    server = None
    server_port = 0
    if serve:
        server, server_port = _serve_run_dir(run_path, port)
        console.print(f"[dim]serving {run_path} at http://127.0.0.1:{server_port}/ (loopback only)[/dim]")
    already = _voted_pair_keys(run_path, voter)
    asked = 0
    for item in plan:
        if max_pairs is not None and asked >= max_pairs:
            break
        cand_a, cand_b = item["candidates"]
        pair_seed = (
            f"{item['instrument_id']}:seed{item['seed']}:rep{item['rep']}:round{item['round']}"
        )
        shuffled = build_blind_pair(cand_a, cand_b, pair_seed=pair_seed)
        if (shuffled.pair_id, voter) in already:
            continue
        # Stage viewer assets under anonymized per-pair names: raw artifact
        # paths embed entrant ids and would unblind a voter reading the DOM.
        pair = BlindPair(
            pair_id=shuffled.pair_id,
            left=_stage_blind_assets(shuffled.left, shuffled.pair_id, "left", vote_pages),
            right=_stage_blind_assets(shuffled.right, shuffled.pair_id, "right", vote_pages),
        )
        page_path = vote_pages / f"{item['instrument_id']}_seed{item['seed']}_rep{item['rep']}_round{item['round']}_{pair.pair_id}.html"
        page_path.write_text(render_vote_surface(pair), encoding="utf-8")

        console.print(
            f"\n[bold]{item['instrument_id']}[/bold] seed={item['seed']} rep={item['rep']} "
            f"round={item['round']}  ({pair.pair_id})"
        )
        if server is not None:
            console.print(
                f"  open: http://127.0.0.1:{server_port}/vote_pages/{page_path.name}"
            )
        else:
            console.print(f"  open: {_windows_link(page_path)}  [dim](3D viewer needs --serve)[/dim]")
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
    if server is not None:
        server.shutdown()
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
    if payload.get("unrated_entrants"):
        console.print(
            "[dim]unrated (0 votes, excluded): "
            + ", ".join(payload["unrated_entrants"])
            + "[/dim]"
        )
    if payload["voters"] <= 1:
        console.print("[yellow]single-voter Elo is directional, not a population claim[/yellow]")


@arena_app.command("report")
def arena_report(
        run_dir: str = typer.Option(..., "--run-dir")):
    """Write a self-contained local HTML report (dual scoreline, gates, gallery)."""

    from .code_cad_arena_report import write_report

    out_path = write_report(Path(run_dir))
    console.print(f"report: {_windows_link(out_path)}")


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


@arena_app.command("ingest-candidate")
def arena_ingest_candidate(
        run_dir: str = typer.Option(..., "--run-dir"),
        instrument: str = typer.Option(..., "--instrument", help="Instrument id from the arena registry."),
        entrant: str = typer.Option(..., "--entrant", help="Model id for the external candidate (e.g. cadam-fable-image)."),
        scad: str = typer.Option(..., "--scad", help="Path to the candidate OpenSCAD source."),
        stl: Optional[str] = typer.Option(None, "--stl", help="Pre-exported STL (skips local compile)."),
        png: Optional[str] = typer.Option(None, "--png", help="Preview image (needed for voting when --stl is used)."),
        seed: int = typer.Option(0, "--seed"),
        rep: int = typer.Option(0, "--rep"),
        registry: str = typer.Option(DEFAULT_REGISTRY, help="Arena registry JSON path."),
        cost_usd: Optional[float] = typer.Option(None, "--cost-usd", help="Generation cost recorded in provenance."),
        source_image: Optional[str] = typer.Option(None, "--source-image", help="Inspiration image recorded in provenance.")):
    """Ingest an externally-generated candidate (CADAM, SolidWorks, ...) into a run (#616)."""

    run_path = Path(run_dir)
    if not (run_path / "run_log.json").exists():
        raise typer.BadParameter(f"no run log at {run_path / 'run_log.json'}")
    registry_payload = arena_runner.load_arena_registry(Path(registry))
    extra = {}
    if cost_usd is not None:
        extra["cost_usd"] = cost_usd
    if source_image:
        extra["source_image"] = source_image
    try:
        entry = arena_runner.ingest_candidate(
            run_log_path=run_path / "run_log.json",
            registry=registry_payload,
            instrument_id=instrument,
            model_id=entrant,
            scad_path=Path(scad),
            run_dir=run_path,
            seed=seed,
            rep=rep,
            stl_path=Path(stl) if stl else None,
            png_path=Path(png) if png else None,
            provenance_extra=extra,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    objective = (entry.get("result") or {}).get("objective") or {}
    console.print(
        f"ingested {entry['trial_id']}: status={entry['status']} "
        f"pass_rate={objective.get('objective_pass_rate')}"
    )
    if stl and not png:
        console.print("[yellow]no --png given — candidate is scored but will not enter blind voting[/yellow]")


@arena_app.command("export-winners")
def arena_export_winners(
        run_dir: str = typer.Option(..., "--run-dir"),
        instruments_root: str = typer.Option(..., "--instruments-root", help="Root of the instrument build repos (registry repo_path values are relative to it)."),
        registry: str = typer.Option(DEFAULT_REGISTRY, help="Arena registry JSON path."),
        force: bool = typer.Option(False, "--force", help="Overwrite an existing export for this run.")):
    """Export each instrument's winning model into its instrument repo (#603).

    Winner = most blind-vote wins on that instrument, tiebroken by objective
    pass-rate. Copies scad/stl/glb/png + provenance + README into
    ``<repo>/arena/<run_id>/``. Committing the export stays a human decision.
    """

    run_path = Path(run_dir)
    run_log = _load_run_log(run_path)
    registry_payload = arena_runner.load_arena_registry(Path(registry))
    summary = arena_export.export_winners(
        run_log=run_log,
        run_dir=run_path,
        registry=registry_payload,
        instruments_root=Path(instruments_root),
        force=force,
    )
    table = Table(title=f"Arena winners exported ({run_path.name})")
    table.add_column("instrument")
    table.add_column("winner")
    table.add_column("votes", justify="right")
    table.add_column("objective", justify="right")
    table.add_column("status")
    for row in summary:
        table.add_row(
            row["instrument_id"], row["entrant"], f"{row['vote_wins']:g}",
            f"{row['objective_rate']:.3f}", row["status"],
        )
    console.print(table)
    for row in summary:
        if row["status"] == "exported":
            console.print(f"  {row['instrument_id']}: {_windows_link(Path(row['dest']))}")
    console.print("[dim]exports are working-tree only — review and commit in each instrument repo yourself[/dim]")
