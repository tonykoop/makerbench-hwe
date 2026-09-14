"""FastAPI Application for MakerBench Arena Studio (Issue #696 / #697)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from makerbench import __version__
from makerbench.cli_arena import DEFAULT_REGISTRY

from .service import ArenaStudioService


class VotePayload(BaseModel):
    pair_id: str
    winner: str  # "left", "right", "draw"
    voter: str = "tony"
    flags: Optional[dict[str, list[str]]] = None


class UndoVotePayload(BaseModel):
    pair_id: str
    voter: str = "tony"


class CompetitionLaunchPayload(BaseModel):
    run_id: Optional[str] = None
    instruments: list[str] = Field(default_factory=lambda: ["ocarina"])
    models: list[str] = Field(default_factory=lambda: ["claude-opus-5", "cadam-fable-5.1"])
    backend: str = "openscad"  # openscad, solidworks-live, fusion-live, luthier-bridge, blender
    context_tier: str = "image"  # image, repo, blind
    levels: list[str] = Field(default_factory=lambda: ["L1", "L2", "L3", "L4"])
    concurrency: int = 2
    max_turns: int = 16
    timeout_s: int = 300
    seed: int = 0
    skip_image_gate: bool = False


def create_studio_app(
    default_run_dir: Optional[Path] = None,
    registry_path: Path = Path(DEFAULT_REGISTRY),
    repo_root: Optional[Path] = None,
) -> FastAPI:
    """Create and configure the Arena Studio FastAPI instance."""

    app = FastAPI(
        title="MakerBench Arena Studio",
        version=__version__,
        description="Unified web cockpit for Code-CAD A/B Arena (Epic #421 / #694).",
    )

    service = ArenaStudioService(
        default_run_dir=default_run_dir,
        registry_path=registry_path,
        repo_root=repo_root,
    )

    # Mount static assets (model-viewer, etc.)
    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    if assets_dir.exists():
        app.mount("/static/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # Mount the Studio SPA (index.html, studio.css, studio.js). Registered after
    # /static/assets so that mount takes precedence for /static/assets/* requests.
    studio_static_dir = Path(__file__).resolve().parent / "static"
    if studio_static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(studio_static_dir)), name="studio-static")

    @app.middleware("http")
    async def require_same_origin_for_posts(request: Request, call_next):
        """Reject browser-driven state changes from any other origin."""
        if request.method == "POST":
            origin = request.headers.get("origin")
            parsed = urlsplit(origin) if origin else None
            if (
                parsed is None
                or parsed.scheme not in {"http", "https"}
                or parsed.netloc != request.url.netloc
            ):
                return PlainTextResponse("Cross-origin POST refused", status_code=403)
        return await call_next(request)

    # Run identifiers are opaque discovery keys, never filesystem paths.
    def _resolve_run_dir(run_id: str) -> Path:
        runs = service.discover_runs()
        for r in runs:
            if r["run_id"] == run_id:
                return Path(r["path"])
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    # API Routes
    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "default_run_dir": str(service.default_run_dir) if service.default_run_dir else None,
        }

    @app.get("/api/runs")
    def list_runs():
        return {"runs": service.discover_runs()}

    @app.get("/api/tasks")
    def list_tasks(family: Optional[str] = Query(None)):
        tasks = service.get_registry_tasks(family=family)
        return {"tasks": tasks, "count": len(tasks)}

    @app.get("/api/runs/{run_id}/summary")
    def get_run_summary(run_id: str):
        run_path = _resolve_run_dir(run_id)
        try:
            return service.get_run_summary(run_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/runs/{run_id}/leaderboard")
    def get_run_leaderboard(run_id: str):
        run_path = _resolve_run_dir(run_id)
        try:
            return service.get_run_leaderboard(run_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/runs/{run_id}/agreement")
    def get_run_agreement(run_id: str):
        run_path = _resolve_run_dir(run_id)
        try:
            return service.get_run_agreement(run_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/runs/{run_id}/queue")
    def get_run_queue(
        run_id: str,
        voter: str = Query("tony"),
        rounds: str = Query("0,1"),
        skip: int = Query(0, ge=0),
    ):
        run_path = _resolve_run_dir(run_id)
        round_ints = tuple(int(r.strip()) for r in rounds.split(",") if r.strip())
        queue = service.get_or_create_queue(run_path, voter=voter, rounds=round_ints)
        done, total = queue.progress()

        # C4/#703 skip ergonomics: `skip` is a read-only client-side cursor into the
        # still-unvoted items (never persisted, never mutates the queue or votes.*jsonl)
        # so a voter can look at another pair without casting a vote. Built entirely from
        # VoteQueue's existing public `items`/`voted_pair_ids` fields — no VoteQueue class
        # change needed. skip=0 is exactly next_unvoted() (unchanged default behavior).
        unvoted_items = [
            item for item in queue.items if item.pair.pair_id not in queue.voted_pair_ids
        ]
        next_item = unvoted_items[skip % len(unvoted_items)] if unvoted_items else None

        next_pair_data = None
        if next_item:
            pair = next_item.pair
            # Blind-vote anonymity (C3/#702): candidate_id is the raw trial_id, which
            # embeds the entrant/model name (see code_cad_vote_surface.py's own "No
            # candidate_id in the page markup" precedent). The frontend never reads it
            # (votes are cast by pair_id + winner side only) — do not put it on the wire
            # pre-vote. Only opaque left/right side plus already-anonymized blind asset
            # paths (staged by _stage_blind_assets under vote_pages/blind/<pair>-<side>)
            # go to the client.
            next_pair_data = {
                "pair_id": pair.pair_id,
                "meta": next_item.meta,
                "left": {
                    "render_path": pair.left.render_path,
                    "model3d_path": pair.left.model3d_path,
                    "frames": pair.left.frames,
                },
                "right": {
                    "render_path": pair.right.render_path,
                    "model3d_path": pair.right.model3d_path,
                    "frames": pair.right.frames,
                },
            }

        return {
            "run_id": run_path.name,
            "voter": voter,
            "done": done,
            "total": total,
            "has_next": next_item is not None,
            "skippable": len(unvoted_items),
            "current_pair": next_pair_data,
        }

    @app.post("/api/runs/{run_id}/vote")
    def cast_vote(run_id: str, payload: VotePayload):
        run_path = _resolve_run_dir(run_id)
        success = service.cast_vote(
            run_dir=run_path,
            pair_id=payload.pair_id,
            winner=payload.winner,
            voter=payload.voter,
            flags=payload.flags,
        )
        if not success:
            raise HTTPException(status_code=400, detail="Invalid pair ID or vote already cast")
        return {"success": True, "pair_id": payload.pair_id, "winner": payload.winner}

    @app.post("/api/runs/{run_id}/undo-vote")
    def undo_vote(run_id: str, payload: UndoVotePayload):
        run_path = _resolve_run_dir(run_id)
        success = service.undo_vote(run_dir=run_path, pair_id=payload.pair_id, voter=payload.voter)
        if not success:
            raise HTTPException(
                status_code=400, detail="Nothing to undo for that pair (never voted, or already undone)"
            )
        return {"success": True, "pair_id": payload.pair_id}

    @app.post("/api/competitions/launch")
    def launch_competition(payload: CompetitionLaunchPayload):
        try:
            return service.launch_competition(payload.model_dump())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/competitions/status")
    def get_competitions_status(run_id: Optional[str] = Query(None)):
        return service.get_competition_status(run_id)

    @app.get("/api/competitions/{run_id}/logs")
    def get_competition_logs(run_id: str, tail: int = Query(100)):
        lines = service.get_run_logs(run_id, tail=tail)
        return {"run_id": run_id, "lines": lines}

    # Serve assets for any run under /runs/{run_id}/vote_pages/...
    @app.get("/runs/{run_id}/vote_pages/{file_path:path}")
    def serve_run_asset(run_id: str, file_path: str):
        run_path = _resolve_run_dir(run_id)
        vote_pages = (run_path / "vote_pages").resolve()
        asset = (vote_pages / file_path).resolve()
        if not asset.is_relative_to(vote_pages):
            raise HTTPException(status_code=404, detail="Asset not found")
        if not asset.exists() or not asset.is_file():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(str(asset))

    # Story #697: Reference Image Gatekeeper Endpoints
    @app.get("/api/tasks/{task_id}/reference")
    def get_task_reference(task_id: str):
        return service.get_task_reference(task_id)

    @app.post("/api/tasks/{task_id}/approve")
    def approve_task_reference(task_id: str, approved: bool = Query(True)):
        return service.set_task_approval(task_id, approved)

    @app.get("/api/tasks/{task_id}/prompt-reference")
    def get_task_prompt_reference(task_id: str):
        ref = service.get_task_reference(task_id)
        return {"task_id": task_id, "prompt_cmd": ref["prompt_cmd"]}

    # Story #699: Export Winners & Reports
    @app.post("/api/runs/{run_id}/export-winners")
    def export_run_winners(run_id: str):
        run_path = _resolve_run_dir(run_id)
        return service.export_winners(run_path)

    @app.get("/api/runs/{run_id}/export-report")
    def export_run_report(run_id: str, fmt: str = Query("markdown")):
        run_path = _resolve_run_dir(run_id)
        report_text = service.export_report(run_path)
        if fmt == "markdown":
            return PlainTextResponse(report_text, media_type="text/markdown")
        return {"run_id": run_id, "report": report_text}

    # Main Dashboard Single-Page UI
    @app.get("/", response_class=HTMLResponse)
    @app.get("/app", response_class=HTMLResponse)
    def studio_home():
        index_path = studio_static_dir / "index.html"
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    return app

