"""FastAPI Application for MakerBench Arena Studio (Issue #696 / #697)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from makerbench import __version__
from makerbench.cli_arena import DEFAULT_REGISTRY
from makerbench.redaction import find_host_paths, redact_host_paths, run_relative_path

from .service import ArenaStudioService


class VotePayload(BaseModel):
    pair_id: str
    winner: str  # "left", "right", "draw"
    voter: str = "tony"
    flags: Optional[dict[str, list[str]]] = None


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
    ):
        run_path = _resolve_run_dir(run_id)
        round_ints = tuple(int(r.strip()) for r in rounds.split(",") if r.strip())
        queue = service.get_or_create_queue(run_path, voter=voter, rounds=round_ints)
        done, total = queue.progress()
        next_item = queue.next_unvoted()

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

    @app.get("/", response_class=PlainTextResponse)
    def studio_home():
        # The Studio UI ships separately; the API is fully usable without it.
        return PlainTextResponse(
            "MakerBench Arena Studio API is running. The Studio UI is not installed in this build."
        )

    return app
