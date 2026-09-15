"""FastAPI Application for MakerBench Arena Studio (Issue #696 / #697)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from makerbench import __version__
from makerbench.cli_arena import DEFAULT_REGISTRY
from makerbench.redaction import find_host_paths, redact_host_paths, run_relative_path

from .routes_delta import register_delta_routes
from .service import ArenaStudioService, is_valid_task_id


class VotePayload(BaseModel):
    pair_id: str
    winner: str  # "left", "right", "draw"
    voter: str = "tony"
    flags: Optional[dict[str, list[str]]] = None


class UndoVotePayload(BaseModel):
    pair_id: str
    voter: str = "tony"


class PreflightPayload(BaseModel):
    secrets: str
    queue: str
    output_root: str
    repo_root: Optional[str] = None
    runner_script: Optional[str] = None
    instruments_root: Optional[str] = None
    lock: Optional[str] = None


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
    live: bool = False


#: Host headers accepted by default. IPv6 loopback is not listed: Starlette's
#: TrustedHostMiddleware cannot parse bracketed hosts, and the CLI binds IPv4.
LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost")

# Browser-facing URLs this app mints itself ("/runs/<id>/vote_pages/...") are not
# filesystem paths, even when a segment happens to look like one.
_APP_URL_PREFIXES = ("/runs/", "/api/", "/static/")


def _publish_value(value: Any, repo_root: Path) -> Any:
    """Strip host-absolute filesystem paths from an API payload, recursively.

    The service keeps real paths for its own use (launching, log tailing); only
    the wire form is rewritten. Paths under the repository become repo-relative,
    anything else collapses through the shared redaction patterns.
    """
    if isinstance(value, dict):
        return {key: _publish_value(item, repo_root) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_publish_value(item, repo_root) for item in value]
    if isinstance(value, str) and not value.startswith(_APP_URL_PREFIXES) and find_host_paths(value):
        return _publish_text(value, repo_root)
    return value


def _publish_text(text: str, repo_root: Path) -> str:
    candidate = Path(text)
    if candidate.is_absolute() and not any(ch.isspace() for ch in text):
        try:
            return candidate.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            return run_relative_path(text)
    root = str(repo_root)
    return redact_host_paths(text.replace(root + os.sep, "").replace(root, "."))


def create_studio_app(
    default_run_dir: Optional[Path] = None,
    registry_path: Path = Path(DEFAULT_REGISTRY),
    repo_root: Optional[Path] = None,
    allow_live: bool = False,
    extra_run_roots: Optional[Sequence[Path]] = None,
    allowed_hosts: Sequence[str] = LOOPBACK_HOSTS,
) -> FastAPI:
    """Create and configure the Arena Studio FastAPI instance."""

    service = ArenaStudioService(
        default_run_dir=default_run_dir,
        registry_path=registry_path,
        repo_root=repo_root,
        allow_live=allow_live,
        extra_run_roots=extra_run_roots,
    )

    class PublishedJSONResponse(JSONResponse):
        """JSON response that never carries a host-absolute filesystem path."""

        def render(self, content: Any) -> bytes:
            return super().render(_publish_value(content, service.repo_root))

    app = FastAPI(
        title="MakerBench Arena Studio",
        version=__version__,
        description="Unified web cockpit for Code-CAD A/B Arena (Epic #421 / #694).",
        default_response_class=PublishedJSONResponse,
    )

    # DNS-rebinding guard: a hostile page can point its own hostname at
    # 127.0.0.1, which a loopback bind does not stop and the same-origin POST
    # check cannot see (Origin and Host would both be the attacker's name).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.exception_handler(StarletteHTTPException)
    async def published_http_exception(request: Request, exc: StarletteHTTPException):
        # Error details often echo an OSError message, which names the host path.
        return PublishedJSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    # Mount static assets (model-viewer, etc.)
    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    if assets_dir.exists():
        app.mount("/static/assets", StaticFiles(directory=str(assets_dir)), name="assets")

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
        run_path = service.resolve_run_dir(run_id)
        if run_path is not None:
            return run_path
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    register_delta_routes(app, service, _resolve_run_dir)  # delta lane (#699)

    # API Routes
    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "default_run_dir": (
                run_relative_path(str(service.default_run_dir))
                if service.default_run_dir
                else None
            ),
        }

    @app.get("/api/runs")
    def list_runs():
        return {"runs": service.discover_runs()}

    @app.get("/api/tasks")
    def list_tasks(family: Optional[str] = Query(None)):
        tasks = service.get_registry_tasks(family=family)
        return {"tasks": tasks, "count": len(tasks)}

    @app.post("/api/preflight")
    def run_preflight(payload: PreflightPayload):
        try:
            return service.run_preflight(payload.model_dump())
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

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

    @app.get("/api/runs/{run_id}/judge-panel")
    def get_run_judge_panel(run_id: str, pair_id: str = Query(...), voter: str = Query("tony")):
        run_path = _resolve_run_dir(run_id)
        panel = service.get_judge_panel(run_path, pair_id, voter)
        if panel is None:
            raise HTTPException(
                status_code=404, detail="No recorded human vote for this pair by this voter yet"
            )
        return panel

    def _resolve_nightly_queue_path(queue: Optional[str]) -> Path:
        # Fixed after review (#732): an earlier version accepted `queue` (and a
        # separate `lock`) as independent, unconstrained absolute paths, making the
        # Studio server an oracle over arbitrary host JSON/lock files. Every route
        # that resolves a nightly/morning queue path (nightly queue, morning queue,
        # morning pair/vote/assets) goes through this one helper, so the containment
        # check applies uniformly rather than only to whichever route happened to
        # inline it first.
        allowed_root = (service.repo_root / "runs").resolve()
        if queue:
            queue_path = Path(queue).resolve()
            if not queue_path.is_relative_to(allowed_root):
                raise HTTPException(
                    status_code=400,
                    detail="queue path must be under the configured nightly runs root",
                )
        else:
            queue_path = allowed_root / "nightly-cad-queue.json"
        if not queue_path.exists():
            raise HTTPException(status_code=404, detail=f"nightly queue not found: {queue_path}")
        return queue_path

    @app.get("/api/nightly/queue")
    def get_nightly_queue(
        queue: Optional[str] = Query(
            None,
            description="Path to a nightly-cad-queue.json, must be under this "
            "server's configured runs/ root",
        ),
    ):
        queue_path = _resolve_nightly_queue_path(queue)
        try:
            return service.get_nightly_queue_view(queue_path, None)
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/morning/queue")
    def list_morning_bundles(queue: Optional[str] = Query(None)):
        queue_path = _resolve_nightly_queue_path(queue)
        try:
            return {"bundles": service.discover_morning_bundles(queue_path)}
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/morning/{job_id}/pair")
    def get_morning_pair(
        job_id: str,
        queue: Optional[str] = Query(None),
        voter: str = Query("tony"),
        skip: int = Query(0, ge=0),
    ):
        queue_path = _resolve_nightly_queue_path(queue)
        try:
            run_dir = service._resolve_morning_run_dir(queue_path, job_id)
            vqueue = service.get_morning_queue(run_dir, job_id, voter=voter)
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))

        done, total = vqueue.progress()
        unvoted_items = [i for i in vqueue.items if i.pair.pair_id not in vqueue.voted_pair_ids]
        next_item = unvoted_items[skip % len(unvoted_items)] if unvoted_items else None

        next_pair_data = None
        if next_item:
            pair = next_item.pair
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
            "job_id": job_id,
            "voter": voter,
            "done": done,
            "total": total,
            "has_next": next_item is not None,
            "skippable": len(unvoted_items),
            "current_pair": next_pair_data,
        }

    @app.post("/api/morning/{job_id}/vote")
    def cast_morning_vote(job_id: str, payload: VotePayload, queue: Optional[str] = Query(None)):
        queue_path = _resolve_nightly_queue_path(queue)
        try:
            run_dir = service._resolve_morning_run_dir(queue_path, job_id)
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        success = service.cast_morning_vote(
            run_dir=run_dir,
            job_id=job_id,
            pair_id=payload.pair_id,
            winner=payload.winner,
            voter=payload.voter,
            flags=payload.flags,
        )
        if not success:
            raise HTTPException(status_code=400, detail="Invalid pair ID or vote already cast")
        return {"success": True, "pair_id": payload.pair_id, "winner": payload.winner}

    # Serve blind-staged assets for a morning bundle under this run's own vote_pages/
    # (never the finalize_morning_bundle-owned morning-vote/ directory).
    @app.get("/api/morning/{job_id}/assets/{file_path:path}")
    def serve_morning_asset(job_id: str, file_path: str, queue: Optional[str] = Query(None)):
        queue_path = _resolve_nightly_queue_path(queue)
        try:
            run_dir = service._resolve_morning_run_dir(queue_path, job_id)
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        vote_pages = (run_dir / "vote_pages").resolve()
        asset = (vote_pages / file_path).resolve()
        if not asset.is_relative_to(vote_pages):
            raise HTTPException(status_code=404, detail="Asset not found")
        if not asset.exists() or not asset.is_file():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(str(asset))

    @app.get("/api/morning/{job_id}/judge-panel")
    def get_morning_judge_panel(
        job_id: str,
        pair_id: str = Query(...),
        voter: str = Query("tony"),
        queue: Optional[str] = Query(None),
    ):
        queue_path = _resolve_nightly_queue_path(queue)
        try:
            run_dir = service._resolve_morning_run_dir(queue_path, job_id)
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        panel = service.get_judge_panel(run_dir, pair_id, voter)
        if panel is None:
            raise HTTPException(
                status_code=404, detail="No recorded human vote for this pair by this voter yet"
            )
        return panel

    @app.post("/api/competitions/launch")
    def launch_competition(payload: CompetitionLaunchPayload):
        try:
            return service.launch_competition(payload.model_dump())
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/competitions/status")
    def get_competitions_status(run_id: Optional[str] = Query(None)):
        return service.get_competition_status(run_id)

    @app.get("/api/competitions/{run_id}/logs")
    def get_competition_logs(run_id: str, tail: int = Query(100)):
        lines = service.get_run_logs(run_id, tail=tail)
        return {"run_id": run_id, "lines": lines}

    @app.get("/api/competitions/{run_id}/logs/stream")
    def stream_competition_logs(
        run_id: str,
        tail: int = Query(100, ge=0, le=10_000),
        follow: bool = Query(True),
    ):
        # SSE bypasses PublishedJSONResponse, and arena logs print run paths.
        return StreamingResponse(
            (
                _publish_value(event, service.repo_root)
                for event in service.stream_run_logs(run_id, tail=tail, follow=follow)
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
    def _require_task_id(task_id: str) -> None:
        # Task ids are joined into reference-image paths and approval records, so
        # they pass their own rule even when a registry lists them (e.g. "..").
        if not is_valid_task_id(task_id):
            raise HTTPException(status_code=404, detail="Unknown task id")

    @app.get("/api/tasks/{task_id}/reference")
    def get_task_reference(task_id: str):
        _require_task_id(task_id)
        return service.get_task_reference(task_id)

    @app.post("/api/tasks/{task_id}/approve")
    def approve_task_reference(task_id: str, approved: bool = Query(True)):
        _require_task_id(task_id)
        return service.set_task_approval(task_id, approved)

    @app.get("/api/tasks/{task_id}/prompt-reference")
    def get_task_prompt_reference(task_id: str):
        _require_task_id(task_id)
        ref = service.get_task_reference(task_id)
        return {"task_id": task_id, "prompt_cmd": ref["prompt_cmd"]}

    # New, unreviewed: lets a person see the exact image they are asked to approve.
    @app.get("/api/tasks/{task_id}/reference/image")
    def get_task_reference_image(task_id: str):
        _require_task_id(task_id)
        image = service.reference_image_path(task_id)
        if image is None:
            raise HTTPException(status_code=404, detail="No reference image for this task")
        # Approval binds to the file's hash, so never show a cached older image.
        return FileResponse(str(image), headers={"Cache-Control": "no-store"})

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

    # The Studio UI: static, vendored Preact + htm ES modules, no build step and no
    # network dependency. Mounted after /static/assets so model-viewer keeps its path.
    studio_static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(studio_static_dir)), name="studio-static")

    @app.get("/", include_in_schema=False)
    def studio_home():
        return FileResponse(str(studio_static_dir / "index.html"), media_type="text/html")

    return app
